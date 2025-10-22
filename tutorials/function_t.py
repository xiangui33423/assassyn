import sys
import os
import io
import contextlib
from typing import Tuple, Optional

def run_quietly(func, *args, **kwargs) -> Tuple[str, Optional[str]]:
    """Runs a function while capturing all output."""
    stdout = io.StringIO()
    stderr = io.StringIO()
    result = None

    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            with open(os.devnull, 'w') as devnull:
                # 重定向原始文件描述符，捕获低层系统调用的输出
                old_stdout = os.dup(1)
                old_stderr = os.dup(2)
                os.dup2(devnull.fileno(), 1)
                os.dup2(devnull.fileno(), 2)
                try:
                    result = func(*args, **kwargs)
                finally:
                    os.dup2(old_stdout, 1)
                    os.dup2(old_stderr, 2)
        except Exception as e:
            stderr.write(f"Error: {str(e)}\n")

    return result, stdout.getvalue(), stderr.getvalue()


def build_and_show_ir(module_class, system_name, *args):
    """构建模块并显示 IR"""
    from assassyn.frontend import SysBuilder

    sys_build = SysBuilder(system_name)
    with sys_build:
        mod = module_class(*args)
        mod.build()

    print(f"\n=== {system_name} IR ===")
    print(sys_build)
    return sys_build


def generate_and_show_verilog(sys_build, show_keywords=None):
    """生成并显示顶层模块的 Verilog (PyCDE 形式)"""
    import assassyn
    from assassyn.backend import elaborate
    from assassyn import utils

    config = assassyn.backend.config(verilog=True, sim_threshold=10, idle_threshold=10)

    def gen_verilog():
        return elaborate(sys_build, **config)

    (_, verilog_path), _, _ = run_quietly(gen_verilog)

    if verilog_path:
        print(f"\n{'='*60}")
        print("生成的 Verilog 代码 (PyCDE 表示)")
        print(f"{'='*60}\n")

        # 显示 design.py 中的 Top 模块
        design_file = os.path.join(verilog_path, 'design.py')
        if os.path.exists(design_file):
            with open(design_file, 'r') as f:
                lines = f.readlines()

            # 找到 Top 类并显示
            print("📄 顶层模块 (Top):\n")
            in_top = False
            top_lines = []
            for line in lines:
                if line.startswith('class Top('):
                    in_top = True
                if in_top:
                    top_lines.append(line.rstrip())
                    # 检测到下一个顶层定义 (system = ...) 或空行后的 class
                    if line.startswith('system =') or (line.startswith('class ') and len(top_lines) > 5):
                        if line.startswith('system ='):
                            pass  # 包含这一行
                        else:
                            top_lines.pop()  # 不包含下一个 class
                        break

            # 显示 Top 类
            for line in top_lines:
                print(line)

        print(f"\n{'='*60}\n")
    else:
        print("⚠️  Verilator 不可用,跳过 Verilog 生成")

    return verilog_path