'''Memory module, a special and subclass of Module.'''

from .downstream import Downstream, combinational
from ..array import Array
from ..block import Condition
from ..expr import Bind
from ..value import Value
from .memorybase import MemoryBase


class SRAM(Downstream): # pylint: disable=too-many-instance-attributes, duplicate-code
    '''The SRAM module, a subclass of Downstream.'''
    #pylint: disable=duplicate-code
    width: int  # Width of the memory in bits
    depth: int  # Depth of the memory in words
    init_file: str  # Path to initialization file
    payload: Array  # Array holding the memory contents
    we: Value  # Write enable signal
    re: Value  # Read enable signal
    addr: Value  # Address signal
    wdata: Value  # Write data signal
    bound: Bind  # Bind handle

    def __init__(self, width, depth, init_file):
        super().__init__()
        MemoryBase.__init__(self, width, depth, init_file)
        self.bound = None

    @combinational
    def build(self, we, re, addr, wdata, user): #pylint: disable=too-many-arguments, duplicate-code
        '''The constructor for the SRAM module.

        # Arguments
        init_file: str: The file to initialize the memory.
        we: Value: The write enable signal.
        re: Value: The read enable signal.
        addr: Value: The address signal.
        wdata: Value: The write data signal.
        user: Module: The user module, it is required to have a rdata port.

        # Returns
        bound: Bind: The bound handle of the user module.
        '''
        self.we = we
        self.re = re
        self.addr = addr
        self.wdata = wdata

        with Condition(we):
            self.payload[addr] = wdata
        with Condition(re):
            self.bound = user.bind(rdata=self.payload[addr])

        return self.bound

    def __repr__(self):
        return self._repr_impl('downstream.SRAM')
