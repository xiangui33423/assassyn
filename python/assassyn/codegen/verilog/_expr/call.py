"""Call and wire operations code generation for Verilog.

This module contains functions to generate Verilog code for call operations,
including AsyncCall, Bind, WireAssign, and WireRead.
"""

from typing import Optional

from ....ir.expr import AsyncCall, WireAssign, WireRead
from ....ir.expr.call import Bind
from ....ir.module import Downstream
from ....ir.module.external import ExternalSV
from ....utils import namify


def codegen_async_call(dumper, expr: AsyncCall) -> Optional[str]:
    """Generate code for async call operations."""
    dumper.expose('trigger', expr)


def codegen_bind(_dumper, _expr: Bind) -> Optional[str]:
    """Generate code for bind operations.

    Bind operations don't generate any code, they just represent bindings.
    """


def codegen_wire_assign(dumper, expr: WireAssign) -> Optional[str]:
    """Generate code for wire assign operations."""
    # Annotate external wire assigns so they show up in the generated script
    if isinstance(dumper.current_module, Downstream):
        wire = expr.wire
        value = expr.value
        owner = getattr(wire, 'parent', None) or getattr(wire, 'module', None)
        wire_name = getattr(wire, 'name', None)
        if isinstance(owner, ExternalSV) and wire_name:
            dumper.pending_external_inputs[owner].append((wire_name, value))

    return f"# External wire assign: {expr}"


def register_external_wire_read(dumper, expr: WireRead):
    """Register bookkeeping for external wire reads without emitting code."""
    wire = expr.wire
    owner = getattr(wire, 'parent', None) or getattr(wire, 'module', None)
    if not isinstance(owner, ExternalSV):
        return owner

    dumper.expose('expr', expr)

    if dumper.current_module is not owner:
        module_externals = getattr(dumper.module_ctx, 'externals', None)
        if module_externals is not None and expr not in module_externals:
            module_externals.setdefault(expr, [])
        port_name = dumper.get_external_port_name(expr)
        key = (dumper.current_module, expr)
        if key not in dumper.external_wire_assignment_keys:
            dumper.external_wire_assignment_keys.add(key)
            dumper.external_wire_assignments.append({
                'consumer': dumper.current_module,
                'producer': owner,
                'wire': wire,
                'expr': expr,
                'port_name': port_name,
            })
    else:
        dumper.external_wire_outputs[wire] = dumper.get_external_port_name(expr)

    return owner


def codegen_wire_read(dumper, expr: WireRead) -> Optional[str]:
    """Generate code for wire read operations."""
    # Document reads from external module outputs and emit the assignment
    dumper.append_code(f'# External wire read: {expr}')
    wire = expr.wire
    wire_name = getattr(wire, 'name', None)
    rval = dumper.dump_rval(expr, False)

    owner = register_external_wire_read(dumper, expr)

    if isinstance(owner, ExternalSV):
        if dumper.current_module is not owner:
            port_name = dumper.get_external_port_name(expr)
            if rval == f"self.{port_name}":
                return None
            return f"{rval} = self.{port_name}"

        if owner not in dumper.instantiated_external_modules:
            ext_module_name = f"{namify(owner.name)}_ffi"
            inst_name = f"{ext_module_name.lower()}_inst"
            dumper.append_code('# instantiate external module')
            connections = []
            if getattr(owner, 'has_clock', False):
                connections.append('clk=self.clk')
            if getattr(owner, 'has_reset', False):
                connections.append('rst=self.rst')
            for input_name, input_val in dumper.pending_external_inputs.get(owner, []):
                connections.append(f"{input_name}={dumper.dump_rval(input_val, False)}")
            if connections:
                dumper.append_code(f'{inst_name} = {ext_module_name}({", ".join(connections)})')
            else:
                dumper.append_code(f'{inst_name} = {ext_module_name}()')
            dumper.instantiated_external_modules.add(owner)
            dumper.pending_external_inputs.pop(owner, None)

        if owner is not None and wire_name is not None:
            inst_name = f"{namify(owner.name)}_ffi".lower() + "_inst"
            return f"{rval} = {inst_name}.{wire_name}"

    return f"# TODO: unresolved external wire read for {expr}"
