# PTOAS 软件路径导览

本文面向新加入 PTOAS 的开发者，回答两件事：仓库里有哪些软件路径，以及面对具体需求时该选哪条。细节不在这里展开，每节末尾给出专题文档链接。

## 1. 概览

**ptoas** 是基于 LLVM/MLIR（llvmorg-19.1.7）的 Out-of-Tree 编译器工具链，负责把 PTO Bytecode（`.pto`）解析、优化，并下降为调用 `pto-isa` 的 C++ kernel 源码。上层框架（PyPTO、TileLang、CuTile）通过 Python 绑定构建 IR；落地则依赖目标架构、CANN / `bisheng` 以及 `pto-isa`。

```text
  PyPTO / TileLang / CuTile / Python 绑定
                    │
                    ▼
                 .pto 文本 IR
           ┌────────┴────────┐
           ▼                 ▼
         ptobc             ptoas
     (.pto ↔ .ptobc)    Pass 流水线
                              │
                 三个选择点：
                 --pto-arch / --pto-level / 同步策略
                              │
                              ▼
                         .cpp kernel
                              │
     ┌──────────┬─────────────┼─────────────┬──────────┐
     ▼          ▼             ▼             ▼          ▼
  NPU 上板   Kirin9030    compile-only   CPU sim   CA model
  验证       + bisheng     （无卡）      (pto-isa)  / msprof
```

构建与环境配置见 [README.md](../README.md)。

## 2. 维度速查

### 2.1 工具入口

**是什么：** 进入 PTOAS 的三种方式。

| 入口 | 路径 | 何时用 |
|------|------|--------|
| `ptoas` | `.pto` → Pass 流水线 → `.cpp` | 编译 kernel（主路径） |
| `ptobc` | `.pto` ↔ `.ptobc` | 二进制字节码编解码，不跑优化 |
| Python 绑定 `mlir.dialects.pto` | Python 构建 / 操作 PTO IR | 框架集成、写 sample |

**选用建议：** 需要生成可编译 C++ 用 `ptoas`；只要在 Python 里造 IR 用绑定；字节码 round-trip 用 `ptobc`。

指针：[README §5](../README.md)、[tools/ptobc/README.md](../tools/ptobc/README.md)、`lib/Bindings/Python/`。

### 2.2 目标架构（`--pto-arch`）

**是什么：** 代码生成后端。默认 `a3`。

| 值 | 硬件 | 后续编译 |
|----|------|----------|
| `a3`（默认） | Ascend910B 系列 | NPU 验证 `--soc-version Ascend910B1` |
| `a5` | Ascend950 | NPU 验证 `--soc-version Ascend950` |
| `kirin9030` | 端侧 dav-l311 | `bisheng --cce-aicore-arch=dav-l311` |

**选用建议：** 云侧训练 / 推理卡选 A3 或 A5；手机 / 端侧选 Kirin9030。A5 与 Kirin9030 上 Vector 同管道时序由硬件保证，自动同步**不会**生成 `pipe_barrier(PIPE_V)`。

指针：`tools/ptoas/ptoas.cpp` 约 1465–1471 行；[端侧 Kirin9030 指南](lite-side-kirin9030-usage-guide_zh.md)。

### 2.3 构建等级（`--pto-level`）

**是什么：** 是否由编译器做本地内存规划。默认 `level2`。

| 值 | 行为 |
|----|------|
| `level1` / `level2`（默认） | 启用 `PlanMemory` 自动分配本地内存；`alloc_tile` **不能**带 `addr` |
| `level3` | 跳过 `PlanMemory`；`alloc_tile` **必须**带 `addr`（前端预分配） |

当前实现里 `level1` 与 `level2` 对 Pass 的效果相同，差异仅在 CLI 取值。含 `pto.tassign` 的模块必须用 `level3`。

**选用建议：** 普通算子用默认 `level2`；前端自己管地址或使用 `tassign` 时用 `level3`。

指针：`tools/ptoas/ptoas.cpp` 约 1338–1419 行。

### 2.4 自动同步策略（互斥）

**是什么：** 在内存规划之后，可选插入流水线同步。三个 flag 互斥，默认都不开。

| 选项 | 行为 |
|------|------|
| （默认） | 不插入自动同步；IR 里已有的 sync 仍会 lowering |
| `--enable-insert-sync` | InsertSync：set/wait 求解 |
| `--enable-inject-barrier-all-sync` | 保守插入 `barrier PIPE_ALL` |
| `--enable-graph-sync-solver` | 实验性图求解器（`--graph-sync-solver-event-id-max`，默认 8） |

含 `pto.tassign` 的模块禁止任何自动同步选项。

**选用建议：** 未手写 sync 的 kernel 优先 `--enable-insert-sync`；调试正确性可用 barrier-all；图求解器仅实验。手写了 sync 或使用 `tassign` 则保持默认关闭。

指针：`tools/ptoas/ptoas.cpp` 约 1344–1435 行；[自动同步设计](designs/ptoas-auto-sync-design.md)。

### 2.5 落地 / 验证路径

**是什么：** `.cpp` 生成之后怎么验证。

| 路径 | 适用 | 指针 |
|------|------|------|
| NPU 上板 | 有卡，要跑 kernel | [README §5.4](../README.md)、[CI 与上板](designs/ci-board-validation-guide.md) |
| Kirin9030 + bisheng | 端侧出 `.o` | [端侧指南](lite-side-kirin9030-usage-guide_zh.md) |
| Host compile-only | 无卡，只验证能编过 | [compile-only 指南](no_npu_compile_only_guide_zh.md)、`test/compile_cpp/` |
| CPU 模拟器 | 功能正确性，不依赖真卡 | 在 **pto-isa** 仓库完成，本仓库无独立用户文档 |
| CA model / msprof | 指令行为与性能 trace | [msprof 指南](msprof_op_simulator_usage_zh.md) |

**选用建议：** 日常开发先 compile-only 或 CPU sim，再上板；看流水线时序用 msprof；端侧走 Kirin9030 指南，不要混用 A3/A5 的 `soc-version`。

## 3. 选用指南

以下命令均摘自 README 或对应专题文档，假设已按 README 配好 `PATH` / `PYTHONPATH`。

### 3.1 在 A3/A5 上跑算子并上板

推荐组合：`ptoas` + 默认 `level2` + 对应 `--pto-arch` + `test/npu_validation`。若 kernel 未手写 sync，建议给 `ptoas` 加上 `--enable-insert-sync`（见 README §5.1）。

```bash
cd $PTO_SOURCE_DIR/test/samples/MatMul/
python3 ./tmatmulk.py > ./tmatmulk.pto
$PTO_SOURCE_DIR/build/tools/ptoas/ptoas ./tmatmulk.pto -o ./tmatmulk.cpp

# 上板脚本使用仓库根相对路径，先回到根目录：
cd $PTO_SOURCE_DIR
# A2/A3 示例：
python3 test/npu_validation/scripts/generate_testcase.py \
  --input test/samples/MatMul/tmatmulk.cpp \
  --run-mode npu \
  --soc-version Ascend910B1

# A5 示例（ptoas 需加 --pto-arch=a5）：
python3 test/npu_validation/scripts/generate_testcase.py \
  --input test/samples/MatMul/tmatmulk.cpp \
  --run-mode npu \
  --soc-version Ascend950

# 2) 运行验证（run.sh 无需额外参数）
test/samples/MatMul/npu_validation/tmatmulk/run.sh
```

上板命令从仓库根目录调用 `generate_testcase.py`（见 [README §5.3–5.4](../README.md)）。无卡请改走 §3.3。

### 3.2 我是框架开发者（PyPTO / TileLang / CuTile）

推荐组合：Python 绑定产出 `.pto`，再交给 `ptoas`。绑定本身不选择 arch / level / sync，那些是 `ptoas` 的事。

```python
from mlir.ir import Context, Module, Location
from mlir.dialects import pto

with Context() as ctx, Location.unknown():
    pto.register_dialect(ctx, load=True)
    module = Module.create()
```

样例：`test/samples/` 下各算子的 `.py`。环境变量见 [README §4–5.2](../README.md)。

### 3.3 无卡机器做 host 侧开发

推荐组合：`ptoas` 生成 `.cpp`，再用 `generate_testcase.py` 做 compile-only（不访问 `/dev/davinci*`）。

```bash
mkdir -p /tmp/ptoas_compile_only_inputs/Addc
./build/tools/ptoas/ptoas \
  test/samples/Addc/addc.pto \
  -o /tmp/ptoas_compile_only_inputs/Addc/addc-pto.cpp

python3 test/npu_validation/scripts/generate_testcase.py \
  --input /tmp/ptoas_compile_only_inputs/Addc/addc-pto.cpp \
  --testcase addc \
  --output-root /tmp/ptoas_compile_only \
  --run-mode npu \
  --soc-version Ascend910
```

完整 cmake 步骤见 [compile-only 指南 §3](no_npu_compile_only_guide_zh.md)。最小 bisheng 编译也可看 `test/compile_cpp/`。

### 3.4 端侧 Kirin9030

推荐组合：`--pto-arch=kirin9030` + `--enable-insert-sync` + CANN@Kirin 的 `bisheng`（`dav-l311`）。不要用 A3/A5 的 `--soc-version`。

```bash
cd test/samples/Adds
python3 adds.py > adds.pto
ptoas ./adds.pto --pto-arch=kirin9030 --enable-insert-sync -o ./adds.cpp
```

随后用 `bisheng --cce-aicore-arch=dav-l311` 编出 `.o`。完整参数与本地内存规格见 [端侧指南](lite-side-kirin9030-usage-guide_zh.md)。

## 附录 A. 主流水线 Pass 顺序

入口：`tools/ptoas/ptoas.cpp` 约 1399–1473 行。

```text
AssignDefaultFrontendPipeId → LowerFrontendPipeOps → InferValidatePipeInit
→ LoweringSyncToPipe → InferPTOLayout（可用 --disable-infer-layout 关掉）
→ A5NormalizeTMov → ValidateIntToPtrUses → ViewToMemref
→ PlanMemory（level3 跳过） → ResolveReservedBuffers
→ [同步：InsertSync / InjectBarrierAll / GraphSyncSolver，三选一或全关]
→ MaterializeTileHandles → CSE → EmitPTOManual(arch)
→ FormExpressions → CSE → translateToCpp（含 marker 重写）
```

`--emit-pto-ir` 在 `MaterializeTileHandles` 之前截断，打印 lowering 后的 MLIR。

## 附录 B. 相关文档

- [README.md](../README.md) — 构建、环境、CLI / Python / 上板
- [PTO IR 手册](PTO_IR_manual.md)
- [端侧 Kirin9030](lite-side-kirin9030-usage-guide_zh.md)
- [无卡 compile-only](no_npu_compile_only_guide_zh.md)
- [msprof op simulator](msprof_op_simulator_usage_zh.md)
- [CI 与上板](designs/ci-board-validation-guide.md)
- [自动同步设计](designs/ptoas-auto-sync-design.md)
- [ptobc](../tools/ptobc/README.md)
- 其它设计稿：`docs/designs/`
