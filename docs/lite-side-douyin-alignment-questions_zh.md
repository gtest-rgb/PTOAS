# 端侧 PTO 对接：抖音沟通待澄清条目

**用途**：2026-08-14 与抖音（字节）端侧 AI 团队第二轮交流的信息收集清单。
**我方定位**：以收集需求和证据为主，**不承诺技术路线和时间点**；被追问发布节奏一律记入本文末尾的 open list 带回内部确认。
**关注范围**：PTO 方向（算子编译器路径、性能数据口径、ISA/硬件信息开放度、工具链）。推理引擎（MSLite）层不在本文范围。
**代码快照**：`feature/kirin9030-arch-support` @ `818d16d`（2026-08-11）。

## 1. 结论先行

抖音第一轮反馈的核心结论是"PTO 算子性能落后手写算子 26~50%（另一处口径写作劣化 3 倍），进展慢，不同芯片整网运行结果差异大"。

从本仓代码看，**这份数据极可能已经过时**：Kirin9030 的本地内存规格和 Vector 流水同步策略是 2026-08-10/11 才修正的（见 §2.1）。在此之前，PTO 在麒麟上按 A3 的片上存储尺寸做分配，并插入了硬件本不需要的 `pipe_barrier(PIPE_V)`。

因此明天的第一优先级不是解释性能差距，而是**先锁定他们测的是哪个版本**。如果测量早于 2026-08-10，那份数字应作废重测，整场交流的结论会完全不同。

## 2. 从代码核对出的事实（作为提问依据）

### 2.1 端侧支持在 fork 的 feature 分支上，关键项刚补完

| 日期 | Commit | 内容 |
| ---- | ------ | ---- |
| 2026-05-26 | `fd76c02` | 首次加入 Kirin9030 架构支持 |
| 2026-06-02 | `d0365f8` / `19a992b` | 加入端侧使用指南 |
| 2026-08-10 | `00166ea` | Kirin9030 上跳过 `pipe_barrier(PIPE_V)`（与 A5 一致，同管道时序由硬件保证） |
| 2026-08-11 | `818d16d` | 补齐 Kirin9030 本地内存容量与对齐 |

使用指南（[lite-side-kirin9030-usage-guide_zh.md](lite-side-kirin9030-usage-guide_zh.md)）让用户从 `github.com/gtest-rgb/PTOAS` 的 `feature/kirin9030-arch-support` 取码，而 PTOAS 的正式来源是 `gitcode.com/cann/pto-as`。**端侧支持尚未上主线**——这正是抖音"没看到发布时间和文档"的具体表现。

### 2.2 片上存储规格差异大到足以单独解释性能差距

`lib/PTO/Transforms/PTOPlanMemory.cpp:54-61`、`:2160-2200`：

| 空间 | A3 | A5 | Kirin9030 |
| ---- | -- | -- | --------- |
| UB (VEC) | 192KB | 248KB | **128KB** |
| L1 (MAT) | 512KB | 512KB | 512KB |
| L0A / L0B | 64KB | 64KB | **32KB** |
| L0C (ACC) | 128KB | 256KB | **64KB** |

L0C 只有 A5 的四分之一。`PTOPlanMemory` 的默认档是 a3，只有显式 `--pto-arch=kirin9030` 才切换（`:2189-2200`）。任何按数据中心尺寸选定的 tile 形状与 double-buffer 策略，搬到 L0C 64KB 的机器上都会退化。

**这是 26~50% 差距的头号候选原因，且属于我们自己能修的那一类。**

### 2.3 PTO 在端侧不是"直出 ISA"，与手写 cce 共用后端

端侧链路（使用指南 §1、§5）：

```text
Python 样例 → .pto → ptoas --pto-arch=kirin9030 → 调用 pto-isa 的 C++ → bisheng --cce-aicore-arch=dav-l311 → kernel .o
```

最终代码质量同时取决于 `pto-isa` 头文件实现和 bisheng 后端，而手写 cce 走的是同一个后端。两个后果：

1. 性能差距不能笼统归给"PTO 编译器"，必须先分离 pto-isa 实现质量、bisheng 版本、以及 PTO 自身的 tiling/同步决策。
2. 抖音"希望 PTO 开放 ISA 完备支持"这个诉求需要重新对齐：**PTO 是 tile 级虚拟 ISA，不是麒麟裸 ISA**，可能和他们想要的不是一个东西。

### 2.4 PTOAS 内没有 Triton 前端

`README.md` 声明的前端是 **PyPTO / TileLang / CuTile**，全仓搜不到 Triton。抖音方案表里的"Triton 降级到 PTO"，其前端归属、代码位置、维护责任在本仓完全没有落点。

**这可能就是"进展慢"的真实原因——这条链没有 owner。**

### 2.5 端侧验证与 profiling 闭环尚未建立

`test/npu_validation/scripts/generate_testcase.py` 的 `--soc-version` 只覆盖 `Ascend910B1` / `Ascend950`（README §5.4）；profiling 相关文档只有 [msprof_op_simulator_usage_zh.md](msprof_op_simulator_usage_zh.md) 和 [no_npu_compile_only_guide_zh.md](no_npu_compile_only_guide_zh.md)。抖音要的"整网 profiling"和"精度对比分析"在麒麟上目前没有现成流程。

## 3. 必问五条（时间只够问这些就问这些）

1. **他们测的是哪个 PTOAS commit / 哪天的包，`ptoas --version` 是多少，编译命令里有没有 `--pto-arch=kirin9030`。**
   为什么问：若答案是"没带 arch 参数"或"分支早于 2026-08-10"，那份性能数据基本作废，需要重测。
2. **"劣化 3 倍"与"落后手写算子 26~50%"分别对应什么测量对象**——单算子 kernel time 还是整网 decode token/s？
   为什么问：两个数字量级差太多，不可能是同一件事；按错的那个做内部决策会投错资源。附带确认 warmup/迭代次数、是否含编译时间、batch/seq 配置、是否锁频。
3. **基线手写 cce 用的是不是同一版 bisheng / CANN@Kirin**（使用指南锁的是 9.1.0）。
   为什么问：共用后端的前提下，这决定差距该归给 PTO 编译器还是 pto-isa 头文件实现。
4. **Triton→PTO 这条链的前端是谁做的、代码在哪、谁维护。**
   为什么问：不问清，无法判断"PTO 路径进展慢"是我们的问题还是无主的问题。
5. **他们算子的 tile 形状是谁定的、有没有针对 128KB UB / 64KB L0C 人工调过。**
   为什么问：如果没调过，gap 里有多大一块是纯 tiling 问题，我们可以自己验证复现。

## 4. 第二梯队：把差距归因到能落成 issue 的粒度

6. 那 26~50% 有没有 breakdown：搬运带宽（MTE）、cube 利用率、vector 空转、还是同步开销？有没有 profiling timeline 可以给我们。
7. **"不同芯片整网运行结果差异也很大"是精度差异还是性能差异？** 若为数值不一致，属正确性问题，优先级压过所有性能话题，当场索要具体 case。
8. 模型里 top-N 耗时算子清单与"最痛的三个"（paged attention、量化 matmul、rmsnorm、rope、KV cache 更新等）。补齐顺序应按此排，而不是按语言特性覆盖度排。
9. 3bit / 4bit 量化算子的具体方案：per-group 还是 per-channel、zero-point 处理、权重 packing layout、解量化在哪一级做。直接决定 pto-isa 需要补哪些原子能力。
10. 融合边界谁负责（Triton 层手写大 kernel / PTO 编译器自动融合）；以及是否需要手工控制片上分配与多级流水——`--pto-level=level3` 可关闭 PlanMemory 与 InsertSync（README §5.1），若他们想自己控，这条路现成，值得当场确认是否是他们要的。
11. **"性能达标"的量化定义**：追平手写 cce，还是追平同规格安卓上的 QNN（他们给的 8Gen2 约 70 token/s、8Gen3 约 86 token/s）？可接受 gap 是多少，按单算子还是整网 token/s 验收。没有这个数字，内部无法定义"做完了"。

## 5. 第三梯队：开放度、工具链、端侧硬约束

12. 让他们把"需要的硬件信息"按"缺了就没法优化"排序：指令语义、时延/吞吐表、流水与同步模型、片上存储层级与容量、bank conflict 规则。原话"底层 ISA 或一定抽象程度的 ISA"过于模糊，不排序无法判断能否提供。
13. **当场验证 PTO 这个 tile 级虚拟 ISA 是否就是他们要的"一定抽象程度的 ISA"**，不要自行假设满足。这恰是 PTO 相对 AscendC 的差异点——他们对 AscendC 的核心抱怨是"拿不到底层信息、优化天花板低"。
14. 端侧支持何时上主线、以什么形式发布（他们现在要从一个 fork 分支取码，见 §2.1）。
15. 是否需要"一份代码跨 9020/9030"，还是允许 per-chip 调优。决定要不要在 PTO 层做代际抽象，成本差别很大。
16. profiling 要到什么粒度（算子级排序 / timeline / 单元利用率 / 指令级下钻）、在什么环境使用（真机、是否需要特殊权限）；精度分析是否需要逐层 dump 与参考实现比对、容差标准。以及在端侧闭环建好前，他们最低可接受的替代方案。
17. 集成形态与端侧硬约束：kernel 二进制如何进入他们的推理引擎（预编译 `.o`/`.so`、静态注册、运行时 JIT）；包大小、启动时延、内存峰值是否有硬指标；功耗温升导致的降频。这些约束在数据中心侧不敏感，但会推翻一部分优化假设，也是"9020 比 9030 快 20%"的候选解释之一。

## 6. 离场前当面确认要拿到的材料

- 可复现包：Triton 源码 + shape/dtype 分布 + 他们的测量脚本 + 基线手写 cce 算子 + profiling 原始数据
- top-N 耗时算子清单与目标性能数字
- 精度差异的具体 case（若 §4.7 确认为数值问题）
- 他们侧对接人、沟通渠道、下次 checkpoint 时间

## 7. 我方口径纪律与 open list

不当场承诺：PTO 端侧发布时间、上主线节奏、开源计划、ISA 文档开放范围、性能达标承诺。

会后需内部确认（现场遇到即记入，不表态）：

- [ ] 端侧 Kirin9030 支持上主线（`gitcode.com/cann/pto-as`）的计划与形态
- [ ] 麒麟侧 ISA / 硬件信息可对外开放的范围与保密形式
- [ ] 端侧 profiling 与精度比对工具链的责任方
- [ ] Triton→PTO 前端的归属与投入

## 8. 我方会前自查（可在交流前先做）

1. 用 `--pto-arch=kirin9030` 在 `818d16d` 上重跑他们提到的算子类型，确认 §2.2 的内存规格修正带来多少提升——这个数字能直接改变交流走向。
2. 检查 tiling 决策是否仍隐含 A3/A5 的片上尺寸假设（尤其 L0C 64KB 下的 cube 分块与 double buffer）。
