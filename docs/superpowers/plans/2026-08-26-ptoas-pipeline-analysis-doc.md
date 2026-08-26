# PTOAS 编译流水线分析文档 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 撰写 `docs/ptoas_compilation_pipeline_analysis_zh.md`——面向新开发者的 PTOAS 编译流水线端到端深度分析文档(中文,按数据流组织,两后端分类法)。

**Architecture:** 单一 Markdown 文档,9 章 + 附录。素材来自已保存的侦察报告 `docs/superpowers/notes/2026-08-26-pipeline-recon.md`(下称 RECON),撰写时对引用做现场复核。每个任务 = 一章(或一组小章),循环节奏:起草 → 该章引用核对 → 提交。

**Tech Stack:** Markdown;核对用 Read/Grep 工具读取源码行;无代码改动。

**契约(全任务适用,下文不再重复):**
- 分类法铁律:`--pto-backend` 仅 `emitc|vpto`(tools/ptoas/ptoas.h:44-47);VMI 是 VPTO 后端内 IR 层;mix 是组合模式。任何表述不得违反。
- 引用格式:`path/to/file.cpp:123`(仓库相对路径,不带 PTOAS/ 前缀);禁止 `§` 式引用,所有文档内段落引用也写成 `file.md:行号`。
- 叙述节奏:每个编译阶段的叙述遵循统一结构 **输入 IR 形态 → 处理(Pass/转换)→ 输出 IR 形态**;IR 形态实例一律取自仓库真实测试文件(`test/lit/`、`test/samples/`、`test/vpto/cases/` 下),不虚构。
- 代码优先:README 与代码冲突处显式标注(已知两处:linalg 表述、level3 与 InsertSync)。
- 复核方法:写完一章后,对章内每个 file:line 引用,用 Read(offset=line-2, limit=6) 确认行号内容匹配;不匹配则修正行号。
- 每任务末尾提交,信息格式 `docs(pipeline-analysis): add chapter N ...`。

---

### Task 1: 文档骨架

**Files:**
- Create: `docs/ptoas_compilation_pipeline_analysis_zh.md`

- [ ] **Step 1: 创建骨架**

写入:一级标题、引言段(文档目的/读者/配套阅读链接 PTO_IR_manual.md、vpto-spec.md、RECON 中提到的专项设计文档)、章节 TOC(9 章 + 附录 A/B 占位)、每章空标题。文首注明"行号基于 2026-08-26 HEAD"。

- [ ] **Step 2: 核对骨架链接目标存在**

用 Glob 确认 `docs/PTO_IR_manual.md`、`docs/vpto-spec.md` 存在。

- [ ] **Step 3: Commit**

```bash
git add docs/ptoas_compilation_pipeline_analysis_zh.md
git commit -m "docs(pipeline-analysis): add document skeleton"
```

### Task 2: 第 1 章总览 + 三张图

**Files:**
- Modify: `docs/ptoas_compilation_pipeline_analysis_zh.md`(第 1 章)

素材:RECON §0、§1.2(注意 driver.cpp:1350-1363 有官方 ASCII 图可参考改绘)。

- [ ] **Step 1: 撰写总览**

覆盖:PTOAS 一句话定位(Python wrapper + 原生 `PTOASCompiler` 库,入口 `mlir::pto::runPTOAS()`,RECON §0/§1.1);软件栈位置图(前端框架 → PTODSL/`.pto`/PTOBC → ptoas → C++ source / fatobj → pto-isa/bisheng/CCE → NPU);两后端分叉总图(EmitC / VPTO,VMI 层标注在 VPTO 内部,mix 组合模式独立标注);关键概念速览表(PTO IR / VMI / VPTO / fatobj / PTOBC 各一行)。

- [ ] **Step 2: 绘制三张 ASCII 图**

图 1 软件栈位置;图 2 流水线主干(解析→公共 Pass→分叉);图 3 后端分叉细节。图中节点名称用真实函数名(loadInputModule / buildBackendInfo / compilePTOASModule / emitcPM / runVPTOBackendPipeline / emitVPTOBackendResult / emitFatobjLLVM / emitFatobjCCE / linkFatobjs)。

- [ ] **Step 3: 核对图引用的函数名与行号**(契约方法,重点 driver.cpp:1350-1363、1440-1447)

- [ ] **Step 4: Commit** `docs(pipeline-analysis): add ch1 overview and diagrams`

### Task 3: 第 2 章入口与驱动

**Files:**
- Modify: `docs/ptoas_compilation_pipeline_analysis_zh.md`(第 2 章)

素材:RECON §1 全部。

- [ ] **Step 1: 撰写章节**

覆盖:入口链四跳(wrapper→_cli→NativeModule→runPTOAS,RECON §1.1,含"ptoas.cpp:324 main 仅前向声明"的澄清);`runPTOASDriver` 七步调用链(§1.2);命令行选项表(照搬 RECON §1.3 表格,补充 `--emit-vpto-llvm-ir`、`--pto-print-seam-ir`、`--vpto-fix-vfsimt-size`);PTOBC 魔数与双格式输入。

- [ ] **Step 2: 抽样复核(本任务重点,spec §6 指定项)**

逐一 Read 核对:ptoas_wrapper.py:73-91、_cli.py:50-67、NativeModule.cpp:174-193、driver.cpp:131-134/1190-1247/1249-1296、ptoas.cpp:656-659、driver.cpp:278-293。特别验证 `--pto-backend` 确实仅接受两值。

- [ ] **Step 3: Commit** `docs(pipeline-analysis): add ch2 entry and driver`

### Task 4: 第 3 章前端入口

**Files:**
- Modify: `docs/ptoas_compilation_pipeline_analysis_zh.md`(第 3 章)

素材:RECON §4。

- [ ] **Step 1: 撰写章节**

覆盖:`.pto` 文本与 PTOBC 二进制(driver 解析分支);PTODSL tracing 机制(`@pto.jit` → AST 重写 → TracingRuntime.build_module → `_ops.py` 发 op),ModuleStyle 三布局与 child module 属性(混合后端消费形态,呼应第 5 章);PTODSL 编译原生库路径(native_build.py 子进程调 ptoas + bisheng 链接,`~/.cache/ptodsl/` 缓存);TileLang 集成(test/tilelang_st 三步流程 + 88 用例注册表);TileLib/SoftOps(lib/TileOps + TileLibService);PyPTO/CuTile 定位(Python 绑定 PTOModule.cpp 在本仓库,CuTile 为外部框架)。

- [ ] **Step 2: 核对引用**(重点:_tracing/runtime.py:80-97、module_builder.py:19-39/74-84、native_build.py:42-70/73-80、tilelang_st CMakeLists.txt:9-17)

- [ ] **Step 3: Commit** `docs(pipeline-analysis): add ch3 frontend entries`

### Task 5: 第 4 章公共阶段

**Files:**
- Modify: `docs/ptoas_compilation_pipeline_analysis_zh.md`(第 4 章)

素材:RECON §2 全部。

- [ ] **Step 1: 撰写章节**

覆盖:include/PTO 与 lib/PTO 组件地图;Pass 注册机制(TableGen→Passes.h.inc→registerPTOPasses,ptoas.cpp:357);`compilePTOASModule` 主流水线 12 步顺序表(每步:Pass 名 + 行号 + 职责一句话,素材 RECON §2.3);Pass 分类速览(规范化/内存规划/同步/TileFusion/TileOp 展开,RECON §2.2,不必逐 Pass 展开——细节引用 Passes.td);level 分档(level3 跳 PlanMemory 的准确表述 + README 冲突标注框);四种同步模式互斥表(InsertSync/BufidSync/BarrierAll/GraphSolver,ptoas.cpp:3723-3757);A5 专属 gate(bufid_sync/vpto-scheduler/op-fusion 要求 a5,ptoas.cpp:3395-3418)。

- [ ] **Step 2: 抽样复核(spec §6 指定项:level3 与 PlanMemory/InsertSync)**

Read 核对 ptoas.cpp:3699(level3 条件)、3504-3508(tassign 断言)、native_build.py:73-80;确认 README.md:275 表述与代码差异,写入冲突标注。

- [ ] **Step 3: 核对主流水线行号表**(12 步逐条 Read)

- [ ] **Step 4: Commit** `docs(pipeline-analysis): add ch4 common pipeline stages`

### Task 6: 第 5 章后端分叉(两后端 + mix)

**Files:**
- Modify: `docs/ptoas_compilation_pipeline_analysis_zh.md`(第 5 章)

素材:RECON §3。分 5.1/5.2/5.3 三小节。

- [ ] **Step 1: 撰写 5.1 EmitC 路径**

主流水线收尾 → emitcPM(createEmitPTOManualPass + FormEmitCExpressionsCompatPass + CSE)→ translateToCpp → marker 后处理;`pto/pto-inst.hpp` include 机制;linalg 冲突标注(README.md:11 vs 代码无 linalg pass)。

- [ ] **Step 2: 撰写 5.2 VPTO 路径(含 VMI 层)**

明确"VMI 是本路径内部 IR 层"定位;VMI 类型进入方式(_vmi_namespace.py / 手写 .pto);`appendVMISemanticPipeline` 15-pass 固定序列表(ptoas.cpp:3303-3338);VMIToVPTO 转换机制;VMI 语义细节不复述,显式链接 `docs/designs/vmi-introduction.md` 与 `docs/designs/vmi-implementation-manual.md`(按 spec §8 分工:本文只讲该层在流水线中的位置);`--emit-vpto` 三分支(IR 文本 / .ll / 对象模式);对象模式细节:双 LLVM 模块(cube/vector)→ VPTOLLVMEmitterDispatcher 按 CANN 版本分派 → host stub + bisheng 编 .o → ld.lld 合并 → cce-ld 合成 fatobj(五步,ObjectEmission.cpp:328-489);ABI 后缀规则(.vector/.cube vs _mix_aiv/_mix_aic,ObjectEmission.cpp:924-934);A5 调度器位置(prepareVPTOForEmission 中 CSE 后校验前,ptoas.cpp:3137-3143)。IR 形态实例可取 `test/lit/vmi_new/` 或 `test/vpto/cases/vmi_new/` 真实用例。

- [ ] **Step 3: 撰写 5.3 mix-backend 模式**

child module `pto.backend` 属性 → collectChildJobs → ChildJob 各自 fatobj → FatobjLinkJob `--cce-fatobj-link` 合并;引用 docs/designs/mix-kernel-mix-backend-compile-flow.md(分工原则:本文讲位置,细节见该文档)。

- [ ] **Step 4: 抽样复核(spec §6 指定项:--emit-vpto 分支、fatobj 发射调用关系)**

Read 核对 ptoas.cpp:3221-3269(三分支)、3303-3338(VMI 序列)、ObjectEmission.cpp:328-489(五步)、VPTOLLVMEmitterDispatcher.cpp:52-65、driver.cpp:959/1008-1035/1151-1188。

- [ ] **Step 5: Commit** `docs(pipeline-analysis): add ch5 backend forks`

### Task 7: 第 6 章路径对比总表

**Files:**
- Modify: `docs/ptoas_compilation_pipeline_analysis_zh.md`(第 6 章)

素材:RECON §1.3 + §2.3 的 gate 信息。

- [ ] **Step 1: 构建矩阵**

主表:arch(a2/a3/a5)× backend(emitc/vpto)× level(1/2/3),单元格值:✅ 可用 / ⚠️ 有条件(注条件)/ ❌ 不支持。附加列:典型产物(C++ source / fatobj / VPTO IR / .ll)。表下注证据引用(每行 gate 的 file:line)。已知 gate 数据:bufid_sync/vpto-scheduler/op-fusion 仅 a5;level3 跳 PlanMemory;`pto.tassign` 仅 level3;op-fusion 需 level≥2;vpto-scheduler 仅 A5 Vector kernel;非 A5 显式启用 vpto-scheduler 报错。不臆造矩阵单元——无法确证的组合标"待验证"。

- [ ] **Step 2: 核对矩阵涉及的每个 gate 行号**

- [ ] **Step 3: Commit** `docs(pipeline-analysis): add ch6 comparison matrix`

### Task 8: 第 7 章运行时与上板

**Files:**
- Modify: `docs/ptoas_compilation_pipeline_analysis_zh.md`(第 7 章)

素材:RECON §5。

- [ ] **Step 1: 撰写章节**

覆盖:pto-isa 外部依赖定位(PTO_ISA_PATH/PTO_ISA_ROOT,ObjectEmission.cpp:261-264);npu_validation 五件套生成与运行(generate_testcase.py,README.md:346-366);test/vpto 用例结构(ptoas.flags 约定、run_host_vpto_validation.sh 的 SIM/NPU 双模式);fatobj 上板链路(bisheng 三 TU → fatobj-link bundle.o → host g++ 链接,docs/kernel_side_wrapper_fatobj_link_guide_zh.md:227-275);direct-call ABI 符号要求(foo.vector/foo.cube)。

- [ ] **Step 2: 核对引用**

- [ ] **Step 3: Commit** `docs(pipeline-analysis): add ch7 runtime and board validation`

### Task 9: 第 8 章发布形态

**Files:**
- Modify: `docs/ptoas_compilation_pipeline_analysis_zh.md`(第 8 章)

素材:RECON §6。

- [ ] **Step 1: 撰写章节**

覆盖:双 release 线(ptoas-vX.Y / vmi-vA.B.C,build_wheel.yml:99-148 分派);ptoas-vmi staging 构建流程(prepare_source.py + pyproject.toml.patch,不改工作区);互斥安装警告;nightly wheel 机制;compiler-only tarball 非 PTODSL-capable 的提醒(README.md:180-182);版本号来源(CMakeLists.txt:47)。

- [ ] **Step 2: 核对引用**

- [ ] **Step 3: Commit** `docs(pipeline-analysis): add ch8 release forms`

### Task 10: 第 9 章上手建议 + 附录

**Files:**
- Modify: `docs/ptoas_compilation_pipeline_analysis_zh.md`(第 9 章、附录)

- [ ] **Step 1: 撰写第 9 章**

按验收标准组织(读者能定位每条路径的入口命令/分叉点/最终产物):建议阅读顺序(第 1 章→第 2 章→跑一个 smoke:ptoas test/lit/pto/empty_func.pto → 第 5 章→test/vpto 案例);首读文件清单(带一句话理由):tools/ptoas/driver.cpp、tools/ptoas/ptoas.cpp 的 compilePTOASModule、include/PTO/Transforms/Passes.td、ptodsl/ptodsl/_tracing/runtime.py、tools/ptoas/ObjectEmission.cpp;环境准备指引(引用 README §3-4,不复述)。

- [ ] **Step 2: 撰写附录**

附录 A:Pass 速查表(RECON §2.2 清单转表:Pass 名 / 类别 / 一句话职责 / Passes.td 行号);附录 B:术语表(PTO/VMI/VPTO/fatobj/PTOBC/TileLib/CCE/bisheng/AIV/AIC/seam IR 等)。

- [ ] **Step 3: 抽查附录 A 行号**

Pass 速查表是全文行号密度最高的部分(大量 Passes.td 行号来自 RECON 转抄,过期风险最高):随机抽 8 条,Read(include/PTO/Transforms/Passes.td, offset=行号-2, limit=6) 确认该行确为对应 Pass 定义;发现系统性偏移则全表重核。

- [ ] **Step 4: Commit** `docs(pipeline-analysis): add ch9 onboarding and appendices`

### Task 11: 全文引用校验

**Files:**
- Modify: `docs/ptoas_compilation_pipeline_analysis_zh.md`(修正失效引用)

- [ ] **Step 1: 提取全部 file:line 引用**

Grep 文档中正则 `[\w./-]+\.(cpp|cc|cxx|h|hpp|py|td|txt|sh|yml|yaml|toml|md|inc|flags|patch):\d+`,汇总去重(字符类与扩展名列表需覆盖带连字符/数字的文件名,如 vpto-spec-v0.3.md、pto-test-opt.cpp,以及 CMakeLists.txt、ptoas.flags 等)。

- [ ] **Step 2: 逐条核对**

每条用 Read(offset-2, limit=6) 验证内容匹配;失配则修正。抽 3 处行数多的引用(如 PTOToEmitC.cpp:13701)确认类/函数确实在该行附近。

- [ ] **Step 3: 修正后 Commit** `docs(pipeline-analysis): verify all file:line references`

### Task 12: 交叉链接与验收自查

**Files:**
- Modify: `docs/ptoas_compilation_pipeline_analysis_zh.md`(必要时)

- [ ] **Step 1: 验证文档内/外部链接**

所有指向 docs/、ptodsl/docs/ 的相对链接用 Glob 确认目标存在;TOC 锚点与章节标题一致。

- [ ] **Step 2: 按 spec §7 验收标准逐条自查**

- [ ] 两后端端到端可独立走通(入口命令→中间产物→最终产物);VMI 讲清进入方式与 pipeline 位置;mix 差异单独说明
- [ ] 矩阵覆盖 arch×backend×level,无效/有条件显式标注
- [ ] 每个提及的 Pass 有 file:line
- [ ] 引用全部核对(Task 11)
- [ ] 第 9 章满足:读者可定位每条路径的 (a) 入口命令 (b) 分叉点 (c) 最终产物

- [ ] **Step 3: 最终 Commit** `docs(pipeline-analysis): complete pipeline analysis document`

---

## 给执行者的提醒

- RECON 是素材而非真理:撰写各章时以现场复核的代码为准,RECON 行号偏差直接修正。
- 遇到 RECON 未覆盖且影响准确性的问题,宁可标"待验证"也不要编造。
- 文档语言:中文正文,代码标识符/Pass 名/选项/类型名保留英文原文。
- 不要在文档中引用 docs/superpowers/(内部工作材料)。
