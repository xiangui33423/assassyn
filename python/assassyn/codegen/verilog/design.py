# pylint: disable=C0302
# pylint: disable=no-member
"""Verilog design generation and code dumping."""

from typing import List, Dict, Tuple
from collections import defaultdict, deque
from string import Formatter

from .utils import (
    HEADER,
    dump_type,
    dump_type_cast,
    get_sram_info,
    extract_sram_params,
    ensure_bits,
)

from ...analysis import expr_externally_used
from ...ir.module import Module, Downstream, Port,SRAM, Wire
from ...ir.module.external import ExternalSV
from ...builder import SysBuilder
from ...ir.visitor import Visitor
from ...ir.block import Block, CondBlock,CycledBlock
from ...ir.const import Const
from ...ir.array import Array
from ...ir.dtype import Int, Bits, Record,RecordValue
from ...utils import namify, unwrap_operand
from ...analysis import get_upstreams
from ...ir.expr import (
    Expr,
    BinaryOp,
    UnaryOp,
    FIFOPop,
    Log,
    ArrayRead,
    ArrayWrite,
    FIFOPush,
    PureIntrinsic,
    AsyncCall,
    Slice,
    Concat,
    Cast,
    Select,
    Bind,
    Select1Hot,
    Intrinsic,
    WireAssign,
    WireRead
)


class CIRCTDumper(Visitor):  # pylint: disable=too-many-instance-attributes,too-many-statements
    """Dumps IR to CIRCT-compatible Verilog code."""

    wait_until: bool
    indent: int
    code: List[str]
    cond_stack: List[str]
    _exposes: Dict[Expr, List[Tuple[Expr, str]]]
    logs: List[str]
    connections: List[Tuple[Module, str, str]]
    current_module: Module
    sys: SysBuilder
    async_callees: Dict[Module, List[Module]]
    downstream_dependencies: Dict[Module, List[Module]]
    is_top_generation: bool
    finish_body:list[str]
    sram_payload_arrays:set
    memory_defs:set

    def __init__(self):
        super().__init__()
        self.wait_until = None
        self.indent = 0
        self.code = []
        self._exposes = {}
        self.cond_stack = []
        self.logs = []
        self.connections = []
        self.current_module = None
        self.sys = None
        self.async_callees = {}
        self.exposed_ports_to_add = []
        self.downstream_dependencies = {}
        self.is_top_generation = False
        self.array_users = {}
        self.finish_body = []
        self.finish_conditions = []
        self.array_write_port_mapping = {}
        self.sram_payload_arrays = set()
        self.memory_defs = set()
        self.expr_to_name = {}
        self.name_counters = defaultdict(int)
        # Track external module usage for downstream modules
        self.external_wire_assignments = []
        self.pending_external_inputs = defaultdict(list)
        self.instantiated_external_modules = set()
        self.external_modules = []

    def get_pred(self) -> str:
        """Get the current predicate for conditional execution."""
        if not self.cond_stack:
            return "Bits(1)(1)"
        pred_parts = []
        for s, _ in self.cond_stack:
            s_bits = ensure_bits(s)
            pred_parts.append(s_bits)
        return " & ".join(pred_parts)

    def get_external_port_name(self, node: Expr) -> str:
        """Get the mangled port name for an external value."""
        producer_module = node.parent.module
        producer_name = namify(producer_module.name)
        base_port_name = namify(node.as_operand())
        if base_port_name.startswith("_"):
            base_port_name = f"port{base_port_name}"
        port_name = f"{producer_name}_{base_port_name}"
        return port_name

    # pylint: disable=protected-access
    @staticmethod
    def _is_external_module(module: Module) -> bool:
        """Return True if the module represents an external implementation."""

        if isinstance(module, ExternalSV):
            return True

        attrs = getattr(module, '_attrs', None)
        return attrs is not None and Module.ATTR_EXTERNAL in attrs


    # pylint: disable=too-many-return-statements,too-many-branches
    def dump_rval(self,node, with_namespace: bool,module_name:str=None) -> str:
        """Dump a reference to a node with options."""

        node = unwrap_operand(node)
        if (
            isinstance(node, Expr)
            and self.current_module is not None
            and hasattr(self.current_module, 'externals')
            and node in self.current_module.externals
            and not self.is_top_generation
        ):
            return f"self.{self.get_external_port_name(node)}"
        if isinstance(node, Module):
            return namify(node.name)
        if isinstance(node, Array):
            array = node
            return namify(array.name)
        if isinstance(node, Port):
            return namify(node.name)
        if isinstance(node, FIFOPop):
            if not with_namespace:
                return f'self.{namify(node.fifo.name)}'
            return namify(node.fifo.module.name) + "_" + namify(node.fifo.name)
        if isinstance(node, Const):
            int_imm = node
            value = int_imm.value
            ty = dump_type(int_imm.dtype)
            return f"{ty}({value})"
        if isinstance(node, str):
            value = node
            return f'"{value}"'
        if isinstance(node, Expr):
            if node not in self.expr_to_name:
                base_name = namify(node.as_operand())
                # Handle anonymous expressions which namify to '_' or an empty string.
                if not base_name or base_name == '_':
                    base_name = 'tmp'

                count = self.name_counters[base_name]
                unique_name = f"{base_name}_{count}" if count > 0 else base_name
                self.name_counters[base_name] += 1
                self.expr_to_name[node] = unique_name

            unique_name = self.expr_to_name[node]

            if with_namespace:
                owner_module_name = namify(node.parent.module.name)
                if owner_module_name is None:
                    owner_module_name = module_name
                return f"{owner_module_name}_{unique_name}"
            return unique_name

        if isinstance(node, RecordValue):
            return self.dump_rval(node.value(), with_namespace, module_name)
        if isinstance(node, Wire):
            # For wires, we use their name directly
            return namify(node.name)

        raise ValueError(f"Unknown node of kind {type(node).__name__}")

    def append_code(self, code: str):
        """Append code with proper indentation."""
        if code.strip() == '':
            self.code.append('')
        else:
            self.code.append(self.indent * ' ' + code)

    def expose(self, kind: str, expr: Expr):
        ''' Expose an expression out of the module.'''
        key = None
        if kind == 'expr':
            key = expr

        elif kind == 'array':
            assert isinstance(expr, (ArrayRead, ArrayWrite))
            key = expr.array
        elif kind == 'fifo':
            assert isinstance(expr, FIFOPush)
            key = expr.fifo
        elif kind == 'fifo_pop':
            assert isinstance(expr, FIFOPop)
            key = expr.fifo
        elif kind == 'trigger':
            assert isinstance(expr, AsyncCall)
            key = expr.bind.callee

        assert key is not None
        if key not in self._exposes:
            self._exposes[key] = []
        self._exposes[key].append((expr, self.get_pred()))

    def visit_block(self, node: Block):
        is_cond = isinstance(node, CondBlock)
        is_cycle = isinstance(node, CycledBlock)

        if is_cond:
            cond_str = self.dump_rval(node.cond, False)
            self.cond_stack.append((f"({cond_str})", node))
            def has_side_effect(block: Block) -> bool:
                if block.body is None:
                    return False
                for item in block.body:
                    if isinstance(item, Log):
                        return True
                    if isinstance(item, Block) and has_side_effect(item):
                        return True
                return False

            if has_side_effect(node):
                self.expose('expr', node.cond)

        elif is_cycle:
            self.cond_stack.append((f"(self.cycle_count == {node.cycle})", node))

        if node is not None and node.body is not None:
            for i in node.body:
                if isinstance(i, Expr):
                    self.visit_expr(i)
                elif isinstance(i, Block):
                    self.visit_block(i)
                elif isinstance(i, RecordValue):
                    pass
                else:
                    print(i)
                    raise ValueError(f'Unknown node type: {type(i)}')

        if is_cond or is_cycle:
            self.cond_stack.pop()


    # pylint: disable=arguments-renamed,too-many-locals,too-many-branches,too-many-statements
    def visit_expr(self, expr: Expr):
        self.append_code(f'# {expr}')
        body = None
        rval = self.dump_rval(expr, False)

        if isinstance(expr, BinaryOp):
            binop = expr.opcode
            dtype = expr.dtype

            lhs_type = expr.lhs.dtype
            rhs_type = expr.rhs.dtype

            a = self.dump_rval(expr.lhs, False)
            b = self.dump_rval(expr.rhs, False)


            if binop in [BinaryOp.SHL, BinaryOp.SHR] or 'SHR' in str(binop):

                if lhs_type.bits != rhs_type.bits:
                    b = \
                    f"BitsSignal.concat([Bits({lhs_type.bits - rhs_type.bits})(0), {b}.as_bits()])"

                b = f"{b}.as_bits()"
                a = f"{a}.as_bits()"

                op_class_name = None
                if binop == BinaryOp.SHL:
                    op_class_name = "comb.ShlOp"
                elif binop == BinaryOp.SHR:
                    if expr.lhs.dtype.is_signed():
                        op_class_name = "comb.ShrSOp"
                    else:
                        op_class_name = "comb.ShrUOp"

                if op_class_name is None:
                    raise TypeError(f"Unhandled shift operation: {binop}")
                body = (
                    f"{rval} = {op_class_name}({a}.as_bits(), {b}.as_bits())"
                    f".as_bits({dtype.bits})[0:{dtype.bits}]"
                    f".{dump_type_cast(dtype)}"
                )
            elif binop == BinaryOp.MOD:
                if expr.dtype.is_signed():
                    op_class_name = "comb.ModSOp"
                else:
                    op_class_name = "comb.ModUOp"
                body = (
                    f"{rval} = {op_class_name}({a}.as_bits(), {b}.as_bits())"
                    f".as_bits({dtype.bits})[0:{dtype.bits}]"
                    f".{dump_type_cast(dtype)}"
                )
            elif expr.is_comparative():
                # Convert to uint for comparison
                if not expr.lhs.dtype.is_int():
                    a= f"{a}.as_uint()"
                if not expr.rhs.dtype.is_int():
                    b = f"{b}.as_uint()"
                op_str = BinaryOp.OPERATORS[expr.opcode]
                op_body = f"(({a} {op_str} {b}).{dump_type_cast(dtype)})"
                body = f'{rval} = {op_body}'
            else:
                op_str = BinaryOp.OPERATORS[expr.opcode]
                if expr.lhs.dtype != expr.rhs.dtype:
                    b=f"{b}.{dump_type_cast(expr.lhs.dtype )}"
                if op_str == "&":
                    if expr.rhs.dtype != Bits:
                        b=f"{b}.as_bits()"
                op_body = f"(({a} {op_str} {b}).{dump_type_cast(dtype)})"
                body = f'{rval} = {op_body}'

        elif isinstance(expr, UnaryOp):
            uop = expr.opcode
            target_cast_str = dump_type_cast(expr.dtype)
            op_str = "~" if uop == UnaryOp.FLIP else "-"
            x = self.dump_rval(expr.x, False)
            if uop == UnaryOp.FLIP:
                x = f"({x}.as_bits())"
            body = f"{op_str}{x}"
            body = f'{rval} = ({body}).{target_cast_str}'

        elif isinstance(expr, Log):
            formatter_str = expr.operands[0].value

            arg_print_snippets = []
            condition_snippets = []
            module_name = namify(self.current_module.name)

            for i in expr.operands[1:]:
                operand = unwrap_operand(i)
                if not isinstance(operand, Const):
                    self.expose('expr', operand)
                    exposed_name = self.dump_rval(operand, True)
                    valid_signal = f'dut.{module_name}.valid_{exposed_name}.value'
                    condition_snippets.append(valid_signal)

                    base_value = f"dut.{module_name}.expose_{exposed_name}.value"
                    if isinstance(operand.dtype, Int):
                        bits = operand.dtype.bits
                        expose_signal = (
                            f"({base_value} - (1 << {bits}) "
                            f"if ({base_value} >> ({bits} - 1)) & 1 else int({base_value}))"
                        )
                    else:
                        expose_signal = f"int({base_value})"
                    arg_print_snippets.append(expose_signal)

                else:
                    arg_print_snippets.append(str(operand.value))
            f_string_content_parts = []
            arg_iterator = iter(arg_print_snippets)

            for literal_text, field_name, format_spec, conversion \
                in Formatter().parse(formatter_str):

                if literal_text:
                    f_string_content_parts.append(literal_text)

                if field_name is not None:
                    if format_spec == '?':
                        conversion = 'r'
                        format_spec = None
                    arg_code = next(arg_iterator)
                    new_placeholder =  f"{{{arg_code}"
                    if conversion:  # for !s, !r, !a
                        new_placeholder += f"!{conversion}"
                    if format_spec:  # for :b, :08x,
                        new_placeholder += f":{format_spec}"
                    new_placeholder += "}"
                    f_string_content_parts.append(new_placeholder)

            f_string_content = "".join(f_string_content_parts)

            block_condition = self.get_pred()
            block_condition=block_condition.replace('cycle_count','dut.global_cycle_count')
            final_conditions = []

            for cond_str, cond_obj in self.cond_stack:
                if isinstance(cond_obj, CycledBlock):
                    tb_cond_path = \
                    cond_str.replace("self.cycle_count", "dut.global_cycle_count.value")
                    final_conditions.append(tb_cond_path)

                elif isinstance(cond_obj, CondBlock):
                    exposed_name = self.dump_rval(cond_obj.cond, True)

                    tb_expose_path = f"(dut.{module_name}.expose_{exposed_name}.value)"
                    tb_valid_path = f"(dut.{module_name}.valid_{exposed_name}.value)"

                    combined_cond = f"({tb_valid_path} & {tb_expose_path})"
                    final_conditions.append(combined_cond)

            if condition_snippets:
                final_conditions.append(" and ".join(condition_snippets))

            if_condition = " and ".join(final_conditions)

            self.logs.append(f'# {expr}')

            line_info = f"@line:{expr.loc.rsplit(':', 1)[-1]}"

            module_info = f"[{namify(self.current_module.name)}]"

            # pylint: disable-next=W1309
            cycle_info = f"Cycle @{{float(dut.global_cycle_count.value):.2f}}:"

            final_print_string = (
                 f'f"{line_info} {cycle_info} {module_info:<20} {f_string_content}"'
             )

            self.logs.append(f'#@ line {expr.loc}: {expr}')
            if if_condition:
                self.logs.append(f'if ( {if_condition} ):')
                self.logs.append(f'    print({final_print_string})')
            else:
                self.logs.append(f'print({final_print_string})')

        elif isinstance(expr, ArrayRead):
            array_ref = expr.array
            is_sram_payload = False
            if isinstance(self.current_module, SRAM):
                if array_ref == self.current_module.payload:
                    is_sram_payload = True
            if is_sram_payload:
                rval = self.dump_rval(expr, False)
                body = f'{rval} = self.mem_dataout'
                self.expose('array', expr)
            else:
                array_idx = unwrap_operand(expr.idx)
                array_idx = (self.dump_rval(array_idx, False)
                            if not isinstance(array_idx, Const) else array_idx.value)
                index_bits = array_ref.index_bits if array_ref.index_bits > 0 else 1
                if dump_type(expr.idx.dtype)!=Bits and not isinstance(array_idx, int):
                    array_idx = f"{array_idx}.as_bits({index_bits})"

                array_name = self.dump_rval(array_ref, False)
                if isinstance(expr.dtype, Record):
                    body = f'{rval} = self.{array_name}_q_in[{array_idx}]'
                else:
                    body = \
                    f'{rval} = self.{array_name}_q_in[{array_idx}].{dump_type_cast(expr.dtype)}'
                self.expose('array', expr)
        elif isinstance(expr, ArrayWrite):
            self.expose('array', expr)
        elif isinstance(expr, FIFOPush):
            self.expose('fifo', expr)
        elif isinstance(expr,FIFOPop):
            rval = namify(expr.as_operand())
            fifo_name = self.dump_rval( expr.fifo, False)
            body = f'{rval} = self.{fifo_name}'
            self.expose('fifo_pop', expr)

        elif isinstance(expr, PureIntrinsic):
            intrinsic = expr.opcode
            if intrinsic in [PureIntrinsic.FIFO_VALID, PureIntrinsic.FIFO_PEEK]:
                fifo = expr.args[0]
                fifo_name = self.dump_rval(fifo, False)
                if intrinsic == PureIntrinsic.FIFO_PEEK:
                    body = f'{rval} = self.{fifo_name}'
                    self.expose('expr', expr)
                elif intrinsic == PureIntrinsic.FIFO_VALID:
                    body = f'{rval} = self.{fifo_name}_valid'
            elif intrinsic == PureIntrinsic.VALUE_VALID:
                value_expr = expr.operands[0].value
                if value_expr.parent.module != expr.parent.module:
                    port_name = self.get_external_port_name(value_expr)
                    body = f"{rval} = self.{port_name}_valid"
                else:
                    body = f"{rval} = self.executed"
            else:
                raise ValueError(f"Unknown intrinsic: {expr}")
        elif isinstance(expr, AsyncCall):
            self.expose('trigger', expr)
        elif isinstance(expr, Slice):
            a = self.dump_rval(expr.x, False)
            l = expr.l.value.value
            r = expr.r.value.value
            body = f"{rval} = {a}.as_bits()[{l}:{r+1}]"
        elif isinstance(expr, Concat):
            a = self.dump_rval(expr.msb, False)
            b = self.dump_rval(expr.lsb, False)
            body = f"{rval} = BitsSignal.concat([{a}.as_bits(), {b}.as_bits()])"


        elif isinstance(expr, Cast):
            dbits = expr.dtype.bits
            a = self.dump_rval(expr.x, False)
            src_dtype = expr.x.dtype
            pad = dbits - src_dtype.bits
            cast_body = ""
            cast_kind =  expr.opcode
            if cast_kind == Cast.BITCAST:
                # assert pad == 0
                cast_body = f"{a}.{dump_type_cast(expr.dtype,dbits)}"
            elif cast_kind == Cast.ZEXT:
                cast_body = (
                    f" BitsSignal.concat( [Bits({pad})(0) , {a}.as_bits()])"
                    f".{dump_type_cast(expr.dtype)} "
                )
            elif cast_kind == Cast.SEXT:
                cast_body =  (
                    f"BitsSignal.concat( [BitsSignal.concat([ {a}.as_bits()[{src_dtype.bits-1}] ]"
                    f" * {pad}) , {a}.as_bits()]).{dump_type_cast(expr.dtype)}"
                )
            body = f"{rval} = {cast_body}"
        elif isinstance(expr, Select):
            cond = self.dump_rval(expr.cond, False)
            true_value = self.dump_rval(expr.true_value, False)

            false_value = self.dump_rval(expr.false_value, False)
            if expr.true_value.dtype != expr.false_value.dtype:
                false_value =  f"{false_value}.{dump_type_cast(expr.true_value)}"
            body = f'{rval} = Mux({cond}, {false_value}, {true_value})'
        elif isinstance(expr, Bind):
            body = None
        elif isinstance(expr, Select1Hot):
            rval = self.dump_rval(expr, False)
            cond = self.dump_rval(expr.cond, False)
            values = [self.dump_rval(v, False) for v in expr.values]

            if len(values) == 1:
                body = f"{rval} = {values[0]}"
            else:
                num_values = len(values)
                selector_bits = max((num_values - 1).bit_length(), 1)
                if num_values == 2:
                    body = f"{cond}.as_bits()[1]"
                else:
                    self.append_code(f"{cond}_res = Bits({selector_bits})(0)")
                    for i in range(num_values):
                        self.append_code(
                            f"{cond}_res = Mux({cond}[{i}] ,"
                            f" {cond}_res , Bits({selector_bits})({i}))")

                    values_str = ", ".join(values)
                    mux_code = f"{rval} = Mux({cond}_res, {values_str})"
                    self.append_code(mux_code)
                    body = None

        elif isinstance(expr, Intrinsic):
            intrinsic = expr.opcode
            if intrinsic == Intrinsic.FINISH:
                predicate_signal = self.get_pred()
                self.finish_conditions.append((predicate_signal, "executed_wire"))
                body = None
            elif intrinsic == Intrinsic.ASSERT:
                self.expose('expr', expr.args[0])
            elif intrinsic == Intrinsic.WAIT_UNTIL:
                cond = self.dump_rval(expr.args[0], False)
                final_cond = cond
                self.wait_until = final_cond
            elif intrinsic == Intrinsic.BARRIER:
                body = None
            # elif intrinsic == Intrinsic.MEM_WRITE:
            # # Create a temporary ArrayWrite to reuse existing logic
            #     array = unwrap_operand(expr.operands[0])
            #     idx = unwrap_operand(expr.operands[1])
            #     val = unwrap_operand(expr.operands[2])
            #     temp_write = ArrayWrite(array, idx, val)
            #     temp_write.parent = expr.parent
            #     self.expose('array', temp_write)
            #     body = None

            # elif intrinsic == Intrinsic.MEM_READ:
            #     # Create a temporary ArrayRead to reuse existing logic
            #     array = unwrap_operand(expr.operands[0])
            #     idx = unwrap_operand(expr.operands[1])

            #     temp_read = ArrayRead(array, idx)
            #     temp_read.parent = expr.parent
            #     temp_read.scalar_ty = array.scalar_ty
            #     self.expose('array', temp_read)
            #     body = None

            else:
                raise ValueError(f"Unknown block intrinsic: {expr}")
        elif isinstance(expr, WireAssign):
            # Annotate external wire assigns so they show up in the generated script
            body = f"# External wire assign: {expr}"
            if isinstance(self.current_module, Downstream):
                wire = expr.wire
                value = expr.value
                owner = getattr(wire, 'parent', None) or getattr(wire, 'module', None)
                wire_name = getattr(wire, 'name', None)
                if isinstance(owner, ExternalSV) and wire_name:
                    self.pending_external_inputs[owner].append((wire_name, value))
        elif isinstance(expr, WireRead):
            # Document reads from external module outputs and emit the assignment
            self.append_code(f'# External wire read: {expr}')
            wire = expr.wire
            owner = getattr(wire, 'parent', None) or getattr(wire, 'module', None)
            wire_name = getattr(wire, 'name', None)

            if (
                isinstance(owner, ExternalSV)
                and owner not in self.instantiated_external_modules
            ):
                ext_module_name = namify(owner.name)
                inst_name = f"{ext_module_name.lower()}_inst"
                self.append_code('# instantiate external module')
                connections = []
                if getattr(owner, 'has_clock', False):
                    connections.append('clk=self.clk')
                if getattr(owner, 'has_reset', False):
                    connections.append('rst=self.rst')
                for input_name, input_val in self.pending_external_inputs.get(owner, []):
                    connections.append(f"{input_name}={self.dump_rval(input_val, False)}")
                if connections:
                    self.append_code(f'{inst_name} = {ext_module_name}({", ".join(connections)})')
                else:
                    self.append_code(f'{inst_name} = {ext_module_name}()')
                self.instantiated_external_modules.add(owner)
                self.pending_external_inputs.pop(owner, None)

            if owner is not None and wire_name is not None:
                inst_name = f"{namify(owner.name).lower()}_inst"
                body = f"{rval} = {inst_name}.{wire_name}"
            else:
                body = f"# TODO: unresolved external wire read for {expr}"
        else:
            raise ValueError(f"Unhandled expression type: {type(expr).__name__}")

        if expr.is_valued() and not isinstance(expr, WireRead) \
                and expr_externally_used(expr, True):
            if not isinstance(unwrap_operand(expr), Const):
                self.expose('expr', expr)

        if body is not None:
            self.append_code(body)

    # pylint: disable=too-many-locals,too-many-branches
    def _generate_sram_control_signals(self, sram_info):
        """Generate control signals for SRAM memory interface."""
        array = sram_info['array']

        array_writes = []
        array_reads = []
        write_addr = None
        write_data = None
        read_addr = None

        for key, exposes in self._exposes.items():
            if isinstance(key, Array) and key == array:
                for expr, pred in exposes:
                    if isinstance(expr, ArrayWrite):
                        array_writes.append((expr, pred))
                    elif isinstance(expr, ArrayRead):
                        array_reads.append((expr, pred))

        if array_writes:
            write_expr, write_pred = array_writes[0]
            write_addr = self.dump_rval(write_expr.idx, False)
            write_enable = f'executed_wire & ({write_pred})'
            write_data = self.dump_rval(write_expr.val, False)
        else:
            write_enable = 'Bits(1)(0)'
            write_addr = None
            write_data = dump_type(array.scalar_ty)(0)
        read_addr = None
        if array_reads:
            read_expr, _ = array_reads[0]
            read_addr = self.dump_rval(read_expr.idx, False)

        self.append_code(f'self.mem_write_enable = {write_enable}')

        # Address selection (prioritize write address when writing)
        if write_addr and read_addr:
            if write_addr != read_addr:
                self.append_code(f'self.mem_address = Mux({write_enable},'
                    f' {read_addr}.as_bits(), {write_addr}.as_bits())')
            else:
                self.append_code(f'self.mem_address = {write_addr}.as_bits()')
        elif write_addr:
            self.append_code(f'self.mem_address = {write_addr}.as_bits()')
        elif read_addr:
            self.append_code(f'self.mem_address = {read_addr}.as_bits()')
        else:
            self.append_code(f'self.mem_address = Bits({array.index_bits})(0)')


        self.append_code(f'self.mem_write_data = {write_data}')

        self.append_code('self.mem_read_enable = Bits(1)(1)')  # Always enable reads

    # pylint: disable=too-many-locals,too-many-branches,too-many-statements,too-many-nested-blocks
    def cleanup_post_generation(self):
        """genearting signals for connecting modules"""
        self.append_code('')

        exec_conditions = []
        if isinstance(self.current_module, Downstream):
            node = self.current_module
            if self.current_module in self.downstream_dependencies:
                dep_signals = [f'self.{namify(dep.name)}_executed' \
                    for dep in self.downstream_dependencies[node]]
                if dep_signals:
                    self.append_code(f"executed_wire = ({' | '.join(dep_signals)})")
                else:
                    self.append_code('executed_wire = Bits(1)(0)')
            else:
                self.append_code('executed_wire = Bits(1)(0)')
        else:
            exec_conditions.append("self.trigger_counter_pop_valid")
            if self.wait_until:
                exec_conditions.append(f"({self.wait_until})")

            if not exec_conditions:
                self.append_code('executed_wire = Bits(1)(1)')
            else:
                self.append_code(f"executed_wire = {' & '.join(exec_conditions)}")

        if self.finish_conditions:
            finish_terms = []
            for pred, exec_signal in self.finish_conditions:
                finish_terms.append(f"({pred} & {exec_signal})")

            if len(finish_terms) == 1:
                self.append_code(f'self.finish = {finish_terms[0]}')
            else:
                self.append_code(f'self.finish = {" | ".join(finish_terms)}')
        else:
            self.append_code('self.finish = Bits(1)(0)')

        if isinstance(self.current_module, SRAM):
            sram_info = get_sram_info(self.current_module)
            if sram_info:
                self._generate_sram_control_signals(sram_info)
        # pylint: disable=too-many-nested-blocks
        for key, exposes in self._exposes.items():
            if isinstance(key, Array):
                if key in self.sram_payload_arrays:
                    continue
                array_writes = [
                        (e, p) for e, p in exposes
                        if isinstance(e, ArrayWrite)
                    ]
                arr = key
                array_name = self.dump_rval(arr, False)
                array_dtype = arr.scalar_ty
                port_mapping = self.array_write_port_mapping.get(arr, {})
                # Group writes by their source module
                writes_by_module = {}
                for expr, pred in array_writes:
                    module = expr.module
                    if module not in writes_by_module:
                        writes_by_module[module] = []
                    writes_by_module[module].append((expr, pred))
                # Generate signals for each port
                for module, module_writes in writes_by_module.items():
                    port_idx = port_mapping[module]
                    port_suffix = f"_port{port_idx}"
                    # Write enable
                    ce_terms = [p for _, p in module_writes]
                    self.append_code(
                        f'self.{array_name}_w{port_suffix} = '
                        f'executed_wire & ({" | ".join(ce_terms)})'
                    )
                    # Write data (mux if multiple writes from same module)
                    if len(module_writes) == 1:
                        wdata = self.dump_rval(module_writes[0][0].val, False)
                        if module_writes[0][0].val.dtype != dump_type(array_dtype):
                            wdata = f"{wdata}.{dump_type_cast(array_dtype)}"
                    else:
                        # Build mux chain
                        wdata = self._build_mux_chain(module_writes, array_dtype)
                    self.append_code(f'self.{array_name}_wdata{port_suffix} = {wdata}')
                    if len(module_writes) == 1:
                        # Single write - no mux needed, just use the index directly
                        widx_mux = self.dump_rval(module_writes[0][0].idx, False)
                    else:
                        # Multiple writes - build mux chain
                        widx_mux = (
                            f"Mux({module_writes[0][1]},"
                            f" {dump_type(module_writes[0][0].idx.dtype)}(0),"
                            f" {self.dump_rval(module_writes[0][0].idx, False)})"
                        )
                        for expr, pred in module_writes[1:]:
                            widx_mux = f"Mux({pred},  {widx_mux},{self.dump_rval(expr.idx, False)})"
                    self.append_code(
                        f'self.{array_name}_widx{port_suffix} = {widx_mux}.as_bits()'
                        )

            elif isinstance(key, Port):
                has_push = any(isinstance(e, FIFOPush) for e, p in exposes)
                has_pop = any(isinstance(e, FIFOPop) for e, p in exposes)

                if has_push:
                    fifo = self.dump_rval(key, False)
                    pushes = [(e, p) for e, p in exposes if isinstance(e, FIFOPush)]
                    final_push_predicate = " | ".join([f"({p})" for _, p in pushes]) \
                    if pushes else "Bits(1)(0)"

                    if len(pushes) == 1:
                        final_push_data = self.dump_rval(pushes[0][0].val, False)
                    else:
                        mux_data = f"{dump_type(key.dtype)}(0)"
                        for expr, pred in pushes:
                            rval = self.dump_rval(expr.val, False)
                            mux_data = f"Mux({pred}, {mux_data}, {rval})"
                        final_push_data = mux_data

                    self.append_code(f'# Push logic for port: {fifo}')
                    ready_signal = f"self.fifo_{namify(key.module.name)}_{fifo}_push_ready"

                    fifo_prefix = f"self.{namify(key.module.name)}_{fifo}"

                    self.append_code(
                        f"{fifo_prefix}_push_valid = executed_wire & "
                        f"({final_push_predicate}) & {ready_signal}"
                    )
                    self.append_code(f"{fifo_prefix}_push_data = {final_push_data}")

                if has_pop:
                    fifo = self.dump_rval(key, False)

                    pop_expr = [e for e, p in exposes if isinstance(e, FIFOPop)][0]
                    self.append_code(f'# {pop_expr}')
                    pop_predicates = [pred for expr, pred in exposes if isinstance(expr, FIFOPop)]

                    if pop_predicates:
                        final_pop_condition = " | ".join([f"({p})" for p in pop_predicates])
                    else:
                        final_pop_condition = "Bits(1)(0)"
                    self.append_code(
                        f"self.{fifo}_pop_ready = executed_wire & ({final_pop_condition})"
                    )

            elif isinstance(key, Module):
                rval = self.dump_rval(key, False)

                call_predicates = [pred for expr, pred in exposes if isinstance(expr, AsyncCall)]

                if not call_predicates:
                    self.append_code(f'self.{rval}_trigger = UInt(8)(0)')
                    continue

                self.append_code(f'# Summing triggers for {rval}')

                add_terms = [f"Mux({pred}, UInt(8)(0), UInt(8)(1))" for pred in call_predicates]

                if len(add_terms) == 1:
                    sum_expression = add_terms[0]
                else:
                    sum_expression = f"({' + '.join(add_terms)})"

                resized_sum = f"(({sum_expression}).as_bits()[0:8].as_uint())"
                final_trigger_value = f"Mux(executed_wire, UInt(8)(0), {resized_sum})"
                self.append_code(f'self.{rval}_trigger = {final_trigger_value}')

            else:
                expr, pred = exposes[0]
                if isinstance(unwrap_operand(expr), Const):
                    continue
                rval = self.dump_rval(expr, False)
                exposed_name = self.dump_rval(expr, True)
                if not isinstance(key,ArrayWrite ):
                    dtype_str = dump_type(expr.dtype)
                else :
                    dtype_str = dump_type(expr.x.dtype)

                if isinstance(expr, Slice):
                    # For slice expressions, calculate actual width
                    l = expr.l.value.value if hasattr(expr.l, 'value') else expr.l
                    r = expr.r.value.value if hasattr(expr.r, 'value') else expr.r
                    actual_bits = r - l + 1
                    dtype_str = f"Bits({actual_bits})"


                # Special handling for Wire objects - they don't need exposed ports
                if isinstance(key, Wire):
                    continue
                # Add port declaration strings to our list
                self.exposed_ports_to_add.append(f'expose_{exposed_name} = Output({dtype_str})')
                self.exposed_ports_to_add.append(f'valid_{exposed_name} = Output(Bits(1))')

                # Generate the logic assignment
                self.append_code(f'# Expose: {expr}')
                self.append_code(f'self.expose_{exposed_name} = {rval}')
                self.append_code(f'self.valid_{exposed_name} = executed_wire')

        self.append_code('self.executed = executed_wire')


    # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    def visit_module(self, node: Module):
        # STAGE 1: ANALYSIS & BODY GENERATION
        original_code_buffer = self.code
        original_indent = self.indent
        self.code = []
        self.indent = original_indent + 8

        self.wait_until = None
        self._exposes = {}
        self.cond_stack = []
        self.current_module = node
        self.exposed_ports_to_add = []
        self.finish_body = []
        self.finish_conditions = []
        self.external_wire_assignments = []
        self.pending_external_inputs.clear()
        self.instantiated_external_modules.clear()

        # For downstream modules, we still need to process the body
        if node.body is not None:
            self.visit_block(node.body)
        self.cleanup_post_generation()

        construct_method_body = self.code

        self.code = original_code_buffer
        self.indent = original_indent

        self.current_module = node

        is_downstream = isinstance(node, Downstream)
        is_sram = isinstance(node, SRAM)
        is_driver = node not in self.async_callees

        self.append_code(f'class {namify(node.name)}(Module):')
        self.indent += 4

        self.append_code('clk = Clock()')
        self.append_code('rst = Reset()')
        self.append_code('executed = Output(Bits(1))')
        self.append_code('cycle_count = Input(UInt(64))')
        self.append_code('finish = Output(Bits(1))')

        if is_downstream:
            if node in self.downstream_dependencies:
                for dep_mod in self.downstream_dependencies[node]:
                    self.append_code(f'{namify(dep_mod.name)}_executed = Input(Bits(1))')
            for ext_val in node.externals:
                if isinstance(ext_val,Bind) or isinstance(unwrap_operand(ext_val), Const):
                    continue
                port_name = self.get_external_port_name(ext_val)
                port_type = dump_type(ext_val.dtype)
                self.append_code(f'{port_name} = Input({port_type})')
                self.append_code(f'{port_name}_valid = Input(Bits(1))')
            if is_sram:
                sram_info = get_sram_info(node)
                if sram_info:
                    sram_array = sram_info['array']
                    self.append_code(f'mem_dataout = Input({dump_type(sram_array.scalar_ty)})')
                    index_bits = sram_array.index_bits if sram_array.index_bits > 0 else 1
                    self.append_code(f'mem_address = Output(Bits({index_bits}))')
                    self.append_code(f'mem_write_data = Output({dump_type(sram_array.scalar_ty)})')
                    self.append_code('mem_write_enable = Output(Bits(1))')
                    self.append_code('mem_read_enable = Output(Bits(1))')

        elif is_driver or node in self.async_callees:
            self.append_code('trigger_counter_pop_valid = Input(Bits(1))')

        if not is_downstream and not self._is_external_module(node):
            for i in node.ports:
                name = namify(i.name)
                self.append_code(f'{name} = Input({dump_type(i.dtype)})')
                self.append_code(f'{name}_valid = Input(Bits(1))')
                has_pop = any(
                    isinstance(e, FIFOPop) and e.fifo == i
                    for e in self._walk_expressions(node.body)
                )
                if has_pop:
                    self.append_code(f'{name}_pop_ready = Output(Bits(1))')

        pushes = [e for e in self._walk_expressions(node.body) if isinstance(e, FIFOPush)]
        calls = [e for e in self._walk_expressions(node.body) if isinstance(e, AsyncCall)]

        unique_push_handshake_targets = {(p.fifo.module, p.fifo.name) for p in pushes}
        unique_call_handshake_targets = {c.bind.callee for c in calls}
        unique_output_push_ports = {p.fifo for p in pushes}

        # Skip external modules for handshake targets
        filtered_push_targets = set()
        for module, fifo_name in unique_push_handshake_targets:
            if not self._is_external_module(module):
                filtered_push_targets.add((module, fifo_name))

        filtered_call_targets = set()
        for callee in unique_call_handshake_targets:
            if not self._is_external_module(callee):
                filtered_call_targets.add(callee)

        for module, fifo_name in filtered_push_targets:
            port_name = f'fifo_{namify(module.name)}_{namify(fifo_name)}_push_ready'
            self.append_code(f'{port_name} = Input(Bits(1))')
        for callee in filtered_call_targets:
            port_name = f'{namify(callee.name)}_trigger_counter_delta_ready'
            self.append_code(f'{port_name} = Input(Bits(1))')

        # Skip external modules for output push ports
        filtered_output_push_ports = set()
        for fifo_port in unique_output_push_ports:
            if not self._is_external_module(fifo_port.module):
                filtered_output_push_ports.add(fifo_port)

        for fifo_port in filtered_output_push_ports:
            port_prefix = f"{namify(fifo_port.module.name)}_{namify(fifo_port.name)}"
            self.append_code(f'{port_prefix}_push_valid = Output(Bits(1))')
            dtype = fifo_port.dtype
            self.append_code(f'{port_prefix}_push_data = Output({dump_type(dtype)})')
        for callee in filtered_call_targets:
            self.append_code(f'{namify(callee.name)}_trigger = Output(UInt(8))')
        # pylint: disable=too-many-nested-blocks
        for arr_container in self.sys.arrays:
            arr = arr_container
            if is_sram:
                sram_info = get_sram_info(node)
                if sram_info and arr == sram_info['array']:
                    continue
            if node in self.array_users.get(arr, []):
                self.append_code(
                    f"{namify(arr.name)}_q_in = "
                    f"Input(dim({dump_type(arr.scalar_ty)}, {arr.size}))"
                )
                port_mapping = self.array_write_port_mapping.get(arr, {})
                for module_key, port_idx in port_mapping.items():
                    if module_key == node:
                        port_suffix = f"_port{port_idx}"
                        self.append_code( \
                            f'{namify(arr.name)}_w{port_suffix} = Output(Bits(1))')
                        self.append_code(
                            f'{namify(arr.name)}_wdata{port_suffix} ='
                            f' Output({dump_type(arr.scalar_ty)})'
                        )
                        idx_type = arr.index_bits if arr.index_bits > 0 else 1
                        self.append_code(
                            f'{namify(arr.name)}_widx{port_suffix} ='
                            f' Output(Bits({idx_type}))'
                        )


        for port_code in self.exposed_ports_to_add:
            self.append_code(port_code)

        self.append_code('')
        self.append_code('@generator')
        self.append_code('def construct(self):')

        if is_sram:
            self.indent += 4
            self.append_code('# SRAM dataout from memory')
            self.append_code('dataout = self.mem_dataout')
            self.code.extend(construct_method_body)
            self.indent -= 4
        else:
            self.code.extend(construct_method_body)
        self.indent -= 4
        self.append_code('')

    def _walk_expressions(self, block: Block):
        """Recursively walks a block and yields all expressions."""
        if block is None:
            return
        if block.body is None:
            return
        for item in block.body:
            if isinstance(item, Expr):
                yield item
            elif isinstance(item, Block):
                yield from self._walk_expressions(item)

    def _build_mux_chain(self, writes, dtype):
        """Helper to build a mux chain for write data"""
        first_val = self.dump_rval(writes[0][0].val, False)
        if writes[0][0].val.dtype != dump_type(dtype):
            first_val = f"{first_val}.{dump_type_cast(dtype)}"
        mux = f"Mux({writes[0][1]}, {dump_type(dtype)}(0), {first_val})"

        for expr, pred in writes[1:]:
            val = self.dump_rval(expr.val, False)
            if expr.val.dtype != dump_type(dtype):
                val = f"{val}.{dump_type_cast(dtype)}"
            mux = f"Mux({pred}, {mux}, {val})"

        return mux

    # pylint: disable=too-many-locals,R0912
    def visit_system(self, node: SysBuilder):
        sys = node
        self.sys = sys
        for module in sys.downstreams:
            if isinstance(module, SRAM) and hasattr(module, 'payload'):
                self.sram_payload_arrays.add(module.payload)

        # Collect external modules
        self.external_modules = []
        for module in sys.modules + sys.downstreams:
            if self._is_external_module(module):
                if module not in self.external_modules:
                    self.external_modules.append(module)
            # Also check for external modules used within downstream modules
            for expr in self._walk_expressions(module.body):
                if isinstance(expr, AsyncCall):
                    callee = expr.bind.callee
                    if self._is_external_module(callee):
                        if callee not in self.external_modules:
                            self.external_modules.append(callee)

        # Generate PyCDE wrapper classes for external modules first
        for ext_module in self.external_modules:
            self._generate_external_module_wrapper(ext_module)

        for arr_container in sys.arrays:
            if arr_container in self.sram_payload_arrays:
                continue
            sub_array = arr_container
            if sub_array not in self.array_write_port_mapping:
                self.array_write_port_mapping[sub_array] = {}
            sub_array_writers = sub_array.get_write_ports()
            for module, _ in sub_array_writers.items():
                if module not in self.array_write_port_mapping[sub_array]:
                    port_idx = len(self.array_write_port_mapping[sub_array])
                    self.array_write_port_mapping[sub_array][module] = port_idx

        for arr_container in sys.arrays:
            if arr_container not in self.sram_payload_arrays:
                self.visit_array(arr_container)

        expr_to_module = {}
        for module in sys.modules + sys.downstreams:
            for expr in self._walk_expressions(module.body):
                if expr.is_valued():
                    expr_to_module[expr] = module

        for ds_module in sys.downstreams:
            self.downstream_dependencies[ds_module] = get_upstreams(ds_module)

        all_modules = self.sys.modules + self.sys.downstreams
        for module in all_modules:
            for expr in self._walk_expressions(module.body):
                if isinstance(expr, AsyncCall):
                    callee = expr.bind.callee
                    if callee not in self.async_callees:
                        self.async_callees[callee] = []

                    if module not in self.async_callees[callee]:
                        self.async_callees[callee].append(module)

        self.array_users = {}
        # pylint: disable=R1702
        for arr_container in self.sys.arrays:
            if arr_container in self.sram_payload_arrays:
                continue
            arr = arr_container
            self.array_users[arr] = []
            for mod in self.sys.modules + self.sys.downstreams:
                if isinstance(mod, SRAM) and hasattr(mod, 'payload') and arr == mod.payload:
                    continue
                for expr in self._walk_expressions(mod.body):
                    if isinstance(expr, (ArrayRead, ArrayWrite)) and expr.array == arr:
                        if mod not in self.array_users[arr]:
                            self.array_users[arr].append(mod)

        # Process only non-external modules from sys.modules
        for elem in sys.modules:
            if self._is_external_module(elem):
                continue

            self.current_module = elem
            self.visit_module(elem)
        self.current_module = None
        for elem in sys.downstreams:
            self.current_module = elem
            self.visit_module(elem)
        self.current_module = None
        self.is_top_generation = True
        self._generate_top_harness()
        self.is_top_generation = False

    # pylint: disable=too-many-statements
    def visit_array(self, node: Array):
        """Generates a PyCDE Module to encapsulate an array and its write logic."""
        array = node
        size = array.size
        dtype = array.scalar_ty
        index_bits = array.index_bits if array.index_bits > 0 else 1

        writers = list(array.get_write_ports().keys())
        num_write_ports = len(writers)

        dim_type = f"dim({dump_type(dtype)}, {size})"
        class_name = namify(array.name)

        self.append_code(f'class {class_name}(Module):')
        self.indent += 4
        self.append_code('clk = Clock()')
        self.append_code('rst = Reset()')
        self.append_code('')

        for i in range(num_write_ports):
            port_suffix = f"_port{i}"
            self.append_code(f'w{port_suffix} = Input(Bits(1))')
            self.append_code(f'widx{port_suffix} = Input(Bits({index_bits}))')
            self.append_code(f'wdata{port_suffix} = Input({dump_type(dtype)})')
            self.append_code('')

        self.append_code(f'q_out = Output({dim_type})')
        self.append_code('')
        self.append_code('@generator')
        self.append_code('def construct(self):')
        self.indent += 4
        initializer = array.initializer
        if initializer is not None:
            rst_value_str = str(initializer)
        else:
            rst_value_str = f"[0] * {size}"

        self.append_code(
            f'data_reg = Reg({dim_type}, '
            f'clk=self.clk, rst=self.rst, rst_value={rst_value_str})'
        )
        self.append_code('')
        if num_write_ports != 0:
            self.append_code('# Multi-port write logic')
            self.append_code('next_data_values = []')
            self.append_code(f'for i in range({size}):')
            self.indent += 4
            self.append_code('# Check each write port for this address')
            self.append_code('element_value = data_reg[i]')
            for port_idx in reversed(range(num_write_ports)):
                port_suffix = f"_port{port_idx}"
                self.append_code(
                    f'# Port {port_idx} write check'
                )
                self.append_code(
                    f'if_write_port{port_idx} = '
                    f'(self.w{port_suffix} & '
                    f'(self.widx{port_suffix} == Bits({index_bits})(i)))'
                )
                self.append_code(
                    f'element_value = Mux(if_write_port{port_idx}, '
                    f'element_value, self.wdata{port_suffix})'
                )
            self.append_code('next_data_values.append(element_value)')
            self.indent -= 4
            self.append_code(f'next_data = {dim_type}(next_data_values)')
        else:
            self.append_code('next_data = data_reg')
        self.append_code('data_reg.assign(next_data)')
        self.append_code('self.q_out = data_reg')

        self.indent -= 8
        self.append_code('')


    def _generate_external_module_wrapper(self, ext_module: ExternalSV):
        """Generate a PyCDE wrapper class for an external module."""
        class_name = namify(ext_module.name)
        module_name = getattr(ext_module, 'external_module_name', class_name)

        self.append_code(f'class {class_name}(Module):')
        self.indent += 4

        # Set the module name for PyCDE
        self.append_code(f'module_name = f"{module_name}"')
        if getattr(ext_module, 'has_clock', False):
            self.append_code('clk = Clock()')
        if getattr(ext_module, 'has_reset', False):
            self.append_code('rst = Reset()')

        # Check if the external module carries declared wires
        if hasattr(ext_module, '_wires') and ext_module._wires:
            # Handle wires with explicit directions
            for wire_name, wire in ext_module._wires.items():
                wire_type = dump_type(wire.dtype)
                if wire.direction == 'input':
                    self.append_code(f'{wire_name} = Input({wire_type})')
                elif wire.direction == 'output':
                    self.append_code(f'{wire_name} = Output({wire_type})')
                else:
                    # For undirected wires, default to Input (backward compatibility)
                    self.append_code(f'{wire_name} = Input({wire_type})')
        else:
            # Fallback to handling ports for backward compatibility
            for port in ext_module.ports:
                port_name = namify(port.name)
                port_type = dump_type(port.dtype)
                # For external modules, default all ports to Input for backward compatibility
                # Actual connections will be handled in the instantiation
                self.append_code(f'{port_name} = Input({port_type})')

        self.indent -= 4
        self.append_code('')
    # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    def _generate_top_harness(self):
        """
        Generates a generic Top-level harness that connects all modules based on
        the analyzed dependencies (async calls, array usage).
        """

        self.append_code('class Top(Module):')
        self.indent += 4
        self.append_code('clk = Clock()')
        self.append_code('rst = Reset()')
        self.append_code('global_cycle_count = Output(UInt(64))')
        self.append_code('global_finish = Output(Bits(1))')
        self.append_code('')
        self.append_code('@generator')
        self.append_code('def construct(self):')
        self.indent += 4

        sram_modules = [m for m in self.sys.downstreams if isinstance(m,SRAM)]
        if sram_modules:
            self.append_code('\n# --- SRAM Memory Blackbox Instances ---')
            for data_width, addr_width, array_name in self.memory_defs:
                self.append_code(f'mem_{array_name}_dataout = Wire(Bits({data_width}))')
                self.append_code(f'mem_{array_name}_address = Wire(Bits({addr_width}))')
                self.append_code(f'mem_{array_name}_write_data = Wire(Bits({data_width}))')
                self.append_code(f'mem_{array_name}_write_enable = Wire(Bits(1))')
                self.append_code(f'mem_{array_name}_read_enable = Wire(Bits(1))')

                # Instantiate memory blackbox (as external Verilog module)
                self.append_code('# Instantiate memory blackbox module')
                self.append_code(
                    f'mem_{array_name}_inst = sramBlackbox_{array_name}()'
                    '(clk=self.clk, rst_n=~self.rst, '
                    f'address=mem_{array_name}_address, '
                    f'wd=mem_{array_name}_write_data, '
                    'banksel=Bits(1)(1), '
                    f'read=mem_{array_name}_read_enable, '
                    f'write=mem_{array_name}_write_enable)'
                )

                # Now mem_{array_name}_dataout is properly driven by the module output
                self.append_code(f'mem_{array_name}_dataout.assign(mem_{array_name}_inst.dataout)')
                self.append_code('')

        self.append_code('\n# --- Global Cycle Counter ---')
        self.append_code('# A free-running counter for testbench control')

        self.append_code('cycle_count = Reg(UInt(64), clk=self.clk, rst=self.rst, rst_value=0)')
        self.append_code(
            'cycle_count.assign( (cycle_count + UInt(64)(1)).as_bits()[0:64].as_uint() )'
            )
        self.append_code('self.global_cycle_count = cycle_count')

        # --- 1. Wire Declarations (Generic) ---
        self.append_code('# --- Wires for FIFOs, Triggers, and Arrays ---')
        for module in self.sys.modules:
            if self._is_external_module(module):
                continue
            for port in module.ports:
                fifo_base_name = f'fifo_{namify(module.name)}_{namify(port.name)}'
                self.append_code(f'# Wires for FIFO connected to {module.name}.{port.name}')
                self.append_code(f'{fifo_base_name}_push_valid = Wire(Bits(1))')
                self.append_code(f'{fifo_base_name}_push_data = Wire(Bits({port.dtype.bits}))')
                self.append_code(f'{fifo_base_name}_push_ready = Wire(Bits(1))')
                self.append_code(f'{fifo_base_name}_pop_valid = Wire(Bits(1))')
                self.append_code(f'{fifo_base_name}_pop_data = Wire(Bits({port.dtype.bits}))')
                self.append_code(f'{fifo_base_name}_pop_ready = Wire(Bits(1))')

        # Wires for TriggerCounters (one per module)
        for module in self.sys.modules:
            if self._is_external_module(module):
                continue
            tc_base_name = f'{namify(module.name)}_trigger_counter'
            self.append_code(f'# Wires for {module.name}\'s TriggerCounter')
            self.append_code(f'{tc_base_name}_delta = Wire(Bits(8))')
            self.append_code(f'{tc_base_name}_delta_ready = Wire(Bits(1))')
            self.append_code(f'{tc_base_name}_pop_valid = Wire(Bits(1))')
            self.append_code(f'{tc_base_name}_pop_ready = Wire(Bits(1))')

        for arr_container in self.sys.arrays:
            arr = arr_container
            is_sram_array = any(isinstance(m, SRAM) and \
                                m.payload == arr for m in self.sys.downstreams)
            if is_sram_array:
                continue
            arr_name = namify(arr.name)
            index_bits = arr.index_bits if arr.index_bits > 0 else 1
            port_mapping = self.array_write_port_mapping.get(arr, {})
            num_ports = len(port_mapping)
            self.append_code(f'# Multi-port array {arr_name} with {num_ports} write ports')
            # Declare wires for each port
            for port_idx in range(num_ports):
                port_suffix = f"_port{port_idx}"
                self.append_code(f'aw_{arr_name}_w{port_suffix} = Wire(Bits(1))')
                self.append_code(
                    f'aw_{arr_name}_wdata{port_suffix} = Wire({dump_type(arr.scalar_ty)})'
                )
                self.append_code(
                    f'aw_{arr_name}_widx{port_suffix} = Wire(Bits({index_bits}))'
                )
            # Instantiate multi-port array
            port_connections = ['clk=self.clk', 'rst=self.rst']
            for port_idx in range(num_ports):
                port_suffix = f"_port{port_idx}"
                port_connections.extend([
                    f'w{port_suffix}=aw_{arr_name}_w{port_suffix}',
                    f'wdata{port_suffix}=aw_{arr_name}_wdata{port_suffix}',
                    f'widx{port_suffix}=aw_{arr_name}_widx{port_suffix}'
                ])
            self.append_code(
                f'array_writer_{arr_name} = {arr_name}({", ".join(port_connections)})'
            )

        # --- 2. Hardware Instantiations (Generic) ---
        self.append_code('\n# --- Hardware Instantiations ---')

        module_fifo_depths = {}
        all_modules = [m for m in (self.sys.modules + self.sys.downstreams)
                       if not self._is_external_module(m)]
        default_fifo_depth = 2
        for mod in all_modules:
            module_fifo_depths[mod] = \
                {port: default_fifo_depth for port in getattr(mod, 'ports', [])}

        for module in self.sys.modules + self.sys.downstreams:
            if not module.body:
                continue
            for expr in self._walk_expressions(module.body):
                if isinstance(expr, FIFOPush):
                    fifo_port = expr.fifo
                    owner = fifo_port.module
                    if owner not in module_fifo_depths:
                        continue
                    depth = getattr(expr, 'fifo_depth', None)
                    if not isinstance(depth, int) or depth <= 0:
                        depth = default_fifo_depth
                    current = module_fifo_depths[owner].get(fifo_port, default_fifo_depth)
                    module_fifo_depths[owner][fifo_port] = max(current, depth)

        for module in self.sys.modules:
            if self._is_external_module(module):
                continue
            depth_map = module_fifo_depths.get(module, {})
            for port in module.ports:
                fifo_base_name = f'fifo_{namify(module.name)}_{namify(port.name)}'
                depth = depth_map.get(port, default_fifo_depth)
                self.append_code(
                    f'{fifo_base_name}_inst = FIFO(WIDTH={port.dtype.bits}, DEPTH_LOG2={depth})'
                    f'(clk=self.clk, rst_n=~self.rst, push_valid={fifo_base_name}_push_valid, '
                    f'push_data={fifo_base_name}_push_data, pop_ready={fifo_base_name}_pop_ready)'
                )

                self.append_code(
                    f'{fifo_base_name}_push_ready.assign({fifo_base_name}_inst.push_ready)'
                )
                self.append_code(
                    f'{fifo_base_name}_pop_valid.assign({fifo_base_name}_inst.pop_valid)'
                )
                self.append_code(
                    f'{fifo_base_name}_pop_data.assign({fifo_base_name}_inst.pop_data)'
                )

        # Instantiate TriggerCounters
        for module in self.sys.modules:
            if self._is_external_module(module):
                continue
            tc_base_name = f'{namify(module.name)}_trigger_counter'
            self.append_code(
                f'{tc_base_name}_inst = TriggerCounter(WIDTH=8)'
                f'(clk=self.clk, rst_n=~self.rst, '
                f'delta={tc_base_name}_delta, pop_ready={tc_base_name}_pop_ready)'
            )
            self.append_code(
                f'{tc_base_name}_delta_ready.assign({tc_base_name}_inst.delta_ready)'
            )
            self.append_code(f'{tc_base_name}_pop_valid.assign({tc_base_name}_inst.pop_valid)')

        all_driven_fifo_ports = set()

        self.append_code('\n# --- Module Instantiations and Connections ---')

        module_deps = defaultdict(set)
        all_modules = self.sys.modules + self.sys.downstreams

        # Track which modules produce which expressions
        expr_producers = {}
        for module in all_modules:
            for expr in self._walk_expressions(module.body):
                if expr.is_valued():
                    expr_producers[expr] = module

        # Build dependencies for all modules
        for module in all_modules:
            # Dependencies from downstream_dependencies
            if module in self.downstream_dependencies:
                for dep in self.downstream_dependencies[module]:
                    module_deps[module].add(dep)

            # Dependencies from external values
            for ext_val in getattr(module, 'externals', {}).keys():
                if isinstance(ext_val, Expr) and ext_val in expr_producers:
                    producer = expr_producers[ext_val]
                    if producer != module:
                        module_deps[module].add(producer)
                elif isinstance(ext_val, Bind):
                    continue

        # Topological sort with proper dependency order
        def topological_sort(modules, deps):
            # Calculate in-degree (number of modules that depend on this module)
            dependents = defaultdict(set)
            for module, dependencies in deps.items():
                for dep in dependencies:
                    dependents[dep].add(module)

            in_degree = {m: len(deps.get(m, set())) for m in modules}

            # Start with modules that have no dependencies
            queue = deque([m for m in modules if in_degree[m] == 0])
            sorted_modules = []

            while queue:
                module = queue.popleft()
                sorted_modules.append(module)

                # For each module that depends on this one
                for dependent in dependents[module]:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        queue.append(dependent)

            # Handle any cycles by adding remaining modules
            for m in modules:
                if m not in sorted_modules:
                    sorted_modules.append(m)

            return sorted_modules

        # Sort modules by dependencies
        sorted_modules = topological_sort(all_modules, module_deps)

        for module in sorted_modules:
            if self._is_external_module(module):
                continue
            mod_name = namify(module.name)
            is_downstream = isinstance(module, Downstream)
            is_sram = isinstance(module, SRAM)

            self.append_code(f'# Instantiation for {module.name}')
            port_map = ['clk=self.clk', 'rst=self.rst', 'cycle_count=cycle_count']

            if not is_downstream:
                port_map.append(f"trigger_counter_pop_valid={mod_name}_trigger_counter_pop_valid")
                for port in module.ports:
                    fifo_base_name = f'fifo_{mod_name}_{namify(port.name)}'
                    if isinstance(port.dtype, Record):
                        port_map.append(f"{namify(port.name)}={fifo_base_name}_pop_data")
                    else:
                        port_map.append(
                            f"{namify(port.name)}="
                            f"{fifo_base_name}_pop_data.{dump_type_cast(port.dtype)}"
                        )
                    port_map.append(f"{namify(port.name)}_valid={fifo_base_name}_pop_valid")

            else:
                if module in self.downstream_dependencies:
                    for dep_mod in self.downstream_dependencies[module]:
                        dep_name = namify(dep_mod.name)
                        port_map.append(f"{dep_name}_executed=inst_{dep_name}.executed")

                for ext_val in module.externals:
                    if isinstance(ext_val, Bind) or isinstance(unwrap_operand(ext_val), Const):
                        continue
                    producer_module = ext_val.parent.module
                    producer_name = namify(producer_module.name)
                    port_name = self.get_external_port_name(ext_val)
                    exposed_name = self.dump_rval(ext_val, True)

                    data_conn = \
                        f"{port_name}=inst_{producer_name}.expose_{exposed_name}"
                    valid_conn = (
                        f"{port_name}_valid="
                        f"inst_{producer_name}.valid_{exposed_name}"
                    )

                    port_map.append(data_conn)
                    port_map.append(valid_conn)
                if is_sram:
                    sram_info = get_sram_info(module)
                    array = sram_info['array']
                    array_name = namify(array.name)
                    port_map.append(f'mem_dataout=mem_{array_name}_dataout')

            for arr, users in self.array_users.items():
                if module in users:
                    port_map.append(
                        f"{namify(arr.name)}_q_in = array_writer_{namify(arr.name)}.q_out"
                    )

            pushes = [e for e in self._walk_expressions(module.body) if isinstance(e, FIFOPush)]
            calls = [e for e in self._walk_expressions(module.body) if isinstance(e, AsyncCall)]

            for p in pushes:
                # Store the actual Port object that is the target of a push
                all_driven_fifo_ports.add(p.fifo)

            unique_push_targets = {(p.fifo.module, p.fifo) for p in pushes}
            unique_call_targets = {c.bind.callee for c in calls}

            # Filter out external modules from push targets
            filtered_push_targets = set()
            for (callee_mod, callee_port) in unique_push_targets:
                if not self._is_external_module(callee_mod):
                    filtered_push_targets.add((callee_mod, callee_port))

            # Filter out external modules from call targets
            filtered_call_targets = set()
            for callee_mod in unique_call_targets:
                if not self._is_external_module(callee_mod):
                    filtered_call_targets.add(callee_mod)

            for (callee_mod, callee_port) in filtered_push_targets:
                port_map.append(
                    f"fifo_{namify(callee_mod.name)}_{namify(callee_port.name)}_push_ready="
                    f"fifo_{namify(callee_mod.name)}_{namify(callee_port.name)}_push_ready"
                )
            for callee_mod in filtered_call_targets:
                port_map.append(
                    f"{namify(callee_mod.name)}_trigger_counter_delta_ready="
                    f"{namify(callee_mod.name)}_trigger_counter_delta_ready"
                )

            self.append_code(f"inst_{mod_name} = {mod_name}({', '.join(port_map)})")

            if is_sram:
                sram_info = get_sram_info(module)
                array = sram_info['array']
                array_name = namify(array.name)
                self.append_code(f'mem_{array_name}_address.assign(inst_{mod_name}.mem_address)')
                self.append_code(
                    f'mem_{array_name}_write_data.assign(inst_{mod_name}.mem_write_data)'
                    )
                self.append_code(
                    f'mem_{array_name}_write_enable.assign(inst_{mod_name}.mem_write_enable)'
                    )
                self.append_code(
                    f'mem_{array_name}_read_enable.assign(inst_{mod_name}.mem_read_enable)'
                    )

            module_ports = getattr(module, 'ports', [])

            if not is_downstream:
                self.append_code(
                    f"{mod_name}_trigger_counter_pop_ready.assign(inst_{mod_name}.executed)"
                    )
                for port in module_ports:
                    if any(isinstance(e, FIFOPop) and e.fifo == port \
                           for e in self._walk_expressions(module.body)):
                        self.append_code(
                            f"fifo_{mod_name}_{namify(port.name)}_pop_ready"
                            f".assign(inst_{mod_name}.{namify(port.name)}_pop_ready)"
                            )
            else:
                for port in module_ports:
                    fifo_name = f"fifo_{mod_name}_{namify(port.name)}"
                    self.append_code(
                        f"{fifo_name}_pop_ready.assign(Bits(1)(1))"
                    )

            for (callee_mod, callee_port) in unique_push_targets:
                callee_mod_name = namify(callee_mod.name)
                callee_port_name = namify(callee_port.name)
                self.append_code(
                    f"fifo_{callee_mod_name}_{callee_port_name}_push_valid"
                    f".assign(inst_{mod_name}.{callee_mod_name}_{callee_port_name}_push_valid)"
                    )
                self.append_code(
                    f"fifo_{callee_mod_name}_{callee_port_name}_push_data"
                    f".assign(inst_{mod_name}.{callee_mod_name}_{callee_port_name}_push_data"
                    f".as_bits())"
                    )
        self.append_code('\n# --- Global Finish Signal Collection ---')
        finish_signals = []
        for module in sorted_modules:
            mod_name = namify(module.name)
            # Check if this module type has finish conditions
            if hasattr(module, 'body'):
                # Check if module contains FINISH intrinsics
                has_finish = any(
                    isinstance(expr, Intrinsic) and expr.opcode == Intrinsic.FINISH
                    for expr in self._walk_expressions(module.body)
                )
                if has_finish:
                    finish_signals.append(f'inst_{mod_name}.finish')

        if finish_signals:
            if len(finish_signals) == 1:
                self.append_code(f'self.global_finish = {finish_signals[0]}')
            else:
                self.append_code(f'self.global_finish = {" | ".join(finish_signals)}')
        else:
            self.append_code('self.global_finish = Bits(1)(0)')

        # self.append_code('\n# --- Tie off unused FIFO push ports ---')
        for module in self.sys.modules:
            if self._is_external_module(module):
                continue
            for port in getattr(module, 'ports', []):
                if port not in all_driven_fifo_ports:
                    fifo_base_name = f'fifo_{namify(module.name)}_{namify(port.name)}'
                    self.append_code(f'{fifo_base_name}_push_valid.assign(Bits(1)(0))')
                    self.append_code(
                        f"{fifo_base_name}_push_data"
                        f".assign(Bits({port.dtype.bits})(0))"
                        )
        self.append_code('\n# --- Array Write-Back Connections ---')
        for arr_container in self.sys.arrays:
            if arr_container in self.array_users and arr_container not in self.sram_payload_arrays:
                self._connect_array(arr_container)

        self.append_code('\n# --- Trigger Counter Delta Connections ---')
        for module in self.sys.modules:
            if self._is_external_module(module):
                continue
            mod_name = namify(module.name)
            if module in self.async_callees:
                callers_of_this_module = self.async_callees[module]
                trigger_terms = [
                    f"inst_{namify(c.name)}.{mod_name}_trigger"
                    for c in callers_of_this_module
                ]
                if len(trigger_terms) > 1:
                    summed_triggers = f"({' + '.join(trigger_terms)})"
                else:
                    summed_triggers = trigger_terms[0]

                self.append_code(
                    f"{mod_name}_trigger_counter_delta.assign({summed_triggers}.as_bits(8))"
                    )
            else:
                self.append_code(f"{mod_name}_trigger_counter_delta.assign(Bits(8)(1))")

        self.indent -= 8
        self.append_code('')
        self.append_code('system = System([Top], name="Top", output_directory="sv")')

        # Copying of external SystemVerilog files occurs during elaboration.

        self.append_code('system.compile()')

    def _connect_array(self, arr):
        """Connect each array to its writers"""
        arr_name = namify(arr.name)
        port_mapping = self.array_write_port_mapping.get(arr, {})
        if not port_mapping:
            return

        self.append_code(f'# Multi-port connections for {arr_name}')

        # Connect each module to its dedicated port
        for module, port_idx in port_mapping.items():
            module_name = namify(module.name)
            port_suffix = f"_port{port_idx}"

            self.append_code(
                f'aw_{arr_name}_w{port_suffix}.assign('
                f'inst_{module_name}.{arr_name}_w{port_suffix})'
            )
            self.append_code(
                f'aw_{arr_name}_wdata{port_suffix}.assign('
                f'inst_{module_name}.{arr_name}_wdata{port_suffix})'
            )
            if arr.index_bits > 0:
                self.append_code(
                    f'aw_{arr_name}_widx{port_suffix}.assign('
                    f'inst_{module_name}.{arr_name}_widx{port_suffix}'
                    f".as_bits({arr.index_bits}))"
                )
            else:
                self.append_code(
                    f'aw_{arr_name}_widx{port_suffix}.assign(Bits(1)(0))'
                )


def generate_design(fname: str, sys: SysBuilder):
    """Generate a complete Verilog design file for the system."""
    with open(fname, 'w', encoding='utf-8') as fd:
        fd.write(HEADER)

        dumper = CIRCTDumper()

        # Generate sramBlackbox module definitions for each SRAM
        sram_modules = [m for m in sys.downstreams if isinstance(m, SRAM)]
        if sram_modules:
            for sram in sram_modules:
                params = extract_sram_params(sram)
                array_name = params['array_name']
                data_width = params['data_width']
                addr_width = params['addr_width']
                dumper.memory_defs.add((data_width, addr_width, array_name))

            # Write sramBlackbox module definitions
            for data_width, addr_width, array_name in dumper.memory_defs:
                fd.write(f'''
@modparams
def sramBlackbox_{array_name}():
    class sramBlackboxImpl(Module):
        module_name = "sram_blackbox_{array_name}"
        clk = Clock()
        rst_n = Input(Bits(1))
        address = Input(Bits({addr_width}))
        wd = Input(Bits({data_width}))
        banksel = Input(Bits(1))
        read = Input(Bits(1))
        write = Input(Bits(1))
        dataout = Output(Bits({data_width}))
    return sramBlackboxImpl

''')
        dumper.visit_system(sys)
        code = '\n'.join(dumper.code)
        fd.write(code)
    logs = dumper.logs
    return logs
