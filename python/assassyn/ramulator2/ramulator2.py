"""Python wrapper for Ramulator2 memory simulator.

This module provides a Python interface to the Ramulator2 memory simulation
library through the CRamualator2Wrapper C++ wrapper.
"""
import os
import sys
import ctypes
from ctypes import c_void_p, c_char_p, c_float, c_bool, c_int64, CFUNCTYPE

def get_shared_lib_extension():
    """Get the appropriate shared library extension for the current OS."""
    if sys.platform.startswith('win'):
        return '.dll'
    if sys.platform.startswith('darwin'):
        return '.dylib'
    # Linux and other Unix-like systems
    return '.so'

def load_shared_library(lib_path):
    """Load a shared library with fallback for different extensions.

    Uses RTLD_GLOBAL mode to handle recursive shared object dependencies
    as per simulator.md documentation for macOS compatibility.
    """
    ext = get_shared_lib_extension()
    primary_path = lib_path + ext
    # Try the primary extension first
    if os.path.exists(primary_path):
        # Use RTLD_GLOBAL mode for macOS compatibility
        if sys.platform.startswith('darwin'):
            # On macOS, use RTLD_GLOBAL to handle recursive dependencies
            return ctypes.CDLL(primary_path, mode=ctypes.RTLD_GLOBAL)
        return ctypes.CDLL(primary_path)
    # Fallback: try other common extensions
    fallback_extensions = ['.so', '.dll', '.dylib']
    for fallback_ext in fallback_extensions:
        if fallback_ext != ext:
            fallback_path = lib_path + fallback_ext
            if os.path.exists(fallback_path):
                if sys.platform.startswith('darwin'):
                    return ctypes.CDLL(fallback_path, mode=ctypes.RTLD_GLOBAL)
                return ctypes.CDLL(fallback_path)
    # If no library found, raise an error
    raise FileNotFoundError(f"Could not find shared library at \
        {lib_path} with any supported extension")

home = os.getenv('ASSASSYN_HOME', os.getcwd())
wrapper_lib_path = os.path.abspath(f"{home}/tools/c-ramulator2-wrapper/build/lib/libwrapper")
ramulator_lib_path = os.path.abspath(f"{home}/3rd-party/ramulator2/libramulator")
wrapper = load_shared_library(wrapper_lib_path)
ramulator = load_shared_library(ramulator_lib_path)


# --- Define Request struct (partial mirror) --- pylint: disable=too-few-public-methods
class Request(ctypes.Structure):
    """Memory request structure mirroring the C++ Request class."""
    _fields_ = [
        ("addr", c_int64),               # Addr_t
        ("addr_vec_placeholder", ctypes.c_byte * 24),  # std::vector dummy (GCC/libstdc++ x86_64)
        ("type_id", ctypes.c_int),
        ("source_id", ctypes.c_int),
        ("command", ctypes.c_int),
        ("final_command", ctypes.c_int),
        ("is_stat_updated", c_bool),
        ("_padding", ctypes.c_byte * 7),   # align to 8 bytes
        ("arrive", c_int64),             # Clk_t
        ("depart", c_int64),             # Clk_t
        ("scratchpad", ctypes.c_int * 4),
        ("callback_placeholder", ctypes.c_byte * 32),  # std::function dummy
        ("m_payload", c_void_p),
    ]

# Define callback type
CALLBACK = CFUNCTYPE(None, c_void_p, c_void_p)
# CRamualator2Wrapper* opaque type
CRamualator2WrapperPtr = c_void_p
# Bind functions
wrapper.dram_new.argtypes = []
wrapper.dram_new.restype = CRamualator2WrapperPtr

wrapper.dram_delete.argtypes = [CRamualator2WrapperPtr]
wrapper.dram_delete.restype = None

wrapper.dram_init.argtypes = [CRamualator2WrapperPtr, c_char_p]
wrapper.dram_init.restype = None

wrapper.get_memory_tCK.argtypes = [CRamualator2WrapperPtr]
wrapper.get_memory_tCK.restype = c_float

wrapper.send_request.argtypes = [CRamualator2WrapperPtr, c_int64, c_bool, CALLBACK, c_void_p]
wrapper.send_request.restype = c_bool

wrapper.finish.argtypes = [CRamualator2WrapperPtr]
wrapper.finish.restype = None

wrapper.frontend_tick.argtypes = [CRamualator2WrapperPtr]
wrapper.frontend_tick.restype = None

wrapper.memory_system_tick.argtypes = [CRamualator2WrapperPtr]
wrapper.memory_system_tick.restype = None


class PyRamulator:
    """Python wrapper for Ramulator2 memory simulator.

    This class provides a high-level interface to interact with the Ramulator2
    memory simulator through the CRamualator2Wrapper C++ wrapper.
    """

    def __init__(self, config_path: str):
        """Initialize PyRamulator with configuration file.

        Args:
            config_path: Path to the YAML configuration file.

        Raises:
            RuntimeError: If the CRamualator2Wrapper instance cannot be created.
        """
        self.obj = wrapper.dram_new()
        if not self.obj:
            raise RuntimeError("Failed to create CRamualator2Wrapper instance")
        wrapper.dram_init(self.obj, config_path.encode('utf-8'))
        self.call_backs = []  # to keep references to callbacks
        self.ctxs = {}  # to keep references to ctx objects

    def __del__(self):
        """Clean up the underlying C++ wrapper instance."""
        if self.obj:
            wrapper.dram_delete(self.obj)
            self.obj = None
    # pylint: disable=invalid-name
    def get_memory_tCK(self) -> float:
        """Get memory clock period (tCK) in nanoseconds.

        Returns:
            Memory clock period in nanoseconds.
        """
        return wrapper.get_memory_tCK(self.obj)

    def finish(self):
        """Finalize the simulation and collect statistics."""
        wrapper.finish(self.obj)

    def frontend_tick(self):
        """Advance the frontend simulation by one clock cycle."""
        wrapper.frontend_tick(self.obj)

    def memory_system_tick(self):
        """Advance the memory system simulation by one clock cycle."""
        wrapper.memory_system_tick(self.obj)

    def send_request(self, addr: int, is_write: bool, callback, ctx) -> bool:
        """Send a memory request to the simulated memory system.

        Args:
            addr: Memory address for the request.
            is_write: True for write request, False for read request.
            callback: Python function to call when request completes.
            ctx: Context object passed to the callback function.

        Returns:
            True if request was successfully enqueued, False otherwise.

        Raises:
            ValueError: If callback is None.
        """
        if callback is None:
            raise ValueError("Callback must not be None")
        # Wrap Python ctx object → store it → get its pointer
        py_obj = ctypes.py_object(ctx)
        ctx_ptr = ctypes.cast(ctypes.pointer(py_obj), c_void_p)
        self.ctxs[ctx_ptr.value] = py_obj

        # C callback wrapper
        def _c_callback(req_ptr, ctx_ptr):
            req = ctypes.cast(req_ptr, ctypes.POINTER(Request)).contents
            # unwrap Python object
            py_obj = self.ctxs.get(ctx_ptr, None)
            ctx_val = py_obj.value
            callback(req, ctx_val)

        c_cb = CALLBACK(_c_callback)
        if c_cb not in self.call_backs:
            self.call_backs.append(c_cb)

        return wrapper.send_request(self.obj, addr, is_write, c_cb, ctx_ptr)
