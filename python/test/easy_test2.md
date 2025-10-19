使用 `assassyn` 前端实现遍历二维索引并输出日志的硬件设计

## 参考资料
- `docs/design/` 中的体系结构与前端设计文档
- `tutorials/` 目录下的入门示例，尤其是 `00_driver_en.qmd`、`01_async_call_en.qmd`

## 目标功能

```
for i in 0..n:
  for j in 0..m:
    log(i, j)
```

### 设计约束
1. 按模块化方式拆分：
   - `Driver` 模块负责每个时钟启动遍历流程，并且决定何时结束仿真。
   - 创建 `Counter` 等子模块，通过 `async_called` / 端口传递索引值。关键在于 `log(i, j)` 的调用发生在硬件时序中。维护 `i`/`j` 寄存器并输出验证。
2. 使用 `RegArray` 储存索引，并在达到 `(n, m)` 时调用 `finish()` 停止仿真，避免闲置周期导致的超时。
3. 代码统一放在 `python/test/easy_test2.py`，提供命令行入口以便指定 `n`、`m`，并根据需要打印完整日志或过滤后的 `[Driver]` 输出。
4. 构建系统时以 `SysBuilder('easy_test2')` 为根，使用 `assassyn.backend.elaborate` 生成仿真工程。默认以 Rust 模拟器离线模式运行；如环境已安装 Verilator，可在配置中开启 `verilog=True` 并执行 `utils.run_verilator` 进行双重验证。