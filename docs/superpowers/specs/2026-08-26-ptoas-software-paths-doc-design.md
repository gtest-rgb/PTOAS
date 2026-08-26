# PTOAS 软件路径导览文档 — 设计规格

- 日期：2026-08-26
- 状态：已获用户批准
- 交付物：`docs/ptoas-software-paths_zh.md`（新建，提交 git）

## 1. 背景与目的

PTOAS 当前存在多条"软件路径"，分散在 README、多篇 docs、CLI 参数与代码中，新成员难以快速建立全景认知。本文档任务是将这些路径梳理为一份**面向新成员的导览级**中文文档，回答两个问题：

1. PTOAS 有哪些软件路径？
2. 面对具体需求时应该选用哪条？

非目标：不改动任何代码；不替代 README 与现有专题文档（只做链接与速查，不重复细节）。

## 2. 路径事实清单（探索结论，文档的素材基础）

### 2.1 工具入口（3 条）

| 入口 | 路径 | 说明 |
|------|------|------|
| `ptoas` | `.pto` → Pass 流水线 → `.cpp` | 主编译器，生成调用 `pto-isa` 库的 C++ kernel 源码 |
| `ptobc` | `.pto` ↔ `.ptobc` | PTO-BC v0 字节码编码/解码器（独立二进制，见 `tools/ptobc/README.md`） |
| Python 绑定 | `mlir.dialects.pto` | 供 PyPTO / TileLang / CuTile 等上层框架在 Python 端直接构建/编译 PTO IR |

### 2.2 目标架构（`--pto-arch`，3 条）

- **A3**（默认，Ascend910B 系列）→ `createEmitPTOManualPass(PTOArch::A3)`
- **A5**（Ascend950）→ `createEmitPTOManualPass(PTOArch::A5)`
- **Kirin9030**（端侧，dav-l311）→ `createEmitPTOManualPass(PTOArch::Kirin9030)`

代码位置：`tools/ptoas/ptoas.cpp` 约 1465–1471 行。

### 2.3 构建等级（`--pto-level`，3 条）

- **level1 / level2**（默认 level2）：启用 `PlanMemory` 自动内存规划
- **level3**：禁用 `PlanMemory` / `InsertSync`，要求 `pto.alloc_tile` 必须带 `addr`（前端预分配地址）

代码位置：`tools/ptoas/ptoas.cpp` 约 1370–1419 行。

### 2.4 自动同步策略（互斥，3+1 条）

- 默认：不插入自动同步
- `--enable-insert-sync`：InsertSync set/wait 求解器
- `--enable-inject-barrier-all-sync`：保守的 `barrier_all` 插入
- `--enable-graph-sync-solver`：实验性图同步求解器（可用 `--graph-sync-solver-event-id-max` 调节）

注意：含 `pto.tassign` 的模块禁止使用任何自动同步选项（`ptoas.cpp` 约 1359–1368 行有显式检查）。

### 2.5 端到端落地/验证路径（5 条）

1. **NPU 上板验证**：`test/npu_validation`（A2/A3 用 `--soc-version Ascend910B1`，A5 用 `Ascend950`）
2. **端侧 Kirin9030**：`ptoas --pto-arch=kirin9030` → `bisheng --cce-aicore-arch=dav-l311` → `.o`（见 `docs/lite-side-kirin9030-usage-guide_zh.md`）
3. **Host-side compile-only**：`test/compile_cpp`（无卡机器只验证编译，见 `docs/no_npu_compile_only_guide_zh.md`）
4. **CPU 模拟器**：pto-isa CPU sim 功能测试
5. **CA model / msprof op simulator**：指令行为与性能分析（A3 `dav_2201`，见 `docs/msprof_op_simulator_usage_zh.md`）

### 2.6 主流水线 Pass 顺序

`tools/ptoas/ptoas.cpp` 约 1399–1473 行：

```text
AssignDefaultFrontendPipeId → LowerFrontendPipeOps → InferValidatePipeInit
→ LoweringSyncToPipe → InferPTOLayout(可禁用) → A5NormalizeTMov
→ ValidateIntToPtrUses → ViewToMemref → PlanMemory(level3 跳过)
→ ResolveReservedBuffers → [同步策略: InsertSync / InjectBarrierAll / GraphSyncSolver]
→ MaterializeTileHandles → CSE → EmitPTOManual(arch) → FormExpressions → CSE
→ translateToCpp（含 marker 重写后处理）
```

`--emit-pto-ir` 在 MaterializeTileHandles 之前截断流水线，输出 lowering 后的 MLIR IR。

## 3. 文档设计

### 3.1 文件与风格

- 路径：`docs/ptoas-software-paths_zh.md`
- 中文，命名与 `docs/` 下现有 `*_zh.md` 文档一致
- 导览级：正文控制在约 200 行以内；细节一律链接到 README 与现有专题文档，不复制内容

### 3.2 结构（混合式：全景图 + 维度速查 + 选用指南）

1. **概览**
   - 一段话说明 PTOAS 的定位（基于 LLVM/MLIR 19.1.7 的 PTO Bytecode 编译器工具链）
   - 一张 ASCII 全景图：上层框架/Python → `.pto` → `ptoas` 流水线（arch/level/sync 三个选择点）→ `.cpp` → 各落地路径（NPU / 端侧 bisheng / compile-only / CPU sim / 模拟器）
2. **维度速查**（5 个小节，对应 2.1–2.5）
   - 每节格式：是什么 → 有哪些选择 → 一句话选用建议 → 代码/文档指针
3. **选用指南**（4 个典型场景）
   - "我要在 A3/A5 上跑一个算子并上板验证"
   - "我是框架开发者（PyPTO/TileLang/CuTile）"
   - "我只有无卡机器，想做 host 侧开发"
   - "我要做端侧 Kirin9030"
   - 每场景给出推荐路径组合 + 最小命令示例（命令与 README/现有 docs 保持一致）
4. **附录**
   - 主流水线 Pass 顺序一览（2.6 的简化版）
   - 相关文档链接清单：README、`docs/lite-side-kirin9030-usage-guide_zh.md`、`docs/no_npu_compile_only_guide_zh.md`、`docs/msprof_op_simulator_usage_zh.md`、`docs/designs/`、`tools/ptobc/README.md`

### 3.3 准确性与验证

- 所有命令示例逐条与 README 及对应专题文档核对，不发明新命令
- 文档内相对链接逐一人工检查有效性
- 代码位置引用（文件+行号）以当前 master 为准，行号标注"约"

## 4. 测试与验收

- 文档渲染检查（Markdown 结构、表格、ASCII 图对齐）
- 链接有效性检查
- 用户评审通过即验收
