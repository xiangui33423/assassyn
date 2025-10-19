使用 `assassyn` 语言实现一个硬件模块

## 参考的文档
1. docs/design/
2. tutorials/

## 实现功能

```
for i in 0..n:
  for j in 0..m:
     log(i, j)
```

1. 分多个模块实现。
   1. `driver` 模块只负责驱动时钟模块，每周期 call 一次 `counter` 模块。
   2. `counter` 模块实现 每个时钟调用一次log(i, j) 功能。
2. 将实现的代码放在 `python/test/easy_test1.py` 文件中。
3. 要求同时有verilator仿真

