'''The module provides the Array class for representing register arrays in the IR.'''

from __future__ import annotations

import typing

from ..builder import ir_builder, Singleton
from .dtype import to_uint, RecordValue, ArrayType
from .expr import ArrayRead, ArrayWrite, Expr,BinaryOp
from .value import Value
from ..utils import identifierize, namify
from .expr.writeport import WritePort

if typing.TYPE_CHECKING:
    from .dtype import DType
    from .module.base import ModuleBase


class Slice(Expr):
    '''The class for slice operation, where x[l:r] as a right value'''

    SLICE = 700

    def __init__(self, x, l: int, r: int):
        assert isinstance(l, int), f'Only int literal can slice, but got {type(l)}'
        assert isinstance(r, int), f'Only int literal can slice, but got {type(r)}'
        assert isinstance(x, Value), f'{type(x)} is not a Value!'
        l = to_uint(l)
        r = to_uint(r)
        super().__init__(Slice.SLICE, [x, l, r])

    @property
    def x(self) -> Value:
        '''Get the value to slice'''
        return self._operands[0]

    @property
    def l(self) -> int:
        '''Get the value to slice'''
        return self._operands[1]

    @property
    def r(self) -> int:
        '''Get the value to slice'''
        return self._operands[2]

    @property
    def dtype(self) -> DType:
        '''Get the data type of the sliced value'''
        # pylint: disable=import-outside-toplevel
        from .dtype import Bits
        from .const import Const
        assert isinstance(self.l.value, Const)
        assert isinstance(self.r.value, Const)
        return Bits(self.r.value.value - self.l.value.value + 1)

    def __repr__(self):
        l = self.l.as_operand()
        r = self.r.as_operand()
        return f'{self.as_operand()} = {self.x.as_operand()}[{l}:{r}]'



def RegArray( #pylint: disable=invalid-name,too-many-arguments
        scalar_ty: DType,
        size: int,
        initializer: list = None,
        name: str = None,
        attr: list = None,):
    '''
    The frontend API to declare a register array.

    Args:
        scalar_ty: The data type of the array elements.
        size: The size of the array. MUST be a compilation time constant.
        attr: The attribute list of the array.
        initializer: The initializer of the register array. If not set, it is 0-initialized.
    '''

    attr = attr if attr is not None else []


    res = Array(scalar_ty, size, initializer)
    if name is not None:
        res.name = name

    manager = getattr(Singleton, 'naming_manager', None)
    if manager is not None:
        hint = res._name if res._name is not None else None  # pylint: disable=protected-access

        # If no explicit name and we're inside a module, prefix with module name
        if hint is None:
            context_prefix = manager.get_context_prefix()
            if context_prefix:
                # Use a generic 'array' suffix for unnamed arrays
                hint = f"{context_prefix}_array"

        # Only assign name if no explicit name was provided
        if name is None:
            manager.assign_name(res, hint)

    Singleton.builder.arrays.append(res)

    return res

class Array:  #pylint: disable=too-many-instance-attributes
    '''The class represents a register array in the AST IR.'''

    scalar_ty: DType  # Data type of each element in the array
    size: int  # Size of the array
    initializer: list  # Initial values for the array elements
    attr: list  # Attributes of the array
    _users: typing.List[Expr]  # Users of the array
    _name: str  # Internal name storage
    _write_ports: typing.Dict['ModuleBase', 'WritePort'] = {} # Write ports for this array


    def as_operand(self):
        '''Dump the array as an operand.'''
        return self.name

    @property
    def name(self):
        '''The name of the array. If not set, a default name is generated.'''
        if self._name is not None:
            return self._name
        return f'array_{identifierize(self)}'

    @name.setter
    def name(self, name):
        self._name = namify(name)

    def __init__(self, scalar_ty: DType, size: int, initializer: list):
        #pylint: disable=import-outside-toplevel
        from .dtype import DType
        assert isinstance(scalar_ty, DType)
        self.scalar_ty = scalar_ty
        self.size = size
        self.initializer = initializer
        self.attr = []
        self._name = None
        self._users = []
        self._write_ports = {}
    @property
    def dtype(self) -> ArrayType:
        '''Get the data type of the array as an ArrayType.'''
        return ArrayType(self.scalar_ty, self.size)

    @property
    def users(self):
        '''Get the users of the array.'''
        return self._users

    def __and__(self, other):
        '''
        Overload & operator to create WritePort when combined with a Module.
        This enables write access: (array & module)[idx] <= value
        '''
        from .module.base import ModuleBase #pylint: disable=import-outside-toplevel
        if isinstance(other, ModuleBase):
            if other not in self._write_ports:
                self._write_ports[other] = WritePort(self, other)
            return self._write_ports[other]

        # Fall back to regular bitwise AND with Value
        if isinstance(other, Value):
            return BinaryOp(BinaryOp.BITWISE_AND, self, other)

        raise TypeError(f"Cannot AND Array with {type(other)}")

    def __repr__(self):
        '''Enhanced repr to show read/write operations in tree format'''
        # Start with array declaration
        res = f'{self.name} = [{self.scalar_ty}; {self.size}];'

        # Collect read and write operations
        read_ops = []
        write_ops = []

        for user in self._users:
            if isinstance(user, ArrayRead):
                read_ops.append(user)
            elif isinstance(user, ArrayWrite):
                write_ops.append(user)

        # Build tree structure
        all_ops = []

        # Add read operations
        for read_op in read_ops:
            module_name = getattr(read_op.parent, 'module', None)
            module_str = getattr(module_name, 'name', 'Unknown') if module_name else 'Unknown'
            all_ops.append(f'Read  by: {read_op} in {module_str}')

        # Add write operations
        for write_op in write_ops:
            module_name = getattr(write_op.parent, 'module', None)
            module_str = getattr(module_name, 'name', 'Unknown') if module_name else 'Unknown'
            all_ops.append(f'Write by: {write_op} in {module_str}')

        # Format tree structure
        if all_ops:
            res += '\n'
            for i, op in enumerate(all_ops):
                if i == len(all_ops) - 1:
                    # Last item uses `-
                    res += f'  `- {op}'
                else:
                    # Other items use |-
                    res += f'  |- {op}'
                if i < len(all_ops) - 1:
                    res += '\n'

        return res

    @property
    def index_bits(self):
        '''Get the number of bits needed to index the array.'''
        is_p2 = self.size & (self.size - 1) == 0
        return self.size.bit_length() - is_p2

    def index_type(self):
        '''Get the type of the index.'''
        #pylint: disable=import-outside-toplevel
        from .dtype import UInt
        return UInt(self.index_bits)

    def get_write_ports(self):
        '''Get the write_ports.'''
        return getattr(self, '_write_ports', {})

    @ir_builder
    def __getitem__(self, index: typing.Union[int, Value]):
        if isinstance(index, int):
            index = to_uint(index, self.index_bits)

        builder = Singleton.builder
        cache = builder.array_read_cache.setdefault(builder.current_block, {})
        cache_key = (self, index)
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        res = ArrayRead(self, index)
        cache[cache_key] = res
        return res

    def get_flattened_size(self):
        '''Get the flattened size of the array.'''
        return self.size * self.scalar_ty.bits

    @ir_builder
    def __setitem__(self, index, value):

        if isinstance(index, int):
            index = to_uint(index)
        assert isinstance(index, Value)
        assert isinstance(value, (Value, RecordValue)), type(value)

        current_module = Singleton.builder.current_module
        write_port = self & current_module
        return write_port._create_write(index, value)
