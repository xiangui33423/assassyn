"""Module elaboration for simulator code generation."""

from __future__ import annotations

import typing

from ...ir.visitor import Visitor
from ...ir.block import Block, CondBlock, CycledBlock
from ...ir.dtype import RecordValue
from ...ir.expr import (
        Expr,
        BinaryOp,
        UnaryOp,
        ArrayRead,
        ArrayWrite,
        Cast,
        Intrinsic,
        PureIntrinsic,
        Bind,
        AsyncCall,
        FIFOPop,
        FIFOPush,
        Log,
        Select,
        Select1Hot,
        Slice,
        Concat,
)
from .utils import dtype_to_rust_type, fifo_name
from ...utils import namify
from .node_dumper import dump_rval_ref
from ...analysis import expr_externally_used

if typing.TYPE_CHECKING:
    from ...ir.module import Module

class ElaborateModule(Visitor):
    """Visitor for elaborating modules with multi-port write support."""

    def __init__(self, sys):
        """Initialize the module elaborator."""
        super().__init__()
        self.sys = sys
        self.indent = 0
        self.module_name = ""
        self.module_ctx = None
        self.modules_for_callback = {}

    def visit_module_for_callback(self, node: Module):
        """Visit a module to collect module names for callback."""
        self.module_name = node.name
        self.module_ctx = node
        self.visit_block(node.body)
        return self.modules_for_callback

    def visit_module(self, node: Module):
        """Visit a module and generate its implementation."""
        self.module_name = node.name
        self.module_ctx = node

        # Create function header
        result = [f"\n// Elaborating module {self.module_name}"]
        result.append(f"pub fn {namify(self.module_name)}(sim: &mut Simulator) -> bool {{")

        # Increase indentation for function body
        self.indent += 2

        # Visit the module body
        body = self.visit_block(node.body)
        result.append(body)

        # Decrease indentation and add function closing
        self.indent -= 2
        result.append(" true }")

        return "\n".join(result)

    # pylint: disable = too-many-statements, too-many-branches,too-many-locals
    def visit_expr(self, node: Expr):
        """Visit an expression and generate its implementation."""
        # pylint: disable=import-outside-toplevel
        id_and_exposure = None
        if node.is_valued():
            need_exposure = False
            need_exposure = expr_externally_used(node, True)
            id_expr = namify(node.as_operand())
            id_and_exposure = (id_expr, need_exposure)

        # Handle different expression types
        open_scope = False
        code = []

        if isinstance(node, BinaryOp):
            binop = BinaryOp.OPERATORS[node.opcode]

            if node.is_comparative():
                rust_ty = node.lhs.dtype
            else:
                rust_ty = node.dtype

            rust_ty = dtype_to_rust_type(rust_ty)
            lhs = dump_rval_ref(self.module_ctx, self.sys, node.lhs)
            rhs = dump_rval_ref(self.module_ctx, self.sys, node.rhs)

            # Special handling for shift operations with signed values
            if node.opcode == BinaryOp.SHR and node.lhs.dtype.is_signed():
                # For signed right shift, cast to signed type first
                if node.lhs.dtype.bits <= 64:
                    lhs = f"ValueCastTo::<i{node.lhs.dtype.bits}>::cast(&{lhs})"
                    rhs = f"ValueCastTo::<i{node.lhs.dtype.bits}>::cast(&{rhs})"
                else:
                    lhs = f"ValueCastTo::<BigInt>::cast(&{lhs})"
                    rhs = f"ValueCastTo::<BigInt>::cast(&{rhs})"
            else:
                lhs = f"ValueCastTo::<{rust_ty}>::cast(&{lhs})"
                rhs = f"ValueCastTo::<{rust_ty}>::cast(&{rhs})"

            code.append(f"{lhs} {binop} {rhs}")
        elif isinstance(node, UnaryOp):
            operand = dump_rval_ref(self.module_ctx, self.sys, node.x)
            uniop = UnaryOp.OPERATORS[node.opcode]
            code.append(f"{uniop}{operand}")

        elif isinstance(node, ArrayRead):
            array = node.array
            idx = node.idx
            array_name = namify(array.name)
            idx_val = dump_rval_ref(self.module_ctx, self.sys, idx)
            code.append(f"sim.{array_name}.payload[{idx_val} as usize].clone()")

        elif isinstance(node, ArrayWrite):
            # Handle array write with port information
            array = node.array
            idx = node.idx
            value = node.val
            module = node.module

            array_name = namify(array.name)
            idx_val = dump_rval_ref(self.module_ctx, self.sys, idx)
            value_val = dump_rval_ref(self.module_ctx, self.sys, value)
            module_writer = namify(module.name)
            port_id = id(module)  # Use module id as port identifier

            code.append(f"""{{
              let stamp = sim.stamp - sim.stamp % 100 + 50;
              sim.{array_name}.write_port.push(
                ArrayWrite::new(stamp, {idx_val} as usize, \
                      {value_val}.clone(), "{module_writer}", {port_id}));
            }}""")

        elif isinstance(node, AsyncCall):
            bind = node.bind
            event_q = f"{namify(bind.callee.name)}_event"
            code.append(f"""{{
              let stamp = sim.stamp - sim.stamp % 100 + 100;
              sim.{event_q}.push_back(stamp)
            }}""")

        elif isinstance(node, FIFOPop):
            fifo = node.fifo
            fifo_id = fifo_name(fifo)
            module_name = self.module_name

            code.append(f"""{{
              let stamp = sim.stamp - sim.stamp % 100 + 50;
              sim.{fifo_id}.pop.push(FIFOPop::new(stamp, "{module_name}"));
              match sim.{fifo_id}.payload.front() {{
                Some(value) => value.clone(),
                None => return false,
              }}
            }}""")

        elif isinstance(node, PureIntrinsic):
            intrinsic = node.opcode

            if intrinsic == PureIntrinsic.FIFO_PEEK:
                port_self = dump_rval_ref(self.module_ctx, self.sys, node.get_operand(0))
                code.append(f"sim.{port_self}.front().cloned()")

            elif intrinsic == PureIntrinsic.FIFO_VALID:
                port_self = dump_rval_ref(self.module_ctx, self.sys, node.get_operand(0))
                code.append(f"!sim.{port_self}.is_empty()")

            elif intrinsic == PureIntrinsic.VALUE_VALID:
                assert isinstance(node.get_operand(0).value, Expr)
                value = node.get_operand(0).value
                value = namify(value.as_operand())
                code.append(f"sim.{value}_value.is_some()")

            elif intrinsic == PureIntrinsic.MODULE_TRIGGERED:
                port_self = dump_rval_ref(self.module_ctx, self.sys, node.get_operand(0))
                code.append(f"sim.{port_self}_triggered")

        elif isinstance(node, FIFOPush):
            fifo = node.fifo
            fifo_id = fifo_name(fifo)
            value = dump_rval_ref(self.module_ctx, self.sys, node.val)
            module_writer = self.module_name
            # self.modules_for_callback["MemUser_rdata"] = fifo_id
            code.append(f"""{{
              let stamp = sim.stamp;
              sim.{fifo_id}.push.push(
                FIFOPush::new(stamp + 50, {value}.clone(), "{module_writer}"));
            }}""")

        elif isinstance(node, Log):
            mn = self.module_name
            result = [f'print!("@line:{{:<5}} {{:<10}}: [{mn}]\\t", line!(), cyclize(sim.stamp));']
            result.append("println!(")
            result.append(f"{dump_rval_ref(self.module_ctx, self.sys, node.operands[0])}, ")

            for elem in node.operands[1:]:
                dump = dump_rval_ref(self.module_ctx, self.sys, elem)
                dtype = elem.dtype
                if dtype.bits == 1:
                    dump = f"if {dump} {{ 1 }} else {{ 0 }}"
                result.append(f"{dump}, ")

            result.append(")")
            code.append("".join(result))

        elif isinstance(node, Slice):
            a = dump_rval_ref(self.module_ctx, self.sys, node.x)
            l = node.l.value.value
            r = node.r.value.value
            dtype = node.dtype
            num_bits = r - l + 1
            mask_bits = "1" * num_bits

            if l < 64 and r < 64:
                result_a = f'''let a = ValueCastTo::<u64>::cast(&{a});
                               let mask = u64::from_str_radix("{mask_bits}", 2).unwrap();'''
            else:
                result_a = f'''let a = ValueCastTo::<BigUint>::cast(&{a});
let mask = BigUint::parse_bytes("{mask_bits}".as_bytes(), 2).unwrap();'''

            code.append(f"""{{
                {result_a}
                let res = (a >> {l}) & mask;
                ValueCastTo::<{dtype_to_rust_type(dtype)}>::cast(&res)
            }}""")

        elif isinstance(node, Concat):
            dtype = node.dtype
            a = dump_rval_ref(self.module_ctx, self.sys, node.msb)
            b = dump_rval_ref(self.module_ctx, self.sys, node.lsb)
            b_bits = node.lsb.dtype.bits

            code.append(f"""{{
                let a = ValueCastTo::<BigUint>::cast(&{a});
                let b = ValueCastTo::<BigUint>::cast(&{b});
                let c = (a << {b_bits}) | b;
                ValueCastTo::<{dtype_to_rust_type(dtype)}>::cast(&c)
            }}""")

        elif isinstance(node, Select):
            cond = dump_rval_ref(self.module_ctx, self.sys, node.cond)
            true_value = dump_rval_ref(self.module_ctx, self.sys, node.true_value)
            false_value = dump_rval_ref(self.module_ctx, self.sys, node.false_value)
            code.append(f"if {cond} {{ {true_value} }} else {{ {false_value} }}")

        elif isinstance(node, Select1Hot):
            cond = dump_rval_ref(self.module_ctx, self.sys, node.cond)
            target_type = dtype_to_rust_type(node.dtype)
            result = [f'''{{ let cond = {cond};
assert!(cond.count_ones() == 1, \"Select1Hot: condition is not 1-hot\");''']

            for i, value in enumerate(node.values):
                if i != 0:
                    result.append(" else ")
                value_ref = dump_rval_ref(self.module_ctx, self.sys, value)
                result.append(f'''if cond >> {i} & 1 != 0
{{ ValueCastTo::<{target_type}>::cast(&{value_ref}) }}''')

            result.append(" else { unreachable!() } }")
            code.append("".join(result))

        elif isinstance(node, Cast):
            dest_dtype = node.dtype
            a = dump_rval_ref(self.module_ctx, self.sys, node.x)

            if node.opcode in [Cast.ZEXT, Cast.BITCAST, Cast.SEXT]:
                code.append(f"ValueCastTo::<{dtype_to_rust_type(dest_dtype)}>::cast(&{a})")

        elif isinstance(node, Bind):
            code.append("()")

        elif isinstance(node, Intrinsic):
            intrinsic = node.opcode

            if intrinsic == Intrinsic.WAIT_UNTIL:
                value = dump_rval_ref(self.module_ctx, self.sys, node.args[0])
                code.append(f"if !{value} {{ return false; }}")

            elif intrinsic == Intrinsic.FINISH:
                code.append("std::process::exit(0);")

            elif intrinsic == Intrinsic.ASSERT:
                value = dump_rval_ref(self.module_ctx, self.sys, node.args[0])
                code.append(f"assert!({value});")

            elif intrinsic == Intrinsic.BARRIER:
                code.append("/* Barrier */")

            elif intrinsic == Intrinsic.SEND_READ_REQUEST:
                idx = node.args[0]
                idx_val = dump_rval_ref(self.module_ctx, self.sys, idx)
                code.append(f"""{{
                    unsafe {{
                        let mem_interface = &sim.mem_interface;
                        let success = mem_interface.send_request({idx_val} as i64, false, rust_callback, sim as *const _ as *mut _,);
                        if success {{
                            sim.request_stamp_map_table.insert({idx_val} as i64, sim.stamp);
                        }}
                        success
                    }}
                }}""")

            elif intrinsic == Intrinsic.SEND_WRITE_REQUEST:
                idx = node.args[0]
                we = node.args[1]
                idx_val = dump_rval_ref(self.module_ctx, self.sys, idx)
                we_val = dump_rval_ref(self.module_ctx, self.sys, we)
                val = dump_rval_ref(self.module_ctx, self.sys, node)
                code.append(f"""
                    let {val} = unsafe {{
                        if {we_val} {{
                            let mem_interface = &sim.mem_interface;
                            let success = mem_interface.send_request({idx_val} as i64, true, rust_callback, sim as *const _ as *mut _,);
                            success
                        }} else {{
                            false
                        }}
                    }};
                """)
            elif intrinsic == Intrinsic.USE_DRAM:
                fifo = node.args[0]
                fifo_id = fifo_name(fifo)
                self.modules_for_callback["MemUser_rdata"] = fifo_id

            elif intrinsic == Intrinsic.HAS_MEM_RESP:
                val = dump_rval_ref(self.module_ctx, self.sys, node)
                if not self.modules_for_callback.get("MemUser_rdata"):
                    code.append(f"let {val} = false")
                else:
                    mem_rdata = self.modules_for_callback["MemUser_rdata"]
                    code.append(f"let {val} = sim.{mem_rdata}.payload.is_empty() == false")
            elif intrinsic == Intrinsic.MEM_RESP:
                val = dump_rval_ref(self.module_ctx, self.sys, node)
                if not self.modules_for_callback.get("MemUser_rdata"):
                    code.append(f"let {val} = 0")
                else:
                    mem_rdata = self.modules_for_callback["MemUser_rdata"]
                    code.append(f"let {val} = sim.{mem_rdata}.payload.front().unwrap().clone()")

            elif intrinsic == Intrinsic.MEM_WRITE:
                array = node.args[0]
                idx = node.args[1]
                value = node.args[2]
                array_name = namify(array.name)
                idx_val = dump_rval_ref(self.module_ctx, self.sys, idx)
                value_val = dump_rval_ref(self.module_ctx, self.sys, value)
                module_writer = self.module_name
                self.modules_for_callback["memory"] = module_writer
                self.modules_for_callback["store"] = array_name
                port_id = id("DRAM")
                code.append(f"""{{
                    let stamp = sim.stamp - sim.stamp % 100 + 50;
                    sim.{array_name}.write_port.push(
                        ArrayWrite::new(stamp, {idx_val} as usize, {value_val}.clone(), "{module_writer}", {port_id}));
                }}""")

        # Format the result with proper indentation and variable assignment
        indent_str = " " * self.indent
        result = ""

        if id_and_exposure:
            id_expr, need_exposure = id_and_exposure
            code_block = "\n".join(code)
            valid_update = ""
            if need_exposure:
                valid_update = f"sim.{id_expr}_value = Some({id_expr}.clone());"

            result = f"{indent_str}let {id_expr} = {{ {code_block} }}; {valid_update}\n"
        else:
            for line in code:
                result += f"{indent_str}{line};\n"

        if open_scope:
            self.indent += 2

        return result

    def visit_int_imm(self, int_imm):
        """Visit an integer immediate value."""
        ty = dump_rval_ref(self.module_ctx, self.sys, int_imm.dtype)
        value = int_imm.value
        return f"ValueCastTo::<{ty}>::cast(&{value})"

    def visit_block(self, node: Block):
        """Visit a block and generate its implementation."""
        result = []
        visited = set()

        # Save current indentation
        restore_indent = self.indent

        if isinstance(node, CondBlock):
            cond = dump_rval_ref(self.module_ctx, self.sys, node.cond)
            result.append(f"if {cond} {{\n")
            self.indent += 2
        elif isinstance(node, CycledBlock):
            result.append(f"if sim.stamp / 100 == {node.cycle} {{\n")
            self.indent += 2

        # Visit each element in the block
        for elem in node.iter():
            elem_id = id(elem)
            if elem_id in visited:
                continue
            visited.add(elem_id)
            if isinstance(elem, Expr):
                result.append(self.visit_expr(elem))
            elif isinstance(elem, Block):
                result.append(self.visit_block(elem))
            elif isinstance(elem, RecordValue):
                result.append(self.visit_expr(elem.value()))
            else:
                raise ValueError(f"Unexpected reference type: {type(elem).__name__}")

        # Restore indentation and close scope if needed
        if restore_indent != self.indent:
            self.indent -= 2
            result.append(f"{' ' * self.indent}}}\n")

        return "".join(result)
