# Builder Module

This module provides the core infrastructure for building intermediate representation (IR) in assassyn. It implements a system builder that serves as both the IR builder and the system context manager, along with decorators for automatic IR node injection and naming management.

---

## Module Overview

The builder module is the core of assassyn's IR construction system. It provides:

1. **Context Management**: `SysBuilder` manages the hierarchical context of modules and blocks during IR construction
2. **Automatic IR Injection**: The `@ir_builder` decorator automatically injects IR nodes into the AST and tracks their source locations
3. **Source Name Inference**: Automatically derives meaningful variable names from Python source code using AST analysis
4. **Global State Management**: `Singleton` metaclass maintains global builder state and configuration

---

## Exposed Interfaces

```python
# Functions
def process_naming(expr, line_of_code: str, lineno: int) -> Dict[str, Any]
def ir_builder(func=None, *, node_type=None) -> Callable

# Classes
class SysBuilder:
    def __init__(self, name: str)
    def __enter__(self) -> SysBuilder
    def __exit__(self, exc_type, exc_value, traceback) -> None

    @property
    def current_module(self) -> Module | None
    @property
    def current_block(self) -> Block | None
    @property
    def insert_point(self) -> list
    @property
    def exposed_nodes(self) -> dict

    def enter_context_of(self, ty: str, entry) -> None
    def exit_context_of(self, ty: str) -> None
    def has_driver(self) -> bool
    def has_module(self, name: str) -> Module | None
    def expose_on_top(self, node, kind=None) -> None

class Singleton(type):
    builder: SysBuilder
    repr_ident: int
    id_slice: slice
    with_py_loc: bool
    all_dirs_to_exclude: list

    @classmethod
    def initialize_dirs_to_exclude(mcs) -> None
```

---

## SysBuilder Class

`SysBuilder` is a context manager that serves as both the system and the IR builder. It maintains the state of IR construction, including active modules, blocks, arrays, and exposed nodes.

**Key Attributes:**
- `name`: System name
- `modules`: List of all modules in the system
- `downstreams`: List of downstream modules
- `arrays`: List of array objects
- `_ctx_stack`: Dictionary tracking module and block context stacks (keys: `'module'`, `'block'`)
- `_exposes`: Dictionary mapping nodes to their exposure kinds
- `line_expression_tracker`: Tracks expressions on each source line for naming
- `naming_manager`: Instance of `NamingManager` for variable name generation

**Properties:**
- `current_module`: Returns the module at the top of the module context stack, or `None` if empty
- `current_block`: Returns the block at the top of the block context stack, or `None` if empty
- `insert_point`: Returns `current_block.body`, the list where new IR nodes are inserted
- `exposed_nodes`: Returns the dictionary of exposed nodes

**Context Methods:**
- `enter_context_of(ty, entry)`: Pushes a new context onto the stack for type `ty` (either `'module'` or `'block'`). For `CondBlock` entries, it adds the condition to the module's externals.
- `exit_context_of(ty)`: Pops the top context from the stack for type `ty`

**Query Methods:**
- `has_driver()`: Returns `True` if any module has class name `'Driver'`
- `has_module(name)`: Returns the module with the given name, or `None` if not found

**Node Management:**
- `expose_on_top(node, kind=None)`: Marks a node for exposure in the top-level function with an optional kind label

**Context Manager Protocol:**
When entering (`__enter__`), it sets itself as `Singleton.builder` and initializes the global naming tracker. When exiting (`__exit__`), it clears the singleton references. This ensures only one builder is active at a time.

**String Representation:**
`__repr__` generates a textual representation showing all arrays, modules, and downstreams in a structured format.

---

## Singleton Metaclass

`Singleton` maintains global state for the IR builder system using class attributes:

- `builder`: The currently active `SysBuilder` instance (or `None`)
- `repr_ident`: Indentation level for string representations
- `id_slice`: Slice used for generating object identifiers (default `slice(-6, -1)`), referenced by `utils.identifierize()`
- `with_py_loc`: Boolean flag controlling whether Python source locations are included in representations
- `all_dirs_to_exclude`: List of directory paths to exclude during stack inspection (site-packages, etc.)

**`initialize_dirs_to_exclude()`**: Lazily initializes `all_dirs_to_exclude` with Python's site-packages directories (from `site.getsitepackages()` and `site.getusersitepackages()`). This prevents the builder from attributing source locations to library code.

---

## IR Builder Decorator

**`ir_builder(func=None, *, node_type=None)`** is a decorator that wraps functions to automatically inject their return values into the IR. It provides two key features:

1. **Automatic IR Node Injection**: Non-`Const` return values are appended to `insert_point` (the current block's body)
2. **Source Location Tracking**: Uses stack inspection to determine the Python source location where the IR node was created

**Decorator Behavior (`_apply_ir_builder`):**

For each IR node returned by the decorated function:
- If the result is `None` or a `Const`, no special handling occurs
- For `Expr` nodes, sets `parent` to `current_block` and adds operands to the module's externals
- Inserts the node into `insert_point` (current block's body)
- Inspects the call stack to find the first frame outside the assassyn package and excluded directories, recording that location as `node.loc`
- For valued expressions with code context, calls `process_naming()` to infer a source name from the assignment statement

**Optional `node_type` Parameter**: If provided, attaches `_ir_builder_node_type` attribute to the decorated function, useful for metadata tracking.

---

## Source Name Inference

**`process_naming(expr, line_of_code, lineno)`** infers meaningful variable names from Python source code by parsing assignment statements.

**Algorithm:**
1. Parse the line of code as an AST
2. If it's an assignment (`ast.Assign`), track it in `line_expression_tracker[lineno]`
3. On first expression for this line, use `naming_manager.generate_source_names()` to extract target names from the assignment node
4. Match expressions to names by position in the assignment
5. For cast operations (opcode 800), append `_cast` suffix and ensure uniqueness
6. For expressions beyond available names, generate `tmp_{base_name}_{position}` with uniqueness guarantees

**Returns:** A unique variable name string, or `None` if naming cannot be determined.

**Example:** For `a, b = foo(), bar()`, the first expression gets name `a`, the second gets `b`.

---

## Key Implementation Details

- **Cyclic Import Handling**: Uses `TYPE_CHECKING` guard and import-outside-toplevel pattern to handle circular dependencies with IR modules
- **Stack Inspection**: The builder inspects `inspect.stack()[2:]` to skip internal frames and find user code locations
- **Directory Exclusion**: Excludes site-packages and the assassyn package itself from location tracking, ensuring only user code locations are recorded
- **Expression Tracking**: `line_expression_tracker` is a dictionary keyed by line number, storing lists of expressions and their generated names for multi-assignment statements
- **Naming Integration**: Works with `NamingManager` from the `namify` module to ensure globally unique variable names
