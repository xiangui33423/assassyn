'''The untilities for the project'''

# import timeit
import os
import subprocess
import sys
import re

def identifierize(obj):
    '''The helper function to get the identifier of the given object. You can change `id_slice`
    to tune the length of the identifier. The default is slice(-5:-1).'''
    # pylint: disable=import-outside-toplevel
    from .builder import Singleton
    return hex(id(obj))[Singleton.id_slice]

def unwrap_operand(node):
    """Unwrap the operand from the node.

    This is a helper function to get the operand from the node.
    """
    # pylint: disable=import-outside-toplevel
    from .ir.expr import Operand
    if isinstance(node, Operand):
        return node.value
    return node

PATH_CACHE = None
VERILATOR_CACHE = None

def repo_path():
    '''Get the path to assassyn repository'''
    # pylint: disable=global-statement
    global PATH_CACHE
    if PATH_CACHE is None:
        PATH_CACHE = os.environ['ASSASSYN_HOME']
    return PATH_CACHE

def package_path():
    '''Get the path to this python package'''
    return repo_path() + '/python/assassyn'

def _cmd_wrapper(cmd):
    env = os.environ.copy()
    env.pop('RUSTC_WRAPPER', None)  # sccache fails under some sandboxed runners
    return subprocess.check_output(cmd, env=env).decode('utf-8')

def patch_fifo(file_path):
    """
    Replaces all occurrences of 'fifo_n #(' with 'fifo #(' in the Top.sv
    """
    if not os.path.isfile(file_path):
        return

    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    pattern = re.compile(r'fifo_\d+\s*#\s*\(')
    replacement = 'fifo #('
    new_content, num_replacements = pattern.subn(replacement, content)
    if num_replacements > 0:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)


def run_simulator(manifest_path, offline=False, release=True):
    '''The helper function to run the simulator'''

    def _run(off):
        cmd = ['cargo', 'run', '--manifest-path', manifest_path]
        if off:
            cmd += ['--offline']
        if release:
            cmd += ['--release']
        print(cmd)
        return _cmd_wrapper(cmd)

    try:
        return _run(offline)
    except subprocess.CalledProcessError as err:
        if offline:
            raise
        try:
            return _run(True)
        except subprocess.CalledProcessError as retry_err:
            raise err from retry_err

def run_verilator(path):
    '''The helper function to run the verilator'''
    restore = os.getcwd()
    os.chdir(path)
    cmd_design = ['python', 'design.py']
    subprocess.check_output(cmd_design)
    patch_fifo("sv/hw/Top.sv")
    cmd_tb = ['python', 'tb.py']
    res = _cmd_wrapper(cmd_tb)
    os.chdir(restore)
    return res

def parse_verilator_cycle(toks):
    '''Helper function to parse verilator dumped cycle'''
    # return int(toks[0]) // 100
    return int(toks[2][1:-4])

def parse_simulator_cycle(toks):
    '''Helper function to parse rust-simulator dumped cycle'''
    return int(toks[2][1:-4])

def has_verilator():
    '''Returns the path to Verilator or None if dependencies are missing'''
    # pylint: disable=global-statement
    global VERILATOR_CACHE
    if VERILATOR_CACHE is not None:
        return VERILATOR_CACHE

    verilator_root = os.environ.get('VERILATOR_ROOT')
    if not (verilator_root and os.path.isdir(verilator_root)):
        VERILATOR_CACHE = None
        return VERILATOR_CACHE

    env = os.environ.copy()
    env.pop('RUSTC_WRAPPER', None)
    try:
        subprocess.run(
            [sys.executable, '-c', 'import pycde'],
            check=True,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        VERILATOR_CACHE = None
    else:
        VERILATOR_CACHE = 'verilator'
    return VERILATOR_CACHE

def create_and_clean_dir(dir_path: str):
    """Create a directory and clear its contents if it already exists."""
    # Create the directory
    os.makedirs(dir_path, exist_ok=True)

def namify(name: str) -> str:
    """Convert a name to a valid identifier.

    This matches the Rust function in src/backend/simulator/utils.rs
    """
    return ''.join(c if c.isalnum() or c == '_' else '_' for c in name)
