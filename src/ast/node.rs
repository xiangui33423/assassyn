use proc_macro::Span;
use syn::spanned::Spanned;

use super::DType;
use super::Expr;

/// A port declaration is something like `a: int<32>`.
pub(crate) struct PortDecl {
  pub(crate) id: syn::Ident,
  pub(crate) ty: DType,
}

/// An array access is something like `a[<expr>]`
pub(crate) struct ArrayAccess {
  pub(crate) id: syn::Ident,
  pub(crate) idx: Expr,
}

/// A function call is something like `foo((<expr>,)*)`.
pub(crate) struct FuncCall {
  pub(crate) func: syn::Ident,
  pub(crate) args: FuncArgs,
}

/// Function arguments can either be {a: v0, b: v1}, or (v0, v1).
pub(crate) enum FuncArgs {
  Bound(Vec<(syn::Ident, Expr)>),
  Plain(Vec<Expr>),
}

/// Key value pair is a component of FuncArgs::Bound.
pub(crate) struct KVPair {
  pub(crate) key: syn::Ident,
  pub(crate) value: Expr,
}

/// A body is a sequence of instructions.
pub(crate) struct Body {
  pub(crate) stmts: Vec<Instruction>,
}

pub(crate) enum Instruction {
  Assign((syn::Ident, syn::Expr)),
  ArrayAlloc((syn::Ident, DType, syn::LitInt)),
  ArrayAssign((ArrayAccess, syn::Expr)),
  ArrayRead((syn::Ident, ArrayAccess)),
  AsyncCall(FuncCall),
  Bind((syn::Ident, FuncCall, bool)),
  SpinCall((ArrayAccess, FuncCall)),
  When((syn::Ident, Box<Body>)),
  Log(Vec<syn::Expr>),
}

impl Instruction {
  pub(crate) fn span(&self) -> proc_macro2::Span {
    match self {
      Instruction::Assign((id, _)) => id.span(),
      Instruction::ArrayAlloc((id, _, _)) => id.span(),
      Instruction::ArrayAssign((ArrayAccess { id, .. }, _)) => id.span(),
      Instruction::ArrayRead((id, ArrayAccess { id: idx, .. })) => {
        id.span().join(idx.span()).unwrap()
      }
      Instruction::AsyncCall(FuncCall { func, .. }) => func.span(),
      Instruction::Bind((id, _, _)) => id.span(),
      Instruction::SpinCall((ArrayAccess { id, .. }, _)) => id.span(),
      Instruction::When((id, _)) => id.span(),
      Instruction::Log(args) => args[0].span(),
    }
  }
}
