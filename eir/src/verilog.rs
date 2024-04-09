use std::{fs::File, io::Write};

use crate::{builder::system::SysBuilder, ir::node::*, ir::visitor::Visitor, ir::*};

struct VerilogDumper<'a> {
  sys: &'a SysBuilder,
  indent: usize,
  pred: Option<String>,
}

impl<'a> VerilogDumper<'a> {
  fn new(sys: &'a SysBuilder) -> Self {
    Self {
      sys,
      indent: 0,
      pred: None,
    }
  }
}

fn namify(name: &str) -> String {
  name.replace(".", "_")
}

macro_rules! fifo_name {
  ($fifo:expr) => {{
    format!("{}", $fifo.get_name())
  }};
}

struct NodeRefDumper;

impl Visitor<String> for NodeRefDumper {
  fn dispatch(&mut self, sys: &SysBuilder, node: &BaseNode, _: Vec<NodeKind>) -> Option<String> {
    match node.get_kind() {
      NodeKind::Array => {
        let array = node.as_ref::<Array>(sys).unwrap();
        namify(array.get_name()).into()
      }
      NodeKind::FIFO => namify(node.as_ref::<FIFO>(sys).unwrap().get_name()).into(),
      NodeKind::IntImm => {
        let int_imm = node.as_ref::<IntImm>(sys).unwrap();
        Some(format!(
          "({})",
          int_imm.get_value()
        ))
      }
      NodeKind::StrImm => {
        let str_imm = node.as_ref::<StrImm>(sys).unwrap();
        let value = str_imm.get_value();
        quote::quote!(#value).to_string().into()
      }
      NodeKind::Module => {
        let module_name = namify(node.as_ref::<Module>(sys).unwrap().get_name());
        format!("Box::new(EventKind::Module_{})", module_name).into()
      }
      _ => Some(format!("_{}", node.get_key())),
    }
  }
}

macro_rules! dump_ref {
  ($sys:expr, $value:expr) => {
    NodeRefDumper.dispatch($sys, $value, vec![]).unwrap()
  };
}

impl<'a> Visitor<String> for VerilogDumper<'a> {
  fn visit_module(&mut self, module: &ModuleRef<'_>) -> Option<String> {
    let mut res = String::new();

    res.push_str(format!("module {} (\n", module.get_name()).as_str());

    self.indent += 2;
    res.push_str(format!(
      "{}input logic clk,\n",
      " ".repeat(self.indent)
    ).as_str());
    res.push_str(format!(
      "{}input logic rst_n,\n",
      " ".repeat(self.indent)
    ).as_str());
    res.push_str("\n");
    for port in module.port_iter() {
      res.push_str(format!(
        "{}// port {}\n",
        " ".repeat(self.indent),
        fifo_name!(port)
      ).as_str());
      res.push_str(format!(
        "{}input logic {}_valid,\n",
        " ".repeat(self.indent),
        fifo_name!(port)
      ).as_str());
      res.push_str(format!(
        "{}input logic [{}:0] {}_data,\n",
        " ".repeat(self.indent),
        port.scalar_ty().bits() - 1,
        fifo_name!(port)
      ).as_str());
      res.push_str(format!(
        "{}output logic {}_ready,\n\n",
        " ".repeat(self.indent),
        fifo_name!(port)
      ).as_str());
    }
    res.push_str(format!(
      "{}input logic trigger\n",
      " ".repeat(self.indent)
    ).as_str());
    self.indent -= 2;
    res.push_str(");\n\n");

    for (array, ops) in module.ext_interf_iter() {
      let array_ref = array.as_ref::<Array>(self.sys).unwrap();
      res.push_str(format!(
        "logic [{}:0] {}_r;\n",
        array_ref.scalar_ty().bits() - 1,
        array_ref.get_name()
      ).as_str());
    }
    if module.ext_interf_iter().next().is_some() {
      res.push_str("\n");
    }

    for elem in module.get_body().iter() {
      match elem.get_kind() {
        NodeKind::Expr => {
          let expr = elem.as_ref::<Expr>(self.sys).unwrap();
          res.push_str(self.visit_expr(&expr).unwrap().as_str());
        }
        NodeKind::Block => {
          let block = elem.as_ref::<Block>(self.sys).unwrap();
          res.push_str(self.visit_block(&block).unwrap().as_str());
        }
        _ => {
          panic!("Unexpected reference type: {:?}", elem);
        }
      }
    }

    res.push_str(format!("endmodule // {}\n\n\n", module.get_name()).as_str());

    Some(res)
  }


  fn visit_block(&mut self, block: &BlockRef<'_>) -> Option<String> {
    let mut res = String::new();
    if let Some(cond) = block.get_pred() {
      self.pred = 
        Some(format!(
          " && {}{}",
          dump_ref!(self.sys, &cond),
          if cond.get_dtype(block.sys).unwrap().bits() == 1 {
            "".into()
          } else {
            format!(" != 0")
          }
        ));
    }
    for elem in block.iter() {
      match elem.get_kind() {
        NodeKind::Expr => {
          let expr = elem.as_ref::<Expr>(self.sys).unwrap();
          res.push_str(self.visit_expr(&expr).unwrap().as_str());
        }
        NodeKind::Block => {
          let block = elem.as_ref::<Block>(self.sys).unwrap();
          res.push_str(self.visit_block(&block).unwrap().as_str());
        }
        _ => {
          panic!("Unexpected reference type: {:?}", elem);
        }
      }
    }
    self.pred = None;
    res.into()
  }

  fn visit_expr(&mut self, expr: &ExprRef<'_>) -> Option<String> {
    if expr.get_opcode().is_binary() {
      Some(format!(
        "logic [{}:0] _{};\nassign _{} = {} {} {};\n\n",
        expr.dtype().bits() - 1,
        expr.get_key(),
        expr.get_key(),
        dump_ref!(self.sys, expr.get_operand(0).unwrap()),
        expr.get_opcode().to_string(),
        dump_ref!(self.sys, expr.get_operand(1).unwrap()),
      ))
    } else if expr.get_opcode().is_unary() {
      Some(format!(
        "{}{}",
        expr.get_opcode().to_string(),
        dump_ref!(self.sys, expr.get_operand(0).unwrap())
      ))
     }  else {
      match expr.get_opcode() {

        Opcode::FIFOPop => {
          let fifo = expr
            .get_operand(0)
            .unwrap()
            .as_ref::<FIFO>(self.sys)
            .unwrap();
          Some(format!(
            "logic [{}:0] _{};\nassign _{} = {}_data;\nassign {}_ready = trigger{};\n\n",
            fifo.scalar_ty().bits() - 1,
            expr.get_key(),
            expr.get_key(),
            fifo.get_name(),
            fifo.get_name(),
            self.pred.clone().unwrap_or("".to_string())
          ))
        }

        Opcode::Log => {
          let mut format_str = dump_ref!(self.sys, expr.operand_iter().collect::<Vec<&BaseNode>>().first().unwrap());
          for elem in expr.operand_iter().skip(1) {
            format_str = format_str.replacen("{}", match elem.get_dtype(self.sys).unwrap() {
              DataType::Int(_) => "%d",
              DataType::Str => "%s",
              _ => "?",
            }, 1);
          }
          format_str = format_str.replace("\"", "");
          let mut res = String::new();
          res.push_str(format!("always_ff @(posedge clk iff trigger{}) ", self.pred.clone().unwrap_or("".to_string())).as_str());
          res.push_str("$display(\"%t\\t");
          res.push_str(format_str.as_str());
          res.push_str("\", $time, ");
          for elem in expr.operand_iter().skip(1) {
            res.push_str(format!("{}, ", dump_ref!(self.sys, elem)).as_str());
          }
          res.pop(); res.pop();
          res.push_str(");\n");
          res.push_str("\n");
          Some(res)
        }

        Opcode::Load => {
          let array_ref = &(expr
            .get_operand(0)
            .unwrap()
            .as_ref::<ArrayPtr>(self.sys)
            .unwrap())
            .get_array()
            .as_ref::<Array>(self.sys)
            .unwrap();
          Some(format!(
            "logic [{}:0] _{};\nassign _{} = {}_r;\n\n",
            expr.dtype().bits() - 1,
            expr.get_key(),
            expr.get_key(),
            array_ref.get_name()
          ))
        }

        Opcode::Store => {
          let array_ref = &(expr
            .get_operand(0)
            .unwrap()
            .as_ref::<ArrayPtr>(self.sys)
            .unwrap())
            .get_array()
            .as_ref::<Array>(self.sys)
            .unwrap();
          Some(format!(
            "always_ff @(posedge clk or negedge rst_n) if (!rst_n) {}_r <= '0; else if (trigger{}) {}_r <= {};\n\n",
            array_ref.get_name(),
            self.pred.clone().unwrap_or("".to_string()),
            array_ref.get_name(),
            dump_ref!(expr.sys, expr.get_operand(1).unwrap())
          ))
        }

        Opcode::FIFOPush => {
          let value = expr.get_operand(2).unwrap();
          let fifo_idx = expr
            .get_operand(1)
            .unwrap()
            .as_ref::<IntImm>(self.sys)
            .unwrap()
            .get_value();
          let module = expr
            .get_operand(0)
            .unwrap()
            .as_ref::<Module>(self.sys)
            .unwrap();
          let fifo = module
            .get_input(fifo_idx as usize)
            .unwrap()
            .as_ref::<FIFO>(self.sys)
            .unwrap();
          let mut res = String::new();
          res.push_str(format!("logic _{}_valid;\n", expr.get_key()).as_str());
          res.push_str(format!("logic [{}:0] _{}_data;\n", value.get_dtype(self.sys).unwrap().bits() - 1, expr.get_key()).as_str());
          res.push_str(format!("logic _{}_ready;\n", expr.get_key()).as_str());
          res.push_str(format!("fifo #({}) fifo_{}_{}_i (\n", value.get_dtype(self.sys).unwrap().bits(), module.get_name(), fifo.get_name()).as_str());
          res.push_str(format!("  .clk(clk),\n").as_str());
          res.push_str(format!("  .rst_n(rst_n),\n").as_str());
          res.push_str(format!("  .up_valid(trigger{}),\n", self.pred.clone().unwrap_or("".to_string())).as_str());
          res.push_str(format!("  .up_data({}),\n", value.to_string(self.sys)).as_str());
          res.push_str(format!("  .up_ready(),\n").as_str());
          res.push_str(format!("  .dn_valid(_{}_valid),\n", expr.get_key()).as_str());
          res.push_str(format!("  .dn_data(_{}_data),\n", expr.get_key()).as_str());
          res.push_str(format!("  .dn_ready(_{}_ready)\n", expr.get_key()).as_str());
          res.push_str(format!(");\n").as_str());
          res.push_str("\n");
          Some(res)
        }

        Opcode::Trigger => {
          let module = expr
            .get_operand(0)
            .unwrap()
            .as_ref::<Module>(self.sys)
            .unwrap();
          let mut res = String::new();
          res.push_str(format!("{} {}_i (\n", module.get_name(), module.get_name()).as_str());
          res.push_str(format!("  .clk(clk),\n").as_str());
          res.push_str(format!("  .rst_n(rst_n),\n").as_str());
          let mut i = 0;
          for op in expr.operand_iter().skip(1) {
            let port = module.get_input(i as usize).unwrap().as_ref::<FIFO>(self.sys).unwrap();
            res.push_str(format!("  .{}_valid(_{}_valid),\n", port.get_name(), op.get_key()).as_str());
            res.push_str(format!("  .{}_data(_{}_data),\n", port.get_name(), op.get_key()).as_str());
            res.push_str(format!("  .{}_ready(_{}_ready),\n", port.get_name(), op.get_key()).as_str());
            i += 1;
          }
          res.push_str(format!("  .trigger(trigger{})\n", self.pred.clone().unwrap_or("".to_string())).as_str());
          res.push_str(format!(");\n").as_str());
          res.push_str("\n");
          Some(res)
        }

        _ => {
          Some(format!("{:?}\n\n", expr.get_opcode()))
        }
      }
    }
  }
}

pub fn elaborate(sys: &SysBuilder, fname: String) -> Result<(), std::io::Error> {
  println!("Writing verilog rtl to {}", fname);

  let mut vd = VerilogDumper::new(sys);

  let mut fd = File::create(fname.clone())?;

  for module in vd.sys.module_iter() {
    fd.write(vd.visit_module(&module).unwrap().as_bytes())?;
  }

  fd.write("module fifo #(
    parameter WIDTH = 8
) (
    input logic clk,
    input logic rst_n,

    input  logic               up_valid,
    input  logic [WIDTH - 1:0] up_data,
    output logic               up_ready,

    output logic               dn_valid,
    output logic [WIDTH - 1:0] dn_data,
    input  logic               dn_ready
);

logic [WIDTH - 1:0] q[$];

always @(posedge clk) begin
    if (q.size() == 0) begin
        dn_valid = up_valid;
        dn_data = up_data;
        if (up_valid && !dn_ready) q.push_back(up_data);
    end else begin
        dn_valid = 1'b1;
        dn_data = q[0];
        if (dn_ready) q.pop_front();
    end
end

assign up_ready = 1'b1;

endmodule


module tb;

logic clk;
logic rst_n;

initial begin
    $fsdbDumpfile(\"wave.fsdb\");
    $fsdbDumpvars();
end

initial begin
    clk = 1'b0;
    rst_n = 1'b0;
    #100;
    rst_n = 1'b1;
    #10000;
    $finish();
end

always #50 clk <= !clk;

driver driver_i (
    .clk(clk),
    .rst_n(rst_n),
    .trigger(1'b1)
);

endmodule
".as_bytes())?;

  Ok(())
}
