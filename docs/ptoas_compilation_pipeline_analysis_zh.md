# PTOAS 编译管线全景解析

> 行号引用基于 2026-08-26 的 HEAD（对应提交 `a82e6cad`）；代码演进后行号可能漂移，请以符号名为准检索。

## 引言

本文档面向新加入团队的开发者，梳理 PTOAS 编译器从源码入口到产物输出的端到端管线：涵盖前端接入、公共编译阶段、两条后端路径（`emitc` 与 `vpto`）的分叉与汇合、运行时调用与上板验证，以及最终发布形态。阅读本文后，读者应能回答"一个 kernel 从写出到跑通，中间经历了什么"这一问题，并能据此定位自己感兴趣的模块在整条管线中的位置；也能分清一份 `.pto` 里的 tile 级 PTO、VMI、VPTO 微指令如何共存、各层支持范围、哪一层能喂 EmitC、哪一层必须走 vpto（3.2.1 / 3.2.2 / 6.6 / 6.7）。

### 配套阅读

- `docs/PTO_IR_manual.md` — PTO IR 语义参考（指令语义、shape/layout/dtype 约束等）
- `docs/vpto-spec.md` — VPTO 规格说明
- `docs/designs/vmi-introduction.md` — VMI（虚拟指令层）概念与动机
- `docs/designs/vmi-implementation-manual.md` — VMI 实现手册
- `docs/designs/mix-kernel-mix-backend-compile-flow.md` — mix-kernel / mix-backend 编译流程

## 目录

（目录为纯文字索引，不做页内锚点链接；各章标题与目录文字保持一致。）

1. 总览（PTOAS 定位、软件栈位置、两后端一图流）
2. 入口与驱动（tools/ptoas/ CLI 驱动、选项分派）
3. 前端入口（PTODSL、TileLang、PyPTO、.pto 文本/PTOBC；3.2.1 三层 IR / 3.2.2 支持范围）
4. 公共阶段（解析/验证、Pass 管线编排、level 分档）
5. 后端分叉（5.1 EmitC 路径 / 5.2 VPTO 路径含 VMI 层 / 5.3 mix-backend）
6. 路径对比总表（arch × backend × level 矩阵；6.6 IR 层 × 后端；6.7 三层能力）
7. 运行时与上板（pto-isa 调用、npu_validation、fatobj 链接）
8. 发布形态（ptoas wheel / ptoas-vmi wheel / tarball）
9. 新开发者上手建议

附录 A：Pass 速查表
附录 B：术语表

## 1. 总览（PTOAS 定位、软件栈位置、两后端一图流）

### 1.1 PTOAS 是什么

一句话定位：**ptoas 是一个 Python 包装的原生编译器**——`ptoas` 命令行入口只是一层很薄的 Python wrapper，真正的编译逻辑全部位于原生共享库 `PTOASCompiler`（编译器本体与 PTO dialect、MLIR CAPI 实现都住在这个库里，对应 Python 侧的 `ptoas._core` 模块），统一的原生入口是 `mlir::pto::runPTOAS()`。

从命令行敲下 `ptoas` 到进入 C++ 世界，调用链只有四跳：

1. `tools/ptoas/ptoas_wrapper.py:73` `main()`：校验 Python 版本、装配资源路径后 `from ptoas import _cli`；
2. `ptodsl/ptoas/_cli.py:50` `launch()`：加载原生模块 `ptoas._core`，拼装 argv；
3. `tools/ptoas/NativeModule.cpp:174` `runPTOASFromPython()`：pybind11 绑定，释放 GIL 后调用 `mlir::pto::runPTOAS()`；
4. `tools/ptoas/driver.cpp:1440` `runPTOAS()`：完成"解析 → 后端分派 → job 编排"的驱动逻辑，核心 Pass 管线则在 `compilePTOASModule()`（见 1.3/1.4 与图 2、图 3）。此处两份源码的职责划分可记成一句：**driver.cpp 管调度，ptoas.cpp 管编译**（构建与模块装配细节留到第 2 章）。

### 1.2 软件栈位置（图 1）

ptoas 在整个 PTO 软件栈中处于"前端 DSL 与下游工具链之间"的编译器位置：上游接收前端产出的 `.pto` 文本或 PTOBC 二进制，下游把 `emitc` 路径的 C++ 源码交给 pto-isa C++ 头文件库使用、把对象产物交给 BiSheng（华为设备侧编译器）的 CCE（BiSheng 的 CCE 前端模式，`-xcce`）工具链，最终运行在 Ascend NPU 上。

图 1：ptoas 在软件栈中的位置

```text
+--------------------------------------------------------+
|  Frontend DSLs / frameworks                            |
|  TileLang          PyPTO          PTODSL (@pto.jit)    |
+--------------------------------------------------------+
                                  |
                                  |  .pto text / PTOBC binary
                                  v
+---------------------------------------------------------------------+
| ptoas  (this repo)                                                  |
|                                                                     |
|   +----------------------------------------------------------+      |
|   | Python thin wrapper: tools/ptoas/ptoas_wrapper.py        |      |
|   |   main() -> from ptoas import _cli; _cli.launch()        |      |
|   +----------------------------------------------------------+      |
|                                |                                    |
|                                v                                    |
|   +----------------------------------------------------------+      |
|   | native shared lib: PTOASCompiler  (Python "ptoas._core") |      |
|   |   runPTOASFromPython() -> mlir::pto::runPTOAS()          |      |
|   |   runPTOASDriver(): parse -> backend fork -> jobs        |      |
|   |   core pass pipeline: compilePTOASModule()               |      |
|   +----------------------------------------------------------+      |
|                                                                     |
+---------------------------------------------------------------------+
              |                               |
        emitc |                               | vpto
              v                               v
+---------------------------+      +---------------------+
| C++ source (.cpp)         |      | fatobj (-o)         |
| pto-isa: pto/pto-inst.hpp |      | (linked device obj) |
+---------------------------+      +---------------------+
              |                               |
              v                               v
+-----------------------------------------------+
| BiSheng / CCE toolchain                       |
|   -xcce device compile, BiSheng cc1 stub      |
|   fatobj compile; --cce-fatobj-link (mix)     |
+-----------------------------------------------+
                        |
                        v
+-----------------------------+
| Ascend NPU  (a2 / a3 / a5)  |
+-----------------------------+
```

两条出口的含义：

- `emitc` 出口产出 **C++ 源码**。生成的 C++ 通过 `#include "pto/pto-inst.hpp"` 调用设备内建（见 `lib/PTO/Transforms/PTOToEmitC.cpp:13770`），即依赖 **pto-isa C++ 头文件库**；该源码后续由 BiSheng 以 `-xcce` 模式编译（`tools/ptoas/ObjectEmission.cpp:589`）。
- `vpto` 出口产出 **fatobj**（fat object，含设备侧代码的 `-o` 产物）。ptoas 在内部完成 VPTO → LLVM IR 的下降，再由 `compileHostStubToFatobj`（`tools/ptoas/ObjectEmission.cpp:1147`）调 BiSheng cc1（`-fcce-fatobj-compile`）合成单个 fatobj。`--cce-fatobj-link` 只出现在"链接多个 fatobj"的阶段：mix 模式由 `linkFatobjs`（`tools/ptoas/ObjectEmission.cpp:1177`）调用（flag 见 `tools/ptoas/ObjectEmission.cpp:821`），tilelang_st 等宿主侧链接同样使用它。

### 1.3 编译流水线主干（图 2）

`runPTOASDriver()`（`tools/ptoas/driver.cpp:1364`）的主干只有六步：读输入 → 解析成 PTO IR → 构建后端信息 → 编排编译 job → 每个 job 跑 `compilePTOASModule()` → 写出产物。其中第四步出现第一处分叉：单后端走 `EmitCBackendJob` / `VPTOBackendJob`，混合模式（mix）按子模块的 `pto.backend` 属性拆成一组 child job。

图 2：流水线主干（解析 → job 编排 → 每 job 编译与发射）

```text
      .pto (text) / PTOBC (binary)
                     |
                     v
         +----------------------+
         | readInputBuffer      |
         +----------------------+
                     |   <- tools/ptoas/driver.cpp:136
                     v
         +----------------------+  text  -> parseTextualModule (driver.cpp:187)
         | loadInputModule      |  PTOBC -> decodePTOBCModule  (driver.cpp:167)
         |                      |  magic "PTOBC\0" check      (driver.cpp:131)
                     |   => PTO IR (ModuleOp)
                     v
         +----------------------+  parseDriverBackend: emitc|vpto (driver.cpp:278)
         | buildBackendInfo     |  resolveSingleBackend (driver.cpp:1190):
         |                      |    single backend  /  mixed mode
                     |
                     v
         +----------------------+
         | runPTOASJobs         |   <- tools/ptoas/driver.cpp:1298
         +----------------------+
                      |
         +------------+---------------------------+
         |                                        |
 single  |                                        | mixed: per-child
 backend |                                        | "pto.backend" attr
 jobs    |                                        |
         v                                        v
    +----------------------+     +----------------------+  collectChildJobs
    | each job:            |     | each child job:      |  (driver.cpp:1151)
    |  compilePTOASModule  |     |  compilePTOASModule  |  FatobjLinkJob
    |  (ptoas.cpp:3355)    |     |  (ptoas.cpp:3355)    |  (driver.cpp:1008)
    +----------------------+     +----------------------+
                 |                             |
                 v                             v
    +-----------------------------+  +------------------------------+
    | Text: writeTextOutput       |  | fatobj: emitted by jobs      |
    | (driver.cpp:1336) -> .cpp   |  | to -o (Fig. 3 detail)        |
    +-----------------------------+  +------------------------------+
```

主干各步的要点（细节留到第 2、4 章）：

- `loadInputModule`（`tools/ptoas/driver.cpp:223`）按输入魔数二选一：缓冲区以 `"PTOBC\0"` 开头（`tools/ptoas/driver.cpp:131`）则走 `decodePTOBCModule`（`tools/ptoas/driver.cpp:167`）解码二进制，否则走 `parseTextualModule`（`tools/ptoas/driver.cpp:187`）解析 `.pto` 文本，产出统一的 PTO IR（MLIR `ModuleOp`）。
- `buildBackendInfo`（`tools/ptoas/driver.cpp:1249`）解析 `--pto-backend` 并结合模块属性判定编译形态：单后端（emitc 或 vpto）或混合模式。
- `runPTOASJobs`（`tools/ptoas/driver.cpp:1298`）是 job 编排中枢；混合模式下 `collectChildJobs`（`tools/ptoas/driver.cpp:1151`）为每个子模块构造 child job，最后由 `FatobjLinkJob` 链接。
- 文本类结果统一由 `writeTextOutput`（`tools/ptoas/driver.cpp:1336`）写出；fatobj 类结果由各 job 直接落在 `-o`。

### 1.4 两后端分叉与 mix 组合模式（图 3）

先立一条铁律：**`--pto-backend` 只接受 `emitc|vpto` 两个值**。枚举定义见 `tools/ptoas/ptoas.h:44`（`enum class PTOBackend { EmitC, VPTO }`），选项声明见 `tools/ptoas/ptoas.cpp:656`，解析见 `tools/ptoas/driver.cpp:278` `parseDriverBackend`。因此 ptoas 只有两条后端路径。**VMI 不是第三条后端**：它是 VPTO 后端内部的 IR 层（`pto.vmi.*`），VMI → VPTO 语义管线在 VPTO 后端中始终启用（`README.md:278`）。mix/mixed 也不是第三条后端：它是"每个子模块可以用不同 `pto.backend` 属性"的组合编译模式，各子模块仍然分别走 emitc 或 vpto。

图 3：后端分叉细节（driver job 编排 → 每 job 调 `compilePTOASModule` 与发射函数）

```text
+------------------------------------------------------------------------------+
| DRIVER layer (tools/ptoas/driver.cpp) -- runPTOASJobs (1298) orchestrates    |
| jobs; every job CALLS compilePTOASModule() below, then an emission helper:   |
|   single: EmitCBackendJob::run (1037)  /  VPTOBackendJob::run (1070)         |
|   mixed : EmitCBackendChildJob (907)   /  VPTOBackendChildJob (959)          |
+------------------------------------------------------------------------------+
                                 | call
                                 v
+------------------------------------------------------------------------------+
| compilePTOASModule  <- tools/ptoas/ptoas.cpp:3355                            |
| common stages: input validation, --pto-level/--pto-arch checks,              |
| backend-independent passes                                                   |
+------------------------------------------------------------------------------+
                | (emitc)                          | (vpto)
                v                                  v
  +--------------------------+             +--------------------------------------+
  | emitcPM                  |             | runVPTOBackendPipeline               |
  | tools/ptoas/ptoas.cpp:   |             | tools/ptoas/ptoas.cpp:3271           |
  |   3833                   |             | PTO -> VMI -> VPTO lowering          |
  | EmitPTOManual (A3/A5)    |             | +----------------------------------+ |
  | + EmitC lowering + CSE   |             | | VMI layer lives HERE: pto.vmi.*  | |
  +--------------------------+             | | layout assignment + vmi-to-vpto, | |
                                           | | always enabled inside VPTO       | |
                                           | +----------------------------------+ |
                                           +--------------------------------------+
                |                                  |
                v                                  v
  +--------------------------+             +--------------------------------------+
  | Text result: C++ source  |             | emitVPTOBackendResult                |
  | #include "pto/pto-inst   |             | tools/ptoas/ptoas.cpp:3221           |
  |  .hpp" (pto-isa C++ API) |             | -> VPTOObject: Cube/Vector LLVM      |
  +--------------------------+             |    modules + host stub source        |
                                           +--------------------------------------+
                |                                  |
                v                                  v
  +--------------------------+             +--------------------------------------+
  | back in driver after                    |             | back in job::run:                    |
  |  job::run: writeTextOutput               |             |  emitVPTOLLVMFatobj (driver.cpp:1128)|
  |  (driver.cpp:1336)                       |             |  -> emitFatobjLLVM                   |
  |  -> .cpp on disk                         |             |     (ObjectEmission.cpp:1117)        |
  +--------------------------+             |  -> ONE fatobj at -o (BiSheng cc1    |
                                           |     stub compile; compileHostStubTo- |
                                           |     Fatobj, ObjectEmission.cpp:1147) |
                                           +--------------------------------------+

 mixed mode (per-child "pto.backend" attr) -- child jobs call the same pipeline:
   emitc child: EmitCBackendChildJob (driver.cpp:907)
                  -> compilePTOASModule -> emitFatobjCCE (ObjEmis.:1012)
                  -> temp fatobj (BiSheng -xcce device codegen)
   vpto child:  VPTOBackendChildJob (driver.cpp:959)
                  -> compilePTOASModule -> emitVPTOLLVMFatobj -> emitFatobjLLVM
                  -> temp fatobj (BiSheng device codegen)
   link stage:  FatobjLinkJob::run (driver.cpp:1008)
                  -> linkFatobjs (ObjectEmission.cpp:1177)
                  -> --cce-fatobj-link merges fatobjs -> final fatobj at -o
```

（图中 `ptoas.cpp`、`driver.cpp`、`ObjectEmission.cpp` 均指 `tools/ptoas/` 目录下同名文件。）

读图 3 时注意五个层次：

- **driver job 编排**（顶部）：`runPTOASJobs`（`tools/ptoas/driver.cpp:1298`）按后端形态挑选并执行 job——单后端走 `EmitCBackendJob::run` / `VPTOBackendJob::run`（行号见图），混合模式为每个子模块构造 child job。**job 层不在 `compilePTOASModule` 内部**：每个 job 先调用 `compilePTOASModule` 跑 Pass 管线，再调用各自的发射函数落盘。
- **公共阶段**：`compilePTOASModule`（`tools/ptoas/ptoas.cpp:3355`）先做输入校验、`--pto-level` / `--pto-arch` 合法性检查等与后端无关的步骤，然后按 effective backend 分叉。
- **emitc 路径**（左）：`emitcPM`（`tools/ptoas/ptoas.cpp:3833`）跑 EmitC 专属 Pass（`EmitPTOManual`、EmitC lowering、CSE），产出文本形式的 C++ 源码；单后端时由 driver 调 `writeTextOutput` 落盘（`tools/ptoas/driver.cpp:1336`）。
- **vpto 路径**（右）：`runVPTOBackendPipeline`（`tools/ptoas/ptoas.cpp:3271`）完成 PTO → VMI → VPTO 的下降（VMI 层就在这个管线内部，见嵌套框标注）；`emitVPTOBackendResult`（`tools/ptoas/ptoas.cpp:3221`）随后把 VPTO lower 成 Cube/Vector（AIC/AIV，即 cube 核 / vector 核）两个 LLVM module（外加可选 host stub 源码），最终经 `emitVPTOLLVMFatobj` → `emitFatobjLLVM`（行号见图）生成单个 fatobj——host stub 经 BiSheng cc1（`-fcce-fatobj-compile`）编译，不走 `--cce-fatobj-link`。
- **mix 组合模式**（底部，独立标注）：当模块按 `pto.backend` 属性拆分时，emitc 子模块经 `EmitCBackendChildJob` → `emitFatobjCCE`（`tools/ptoas/ObjectEmission.cpp:1012`）产临时 fatobj，vpto 子模块经 `VPTOBackendChildJob` → `emitFatobjLLVM` 产临时 fatobj，最后 `FatobjLinkJob::run` 调 `linkFatobjs`（`tools/ptoas/ObjectEmission.cpp:1177`）用 `--cce-fatobj-link` 把所有 fatobj 链接成最终 `-o` 产物。

`tools/ptoas/driver.cpp:1350` 起的注释里还有一张官方 ASCII job 示意图（EmitC job / VPTO job / child jobs / Fatobj link job 的并排画法，至 `tools/ptoas/driver.cpp:1363` 结束），本文图 2、图 3 即在其基础上改绘并补充函数名与行号。

### 1.5 关键概念速览

| 概念 | 一句话说明 |
| --- | --- |
| PTO IR | ptoas 的输入与核心中间表示：**一个** MLIR 方言 `pto`（不是三个 dialect）。tile 级、VMI、VPTO 微指令都是这个 dialect 里的不同粒度层，写在同一份 `.pto` / 同一个 `ModuleOp` 里。走哪条后端只看 `--pto-backend`（见 3.2.1 / 6.6）；各层能表达什么见 3.2.2 / 6.7。tile **不会**自动降到 VMI（3.2.1）。 |
| VMI | VPTO 后端内部的虚拟向量指令层（`pto.vmi.*`）：表达"逻辑向量"语义，由 layout assignment 决定如何映射到物理 vector register；不是独立的 CLI 后端。 |
| VPTO | 两层含义不要混：① **微指令 IR**（`pto.vdup` / `pto.vlds` / `pto.vsts` 等，类型 `!pto.vreg` / `!pto.mask`）；② **CLI 后端名** `--pto-backend=vpto`。vpto 路径把上述 IR lower 为 LLVM IR 再交 BiSheng。 |
| fatobj | 含设备侧代码（及 host stub）的 fat object 文件，是 `-o` 的对象类产物；vpto 单后端与 mix 模式都产出 fatobj。 |
| PTOBC | PTO Bytecode：`.pto` 文本的二进制序列化格式，文件头 magic 为 `"PTOBC\0"`，由 `decodePTOBCModule` 解码。 |

## 2. 入口与驱动（tools/ptoas/ CLI 驱动、选项分派）

第 1 章用图 1／图 2 给出了驱动层的骨架；本章把骨架填满：先解剖 `ptoas` 命令的真实形态与四跳启动链（概览见 1.1），再按源码顺序展开 `runPTOASDriver()` 的七步流程、双格式输入解析、单/混合后端判定与 job 编排，最后给出命令行选项速查表。

### 2.1 启动链（`ptoas` 命令的真身）

先澄清一个容易误导新人的细节：`tools/ptoas/ptoas.cpp:324` 的 `int main(int argc, char **argv);` **只是一条前向声明**，全仓库不存在它的定义——在 `tools/ptoas/` 下按 `^int main\(` 检索仅此一处。这是历史遗留：CMake 从不构建独立的 ptoas 可执行文件。`tools/ptoas/CMakeLists.txt:110` 的 `PTOASCompilerImplementation` 是一个 OBJECT 库（聚合全部编译器源码与 Python 绑定入口 `PTOModule.cpp`），唯一的扩展产物是 `tools/ptoas/CMakeLists.txt:158` 的 `PTOASPythonCore`——即 Python 模块 `ptoas._core`。仓库里真正的独立 C++ 可执行工具还有几个，典型如 `tools/ptobc/src/main.cpp:126`（PTOBC 编解码工具）与 `tools/pto-test-opt/` 下的 pass runner / 调度器与 VFSIMT patcher 测试工具（`pto-test-opt.cpp:25` 等），它们都是辅助工具，不在 ptoas 主编译链上。

因此 `ptoas` 命令的真身是一个由 CMake 配置生成的 Python 脚本。`ptoas_wrapper.py` 会被配置两遍（`tools/ptoas/CMakeLists.txt:36-53`）：构建树版本注入绝对路径的 Python root；安装版本改用相对 `bin/` 的 wrapper-relative root——CMake 安装前缀可能在安装时被改写，不能把 configure 期前缀烧死。安装时后者以 `bin/ptoas` 落盘（`tools/ptoas/CMakeLists.txt:231`）。

四跳启动链逐步展开（第 1 章只给了行号索引，见 1.1）：

1. **wrapper**：`tools/ptoas/ptoas_wrapper.py:73` `main()` 先做解释器门禁——若归档附带 Python 版本要求文件，实际解释器版本不符就直接 `SystemExit`（`ptoas_wrapper.py:75-86`）；随后 `_add_configured_python_root()` 把生成期注入的 Python root 挂进模块搜索路径，`_disable_editable_import_redirects()` 排除 editable 安装的重定向干扰（`ptoas_wrapper.py:87-88`）；最后 `from ptoas import _cli` 并 `raise SystemExit(_cli.launch(sys.argv[1:], wrapper=wrapper))`（`ptoas_wrapper.py:89-91`）。
2. **launcher**：`ptodsl/ptoas/_cli.py:50` `launch()` 加载原生模块 `ptoas._core`，解析随包分发的 TileOps 资源目录；把 wrapper 路径写入环境变量 `PTOAS_BIN` 并用作 argv[0]（`_cli.py:55-57`）；把 TileOps 的 Python root 临时插到 `sys.path` 头部，`try/finally` 保证用完移除（`_cli.py:59-67`）；随后 `int(native_module.main(argv))` 进入 C++ 世界。
3. **pybind11 绑定**：`tools/ptoas/NativeModule.cpp:174` `runPTOASFromPython()` 把 `std::vector<std::string>` 重排成 `char **` argv；在 Python 侧实例化 `ptoas.mlir.ir.Context` 再 cast 成 `MlirContext`（`NativeModule.cpp:182-184`）——**MLIR Context 的所有权留在 Python**，编译结束后 Python 侧仍可持有、检视 IR 对象；之后 `py::gil_scoped_release` 释放 GIL 再调用 `mlir::pto::runPTOAS()`（`NativeModule.cpp:187-191`），长编译不阻塞其他 Python 线程。
4. **driver**：`tools/ptoas/driver.cpp:1440` 与 `driver.cpp:1444` 的两个 `runPTOAS()` 重载（带/不带借入 Context）都直接转发 `runPTOASDriver()`（`driver.cpp:1364`）。

"宿主是 Python" 还带来一个工程细节：一个 Python 进程可能反复调用驱动，因此 `runPTOASDriver` 每次解析命令行前先 `llvm::cl::ResetAllOptionOccurrences()` 把所有已注册 LLVM 选项恢复默认值（`driver.cpp:1374-1377`），否则上一次调用的选项值会泄漏到下一次。

### 2.2 `runPTOASDriver` 七步

第 1 章图 2 把主干概括为六步；按源码顺序展开是七步（把"注册与选项解析"从"读输入"前单列出来）：

| 步 | 做什么 | 关键代码（`tools/ptoas/driver.cpp`） |
| --- | --- | --- |
| 1 | 注册 dialect 与 Pass/CLOption | 1367、1371（实现 `ptoas.cpp:326-362`） |
| 2 | 解析命令行与 CANN 版本 | 1377-1389 |
| 3 | 读输入缓冲区 | 1403（`readInputBuffer` 136-144） |
| 4 | 装载输入模块 | 1409-1413（`loadInputModule` 223-276） |
| 5 | 判定后端形态 | 1416-1420（`buildBackendInfo` 1249-1296） |
| 6 | 编排并执行编译 job | 1425（`runPTOASJobs` 1298-1334） |
| 7 | 写出产物 | 1429-1437（`writeTextOutput` 1336-1348） |

逐步说明：

1. **注册**：`registerPTOASDialects()`（实现 `tools/ptoas/ptoas.cpp:326-345`）插入 func/tensor/arith/memref/affine/cf/bufferization/scf/math 等 MLIR 通用 dialect，加上 `pto`、`emitc`、LLVM 三个关键 dialect，并注册 bufferization 外部模型；`registerPTOASPassesAndCLOptions()`（`ptoas.cpp:347-362`）注册 MLIR 各包 Pass 与 `registerPTOPasses()`（`ptoas.cpp:357`）注册的 PTO 自有 Pass，最后 `registerPassManagerCLOptions()` 让 `--mlir-print-ir-after-all` 之类调试开关可用。若从 Python 借入 Context，registry 直接 append 进去（`driver.cpp:1368-1370`）。
2. **解析命令行**：注意 `driver.cpp:1379-1380` 在 `ParseCommandLineOptions`（`driver.cpp:1382`）**之前**就用 `hasCLIOption()` 探测 `--pto-arch` / `--pto-backend` 是否被显式传入。"显式传了默认值"与"没传"在这两处语义不同：arch 未传时才会从文本输入刮取 `pto.target_arch`（见 2.3）；backend 未传时才会进入 mix 判定（见 2.4）。`--version` 由 `SetVersionPrinter` 挂上（`driver.cpp:1372`）；`--cann-output-version` 随后单独解析（`driver.cpp:1384-1389`）。
3. **读输入**：`readInputBuffer()`（`driver.cpp:136-144`）基于 `getFileOrSTDIN`，输入路径传 `-` 即读 stdin。
4. **装载模块**：`loadInputModule()` 把文本或 PTOBC 统一成 PTO IR `ModuleOp`，细节见 2.3。
5. **判定后端形态**：`buildBackendInfo()` → `resolveSingleBackend()` 决定"单后端还是 mix"，细节见 2.4。
6. **编排执行 job**：单后端一个 job，mix 一组 child job 加链接 job，细节见 2.4。
7. **写产物**：Text 结果经 `writeTextOutput()`（`driver.cpp:1336`）落 `-o`；`MixedObject` 直接返回 0——fatobj 已在 job 内部写到 `-o`（`driver.cpp:1432-1433`）。

步骤 5／6 之间还有一处容易被忽略的环境准备：`PTOASContext` 持有输出路径、CANN 版本覆盖与 VFSIMT 修复模式（`driver.cpp:1391-1401`），`initializeEnvironment()`（`driver.cpp:1421`）在需要外部工具链（vpto 单后端未开 IR dump，或 mix 模式）时定位 BiSheng/CANN 工具。

### 2.3 输入解析：文本与 PTOBC

双格式输入在 `loadInputModule`（`driver.cpp:223-276`）入口处分叉，判据是 6 字节魔数：

- **魔数判定** `isPTOBCBuffer`（`driver.cpp:131-134`）：缓冲区长度 ≥ 6 且前 6 字节为 `"PTOBC\0"`。
- **PTOBC 二进制分支**（`driver.cpp:230-237`）：arch 只认 `--pto-arch`（二进制内已有结构化信息，无需也无法从文本刮取），显式传入时校验 `a2|a3|a5`；`decodePTOBCModule`（`driver.cpp:167-185`）转调 `ptobc::decodePTOBCToModule`，开启异常编译时 try/catch、否则判空，失败统一报 "Failed to decode PTOBC"。
- **`.pto` 文本分支**（`driver.cpp:238-242`）：
  - `resolveTextInputArch`（`driver.cpp:146-165`）：CLI 显式指定时校验三值；未指定时用正则在**原始文本**上刮 `pto.target_arch = "..."`（`detectPTOASTextualModuleArch`，`driver.cpp:119-129`）——刮取发生在真正 parse 之前，因为 parser 需要先知道目标 arch 才能判定方言合法性；刮不到或不支持则回落 `a3`。
  - `parseTextualModule`（`driver.cpp:187-221`）：`ScopedPTOParserTargetArch` 把 parser 置于 A5 或 A3 模式（`driver.cpp:192-194`）；`parseAsmSourceFile` 携带 `AsmParserState` 以恢复文本里的 SSA 名字，`applyTextualNameHintsToModule`（`driver.cpp:219`）把它们挂到 Location 上——这些名字提示会在 EmitC 发射前重新贴回（收集点 `tools/ptoas/ptoas.cpp:3377-3380`），让生成的 C++ 变量名贴近用户源码。
- **汇合**：两条分支产出统一的 `ModuleOp` 后，`loadInputModule` 统一回写 `pto.target_arch` 属性（`driver.cpp:248-269`）——CLI 值优先，其次模块自带属性（合法才采纳），否则兜底 `a3` 写回；最后 `mlir::verify` 整体校验（`driver.cpp:271-274`）。

PTOBC 的编解码实现位于 `tools/ptobc/`，配套独立 CLI（`tools/ptobc/src/main.cpp:126`）与 roundtrip 测试数据 `tools/ptobc/testdata/`。

### 2.4 后端判定与 job 编排

先复述第 1 章 1.4 的铁律（本章从 driver 视角再证一次）：`--pto-backend` **只接受 `emitc|vpto`**。`parseDriverBackend`（`driver.cpp:278-293`）大小写不敏感地只认这两个字符串，其余一律返回 false，`buildBackendInfo` 据此报 "Expected 'emitc' or 'vpto'"（`driver.cpp:1255-1256`）；模块属性 `pto.backend` 走同一个解析器（`parseDriverBackendAttr`，`driver.cpp:295-318`，非法值报错于 `driver.cpp:311-313`）。VMI 是 vpto 管线内部的 IR 层、mix 是组合编译模式，都不是第三后端（见 1.4）。

**单后端还是 mix？** `buildBackendInfo`（`driver.cpp:1249-1296`）先解析默认后端，CLI 未指定时才读模块属性（`driver.cpp:1261-1265`），然后交给 `resolveSingleBackend`（`driver.cpp:1190-1247`）裁决，规则按优先级：

1. CLI 显式指定：默认单后端；例外是"非 IR dump 模式 + 外层含多个子模块 + 外层只含子模块"（`isBackendPartitionedContainer`，`driver.cpp:320-327`）时降级为 mix（`driver.cpp:1196-1204`）——即 `--pto-backend` 不能把一个 backend-partitioned 容器强行压成单后端对象编译。
2. 模块属性指定：单后端（`driver.cpp:1205-1208`）。
3. 都没有：多个子模块且是 partitioned 容器则 mix（`driver.cpp:1210-1220`；容器里混有非 ModuleOp 顶层 op 则报错，`driver.cpp:1212-1218`）；否则逐子模块看属性（`driver.cpp:1222-1238`）——全部一致取之为单后端，出现分歧则 mix。

mix 模式另有两条件硬约束：不支持任何调试 IR 输出 flag（`driver.cpp:1281-1287`），必须显式 `-o`（`driver.cpp:1288-1292`）。工具链需求 `requiresToolchain`：vpto 单后端在未开任何 IR dump 时为 true（`driver.cpp:1273-1277`），mix 恒为 true（`driver.cpp:1294`）。

**job 编排**：`runPTOASJobs`（`driver.cpp:1298-1334`）按判定结果二选一：

- **单后端**：`EmitCBackendJob::run`（`driver.cpp:1037-1068`）或 `VPTOBackendJob::run`（`driver.cpp:1070-1126`）。两者都先把 `pto.backend` 属性写回模块；若输入是"仅含单个子模块的 partitioned 容器"且非 IR dump 模式，会先用 `buildBackendChildCompileUnit` 归一化出子模块编译单元（EmitC：`driver.cpp:1044-1055`；VPTO：`driver.cpp:1082-1095`——PTODSL 即使只有一个子模块也会产 partitioned 容器）。EmitC job 要求结果是 Text（`driver.cpp:1063-1066`）；VPTO job 允许 Text（配合 `--emit-vpto` 等）或 VPTOObject——后者要求显式 `-o`（`driver.cpp:1111-1115`），经 `emitVPTOLLVMFatobj`（`driver.cpp:1128`）→ `emitFatobjLLVM` 落盘后把 result 置为 `MixedObject`（`driver.cpp:1123-1124`），driver 对它直接返回成功。
- **mix**：`collectChildJobs`（`driver.cpp:1151-1188`）为每个子模块构造编译单元并选定 effective backend（CLI 覆盖 > 子模块属性 > 默认，`driver.cpp:1175-1177`）：vpto → `VPTOBackendChildJob`（`driver.cpp:959`），否则 `EmitCBackendChildJob`（`driver.cpp:907`）。每个 child job 照常调用 `compilePTOASModule`，再把产物编成**临时 fatobj**（emitc 子模块走 `emitFatobjCCE`，`driver.cpp:942-947`；vpto 子模块走 `emitVPTOLLVMFatobj`，`driver.cpp:992`）。最后 `FatobjLinkJob::run`（`driver.cpp:1008-1031`）要求至少两个 fatobj（`driver.cpp:1014-1017`）并调 `linkFatobjs` 链接到 `-o`。

调试提示：环境变量 `PTOAS_DEBUG_CHILD_UNIT=1` 会把每个 child 编译单元 dump 到 stderr（`driver.cpp:1169-1173`）。`tools/ptoas/driver.cpp:1350-1363` 的源码注释里还有一张官方 ASCII job 示意图（EmitC job / VPTO job / child jobs / Fatobj link job 并排画法），与本章文字一一对应。

### 2.5 命令行选项速查表

除 `-o` 与位置参数输入定义在 `tools/ptoas/driver.cpp:57-63` 外，下表选项都定义在 `tools/ptoas/ptoas.cpp` 的 `cl::opt` 区块（`ptoas.cpp:510-714`，约 32 个），随共享库进程注册。本表是影响流水线形态的核心子集（13 项），其余多为单个 Pass 的微调开关；"定义"列给声明行号，"生效点与要点"列给消费位置（默认值以代码为准）：

| 选项 | 取值（默认） | 定义 | 生效点与要点 |
| --- | --- | --- | --- |
| `--pto-arch` | `a2|a3|a5`（`a3`） | `ptoas.cpp:644` | 回写 `pto.target_arch`（`ptoas.cpp:3404`）；A2/A3 用 `dav-c220-vec` march（`ptoas.cpp:3215-3217`）并走 `LowerPTOToUBufOps` 的提前收尾路径（`ptoas.cpp:3157-3163`）；文本输入未显式指定时可从模块刮取（见 2.3） |
| `--pto-level` | `level1|level2|level3`（`level2`） | `ptoas.cpp:650` | 解析校验 `ptoas.cpp:3389-3394`（`parseBuildLevel` `ptoas.cpp:726-744`）；level3 跳过 PlanMemory（`ptoas.cpp:3699`）且要求 alloc 显式带 `addr`（`ptoas.cpp:3548-3568`）；`pto.tassign` 仅 level3（`ptoas.cpp:3498-3502`） |
| `--pto-backend` | **仅 `emitc|vpto`**（`emitc`） | `ptoas.cpp:656` | 解析 `driver.cpp:278-293`；job 分派 `driver.cpp:1302-1309`、child 分派 `driver.cpp:1178-1185`；vpto → `runVPTOBackendPipeline`（`ptoas.cpp:3794-3815`），emitc → `emitcPM`（`ptoas.cpp:3833-3849`） |
| `--emit-vpto` | bool（关） | `ptoas.cpp:661` | `emitVPTOBackendResult` 把最终 VPTO IR 文本写到 `-o`（`ptoas.cpp:3224-3231`） |
| `--emit-vpto-llvm-ir` | bool（关） | `ptoas.cpp:666` | `lowerVPTOModuleToLLVMIRText` 输出 `.ll` 文本（`ptoas.cpp:3233-3244`） |
| `--vpto-scheduler` | `off|analyze|on`（`off`） | `ptoas.cpp:514` | 在 `prepareVPTOForEmission` 尾段、发射校验 pass 前插入调度 pass（`ptoas.cpp:3137-3143`）；仅 A5（`ptoas.cpp:3399-3402`） |
| `--emit-pto-ir` | bool（关） | `ptoas.cpp:639` | 主管线跑完即返回并 dump PTO IR（`ptoas.cpp:3770-3780`）；此模式下同步 pass 退化为串行 `SerialAutoSyncPass`（`ptoas.cpp:3723-3757`） |
| `--plan-memory-impl` | `legacy|modern`（`legacy`） | `ptoas.cpp:536` | 选择 `createPlanMemoryPass` / `createPlanMemoryModernPass`（`ptoas.cpp:3708-3712`）；modern 且未显式指定排序开关时默认按大小排序（`ptoas.cpp:3703-3706`） |
| `--enable-insert-sync` / `--enable-bufid_sync` / `--enable-inject-barrier-all-sync` / `--enable-graph-sync-solver` | bool（均关，互斥） | `ptoas.cpp:524/542/552/558` | if-else 链只装一个同步 pass（`ptoas.cpp:3723-3757`）；`--enable-bufid_sync` 仅 A5（`ptoas.cpp:3395-3397`） |
| `--enable-op-fusion` | boolOrDefault（默认不开） | `ptoas.cpp:585` | 需 A5（`ptoas.cpp:3415-3418`）且 level≥2（`ptoas.cpp:3441-3447`；level1 仅告警 `ptoas.cpp:3419-3422`）；EmitC 路径加 `FusionPlan`+`OpScheduling`+`MarkLastUse`（`ptoas.cpp:3675-3679`），VPTO 路径加 `FusionPlan`+`OpScheduling`+`FusionRegionGen`（`ptoas.cpp:3680-3685`），lowering 后另有融合收尾段（`ptoas.cpp:3171-3200`） |
| `--pto-print-seam-ir` / `--pto-seam-ir-file` | bool / 路径 | `ptoas.cpp:687-696` | 在共享 pre-backend seam 处把 IR dump 到 stderr / 文件（vpto 分支 `ptoas.cpp:3800-3809`、emitc 分支 `ptoas.cpp:3823-3828`；helper `ptoas.cpp:864-894`）；非 vpto 后端直接报错（`ptoas.cpp:3382-3388`）；mix 模式拒绝（`driver.cpp:1281-1287`） |
| `--cann-output-version` | 版本串，如 `9.0.0-beta.1` | `ptoas.cpp:698` | 覆盖探测到的 CANN 版本（解析 `driver.cpp:1384-1389`）：≥ 9.0.0-beta.2 时公开 ABI 后缀用 `.vector`/`.cube`，否则 `_mix_aiv`/`_mix_aic`（`tools/ptoas/ObjectEmission.cpp:924-934`；阈值常量 `include/PTO/Support/CANNVersion.h:61`）；并影响 VPTO LLVM emitter 选择（`lib/PTO/Transforms/VPTOLLVMEmitterDispatcher.cpp:16-21`） |
| `--vpto-fix-vfsimt-size` | `auto|off|verify`（`auto`） | `ptoas.cpp:703` | 控制 VF_SIMT code size 的校验/修复：经 `PTOASContext` 传入 `emitFatobjLLVM`（`driver.cpp:1400`、`driver.cpp:1145`）；修复器实现见 `tools/ptoas/VFSIMTSizePatcher.cpp` |

读表时注意三点：

- 选项是**进程级全局注册**：Python 一进程内多次调用编译靠 `ResetAllOptionOccurrences` 复位（`driver.cpp:1377`），没有每次调用的独立选项域。
- `--pto-arch` / `--pto-backend` 是否被**显式传入**有独立探测（`driver.cpp:1379-1380`），因此"显式传了默认值"≠"没传"（见 2.3／2.4 的分支差异）。
- VPTO 专属 flag（`--emit-vpto`、`--emit-vpto-llvm-ir`、seam IR 两项）在非 vpto 后端下直接报错（`ptoas.cpp:3382-3388`）。

### 2.6 小结（新开发者关注点）

- **跟读代码的顺序**：`ptoas_wrapper.py` → `_cli.py` → `NativeModule.cpp` → `runPTOASDriver` → `runPTOASJobs` → `compilePTOASModule`，正好是本章 2.1→2.4 再接第 4/5 章的路径。
- **别在 `ptoas.cpp` 里找 `main` 的定义**（见 2.1 的前向声明澄清）；排查入口行为时先分清跑的是构建树 wrapper 还是安装版 `bin/ptoas`，两者 Python root 的解析方式不同。
- **加新 CLI 选项**：在 `ptoas.cpp:510-714` 的 `cl::opt` 区块声明即可被解析链捕获；若语义需要区分"显式传入/未传"，参考 `driver.cpp:1379-1380` 的 `hasCLIOption` 预探测模式；若影响后端判定，需同步修改 `buildBackendInfo`。
- **加新 dialect/Pass**：注册进 `registerPTOASDialects` / `registerPTOASPassesAndCLOptions`（`ptoas.cpp:326-362`），否则 Python 借入的 Context 看不到它。
- **排查 mix 模式**：设 `PTOAS_DEBUG_CHILD_UNIT=1` 看 child 编译单元；记住 mix 拒绝一切调试 IR flag 且强制 `-o`（`driver.cpp:1281-1292`）。
- 选项如何变成具体 Pass 管线见第 4 章；两条后端路径的内部展开见第 5 章。

## 3. 前端入口（PTODSL、TileLang、PyPTO、.pto 文本/PTOBC）

第 2 章站在 driver 视角描述了"输入是什么"（文本 / PTOBC 双格式解析，见 2.3）；本章站在**生产者**视角回答"输入从哪来"。ptoas 自身不做源码级编译——它接收的已经是 PTO IR，因此前端的工作就是**把各种编程形态归一成 PTO IR（MLIR `ModuleOp`）或其序列化形式**。本章按用户最常碰到的顺序展开：ptoas 直接吃的形态（`.pto` / PTOBC）→ 本仓库主 DSL（PTODSL）→ 上游 TileLang 验证 → 编译期模板库（TileLib/SoftOps）→ 更底层的 Python 绑定（PyPTO）与仓库外 CuTile。每一类都遵循统一叙述节奏：**输入形态 → 处理 → 输出形态**。

### 3.1 前端形态总览

| 前端形态 | 代码位置（本仓库） | 产出物 | 进入 ptoas 的方式 |
| --- | --- | --- | --- |
| `.pto` 文本 | `test/lit/pto/*.pto` 等测试语料 | PTO IR 文本（`.pto`） | `ptoas input.pto`，文本解析分支（见 2.3） |
| PTOBC 二进制 | `tools/ptobc/`（编解码器 + CLI） | PTO Bytecode（magic `"PTOBC\0"`） | `ptoas input.ptobc`，二进制解码分支（见 2.3） |
| PTODSL（`@pto.jit`） | `ptodsl/ptodsl/` | 内存中的 PTO IR `ModuleOp`；或经原生库路径产 `.so` | tracing 构建 IR（见 3.3）；可打印成 `.pto` 走 CLI，或子进程调 `ptoas` 编原生库（见 3.4） |
| TileLang | 仓库外（上游框架）；`test/tilelang_st/` 为验证设施 | `.pto` 文本 | 现成 `.pto` 文件直接喂 `ptoas`（见 3.5） |
| PyPTO | `lib/Bindings/Python/PTOModule.cpp`（绑定本体） | 内存中的 PTO IR `ModuleOp`（通常 `print` 成文本） | 不自带编译驱动；产出走 3.2 的 `ptoas *.pto`（见 3.7） |
| CuTile | 仓库外（外部框架，README 提及） | —— | 仅文档引用，无仓库内代码（见 3.7） |

一张图记住各前端入口与 ptoas 的关系（CuTile 为仓库外消费者，不画）：

```text
  PTODSL (@pto.jit)          TileLang (upstream)         .pto text (hand-written)
        | trace                      | emit                     | (file)
        v                            v                         v
   PTO ModuleOp                <NAME>.pto file ────────────────┘
        | print .pto / subprocess CLI      |
        v                                  v
  +----------------------------------------------------------+
  | ptoas  (tools/ptoas/driver.cpp, 见第 2 章)                |
  +----------------------------------------------------------+
        ^
        | print(module) → .pto text (no in-process compile)
  PyPTO (PTOModule.cpp bindings; test/samples/)
```

### 3.2 `.pto` 文本与 PTOBC

**输入形态。** `.pto` 文本就是 PTO IR 的 MLIR 通用文本格式（`.mlir` 风格），人可直接读写。最小样例是 lit 冒烟用例 `test/lit/pto/empty_func.pto`：

```text
// RUN: ptoas %s | FileCheck %s

module {
  func.func @hello() -> () {
    return
  }
}

// CHECK: AICORE void hello() {
```

默认 `--pto-backend=emitc`，FileCheck 盯的是生成的 C++（`AICORE void hello()`），不是 PTO IR 本身。

典型算子用例会带 `pto.*` tile 级算子，如 `test/lit/pto/basic_float_tile_native.pto` 中的逐元素段：

```text
func.func private @trelu_arg(%src: !pto.tile_buf<vec, 1x16xf32>, %dst: !pto.tile_buf<vec, 1x16xf32>) {
  pto.trelu ins(%src : !pto.tile_buf<vec, 1x16xf32>) outs(%dst : !pto.tile_buf<vec, 1x16xf32>)
  return
}
```

**处理。** driver 按 6 字节魔数分派（`tools/ptoas/driver.cpp:131`，`isPTOBCBuffer`）：文本走 `parseTextualModule`（`tools/ptoas/driver.cpp:187`），解析前还可能从原始文本刮取 `pto.target_arch`；PTOBC 走 `decodePTOBCModule`（`tools/ptoas/driver.cpp:167`）。两条分支的细节与 arch 刮取规则已在 2.3 展开，此处不重复。

**输出形态（对 driver 而言的下游）。** 两条分支汇合成统一的 PTO IR `ModuleOp`，随即进入第 4 章的公共阶段。文本与 PTOBC 之外还有一个配套工具值得知道：`tools/ptobc/` 提供独立 CLI（`tools/ptobc/src/main.cpp:126`）做文本↔二进制 roundtrip，测试数据在 `tools/ptobc/testdata/`——新开发者想看某个 `.pto` 的二进制形态，用它转一次即可。

同一份 `.pto` **不是只能写 tile 级算子**。下面 3.2.1 把「文件里可以同时出现哪几层 IR、它们怎么汇合、哪一层能喂 emitc / vpto」单独写清；3.2.2 写各层支持范围。3.3 的 PTODSL 例子里 `pto.tile.load` 与 `pto.vlds` 同函数出现，就是这种混写的前端形态。

### 3.2.1 `.pto` 内的三层 IR（tile 级 PTO、VMI、VPTO 微指令）

**输入形态。** `.pto` 是 MLIR 通用文本，解析器不按「这是 VMI 文件还是 tile 文件」分派：一律 `parseTextualModule`（`tools/ptoas/driver.cpp:187`）。三层都挂在同一个 PTO dialect 上，只是 **op / 类型前缀不同**：

| 层 | 典型写法 | 类型 | 仓库里的现成语料 |
| --- | --- | --- | --- |
| Tile 级 PTO | `pto.tadd` / `pto.tload` / `pto.tmatmul` | `!pto.tile_buf<…>`、`!pto.tensor_view` | `test/lit/pto/`、`test/tilelang_st/`（3.2 / 3.5） |
| VMI | `pto.vmi.vadd` / `pto.vmi.vload` / `pto.vmi.vbrc` | `!pto.vmi.vreg<NxT>`、`!pto.vmi.mask`（mnemonic `vmi.vreg` / `vmi.mask`，`include/PTO/IR/VMITypeDefs.td:18-39`） | `test/lit/vmi_new/`、`test/vpto/cases/vmi_new/` |
| VPTO 微指令 | `pto.vadd` / `pto.vlds` / `pto.vsts` / `pto.vecscope` | 物理 `!pto.vreg` / `!pto.mask`（**没有** `vmi.` 前缀） | `--emit-vpto` 的出口；也可手写。TileOps 模板展开后也是这一层（`include/PTO/Transforms/Passes.td:564-571`） |

**三层都是 PTO IR。** 它们不是三个独立 dialect，也不是三种文件格式。方言名是 `pto`（`include/PTO/IR/PTODialect.td:25-32`）；全部 op 都走 `PTO_Op` → `Op<PTO_Dialect, …>`（`include/PTO/IR/PTOOps.td:73-74`）。`PTOOps.td:76-78` 把 `VMIOps.td` 与 `VPTOOps.td` **include 进同一个 dialect**：

- VMI 只是 mnemonic 带前缀：`PTO_Op<"vmi." # mnemonic>`，印出来是 `pto.vmi.vadd`（`include/PTO/IR/VMIOps.td:42-44`）。
- VPTO 微指令没有第二前缀：`pto.vadd` / `pto.vlds`，类是 `PTO_MicroOp`（`include/PTO/IR/VPTOOps.td:142-144`）。

口头上的「PTO IR」在本文里指这份 `pto` dialect 的整体（包括三层）。不要把它理解成「只有 tile 级才叫 PTO IR」。也不要把 **CLI 后端名** `--pto-backend=vpto` 当成另一种 IR：那是编译路径。VMI 也不是第三后端。

走哪条后端 **只看** `--pto-backend`（或 mix 子模块的 `pto.backend`），不看文件扩展名。`--emit-vpto` 只是 VPTO 后端的一种文本出口（5.2.4），不是第三后端。

**处理（三层如何配合）。** 它们是 VPTO 管线里的前后站，不是并列 ISA。`runVPTOBackendPipeline`（`tools/ptoas/ptoas.cpp:3271-3292`）的顺序把汇合点钉死：

```text
.pto 里可以同时有：

  pto.tadd          ──ExpandTileOp──►  pto.vlds / pto.vadd / pto.vsts
  pto.vmi.vadd      ──VMI pipeline──►  pto.vadd（可能 1 条变 N 条）
  pto.vadd          ──────────────►  已经是物理 VPTO，后面校验 / 发射
                                              │
                                              ▼
                                    fatobj / --emit-vpto 打印
```

对应代码步骤：

1. 若还有待展开 tile op → `lowerPTOToVPTOBackend`（`ptoas.cpp:3280-3282`）里的 `ExpandTileOp`（`ptoas.cpp:3166`）。模板函数体是 **物理 VPTO 微指令**（`pto.vecscope` / `pto.vlds` / `pto.vadd` / `pto.vsts`，`Passes.td:564-571`），**不是** `pto.vmi.*`。A2/A3 在 `LowerPTOToUBufOps` 后提前 `return`（`ptoas.cpp:3157-3163`），不走这次展开。
2. inline（`ptoas.cpp:3289-3290`），让 private helper 进入同一次 layout 决策。
3. **总是**跑 `appendVMISemanticPipeline`（`ptoas.cpp:3291`，序列见 5.2.3）：剩下的 `pto.vmi.*` 经 layout 赋值与 `VMIToVPTO` 变成物理 VPTO；已经是 `pto.vadd` 的基本空转。
4. `prepareVPTOForEmission`（`ptoas.cpp:3292`）做发射前合法性检查（含 `PTOValidateVPTOEmissionIR`）。

**tile 级不会降到 VMI。** 仓库里没有 `PTOToVMI` 一类 pass。A5 vpto 上 `pto.tadd` 经 `ExpandTileOp` **直接**变成物理 VPTO 微指令，再跑 VMI 管线时对已有 `pto.vadd` 基本空转。模板证据：`lib/TileOps/a5/tadd.py:16-17` 调 `pto.vadd`，不是 `pto.vmi.vadd`；`pto.vadd` 发的是 `_pto.VaddOp`（`ptodsl/ptodsl/_ops.py:1906-1916`）。另外两条路同样不经过 VMI：emitc 把 `tadd` 译成 `TADD(...)`；A2/A3 vpto 走 `pto.ub.vadd` 并跳过 `ExpandTileOp`（`ptoas.cpp:3157-3163`）。

外层 `tload` / 内层手写 `pto.vmi.vadd` 可以出现在同一份 `.pto` 里——那是**作者混写**，不是编译器把 tile 降到 VMI。要进 VMI 必须自己发 `pto.vmi.*`，或走 PTODSL 的 `pto.vmi` API。

因此一份文件完全可以：**外层 tile 算子 + 内层手写 VMI + 已经是 VPTO 的 helper**。它们在 vpto 后端汇合成同一套物理 VPTO，再发射。EmitC 路径没有上述展开 / VMI 管线，消费面只覆盖 tile 级 PTO（见下表与 5.1）。

**签名约定。** `README.md:278-280` 写明：VPTO backend 总是启用 VMI → VPTO 语义 pipeline；**public function signature 不能直接暴露 `!pto.vmi.*` 类型**。入口用 `!pto.ptr` / tile / 标量；VMI 类型放在函数体或 private helper 里。`test/lit/vmi_new/vmi_ptoas_cli_pipeline.pto:14-22` 即此形态：参数是 `f32` + `!pto.ptr<f32, ub>`，体内才是 `pto.vmi.vbrc` / `pto.vmi.vstore`，`--emit-vpto` 之后变成 `pto.vdup` / `pto.vsts`，不再出现 `pto.vmi.`。手写 `.pto` 一般写 **不带 layout 的 surface VMI**（`PTOValidateVMIIR` 在 layout 前校验，`ptoas.cpp:3314`）；带 `#pto.vmi.layout<…>` 的形态是 layout 赋值之后的中间 IR，不是常规作者输入。

**输出形态（对作者选后端而言）。** 哪一层能走哪条 `--pto-backend`：

| `.pto` 里主要是什么 | `--pto-backend=emitc` | `--pto-backend=vpto` |
| --- | --- | --- |
| Tile 级：`pto.tadd` / `tload` / `alloc_tile` … | **能。** `PTOToEmitC` 把 `pto.tadd` 降成 `TADD(...)`（`lib/PTO/Transforms/PTOToEmitC.cpp:6877-6880`）；类型转换认 `TileBufType`（`PTOToEmitC.cpp:1020`）。**不读 `lib/TileOps`。** | **能（A5）。** `ExpandTileOp` 用 TileOps 展开再变 VPTO。A2/A3 跳过展开（`ptoas.cpp:3157-3163`），tile-native 端到端见 6.5。 |
| VMI：`pto.vmi.*` / `!pto.vmi.vreg` | **不能。** `PTOToEmitC.cpp` 检索 `vmi` / `VaddOp` / `VldsOp` 零命中；没有 VMI 类型转换。会剩下未转换 op，`EmitPTOManualPass` 失败。 | **能，而且是这条后端的本职。** 语义管线始终启用（`ptoas.cpp:3291`）。 |
| VPTO 微指令：`pto.vadd` / `pto.vlds` / `pto.vecscope` | **不能。** EmitC 降的是 `pto.tadd` 这种 tile op，没有物理 `VaddOp` pattern。 | **能。** 视为已经接近发射形态，经校验后进 LLVM / fatobj。 |

经验规则：**EmitC = tile 级 PTO → C++ / pto-isa**；**VPTO = tile（A5 展开）+ VMI + 已有物理微指令 → 设备对象**。VMI 与 VPTO 微指令都只在 vpto 路上。默认 `--pto-backend=emitc`；文件里写了 VMI / `pto.vadd` 时必须显式 `--pto-backend=vpto`。查阅表见第 6 章 6.6；VPTO 管线细节见 5.2。各层**能表达什么**（硬件覆盖、类型、算子族）见下一节 3.2.2。

实际该怎么写 `.pto`：

1. **只写 tile 算子**（`test/lit/pto/`、TileLang 产出）：emitc、vpto（A5）都可以；emitc 出 C++，vpto 出 fatobj。
2. **只写 VMI**（`test/lit/vmi_new/`）：必须 vpto。入口用 ptr / 标量，体内 `pto.vmi.*`。
3. **只写物理 VPTO**（`pto.vecscope` + `pto.vadd`）：必须 vpto；VMI 管线几乎不改这些 op。
4. **混写**：可以，但要清楚汇合点——tile 先被展开成 VPTO 微指令，VMI 再被降成 VPTO 微指令。不要指望 emitc 消化 VMI 或 `pto.vadd`。

### 3.2.2 三层 IR 的支持范围

3.2.1 回答「一份文件里能不能混、走哪条后端」。本节回答 **每一层能表达什么、明确不覆盖什么**。三层都是同一个 `pto` dialect 里的 IR（ODS 装配见 3.2.1），但不是同一套算子换前缀，而是三种粒度的编程合同。逐 op 语义以手册为准：tile 级见 `docs/PTO_IR_manual.md`，VMI 见 `docs/isa/vmi-isa/`，VPTO 微指令见 `docs/vpto-spec.md`。能力速查表见 6.7。

**一句话对照。**

| 维度 | Tile 级 PTO | VMI | VPTO 微指令 |
| --- | --- | --- | --- |
| 工作单元 | 二维 `!pto.tile_buf`（rows×cols，DPS） | 一维逻辑向量 `!pto.vmi.vreg<N×T>` | 一条 256B 物理 `!pto.vreg<N×T>` |
| 作者要管什么 | tile 形状、valid、layout、本地缓冲地址 | 逻辑 lane 数与 elementwise 意图 | 物理寄存器、mask 粒度、`dist` / `part`、`vecscope` |
| 硬件覆盖 | Vector **+ Cube + MTE/DMA + 核间 pipe / 通信** | **仅 Vector 管线 + UB load/store** | Vector **+ Cube + MTE + SIMT + 同步** |
| 目标规格 | A2 / A3 / A5（路径不同） | 按 A5 向量管线建模 | 规格以 A5 为中心；A2/A3 另有 `pto.ub.*` |
| 后端 | emitc **和** vpto（A5） | **只能 vpto** | **只能 vpto** |

ODS 里「有这个 op」不等于这条路径能 lowering：tile 看 TileOps 模板或 EmitC pattern；VMI 看 `VMIToVPTO` 的 shape / layout 检查；VPTO 看 `PTOValidateVPTOEmissionIR`。

#### Tile 级：二维 tile 上的 nano-kernel

**合同。** `docs/PTO_IR_manual.md:14-22` 的 Level-2/3：tile 是带寿命的缓冲，不是纯 SSA。标记接口是 `TileOpInterface`（`include/PTO/IR/PTOInterfaces.td:43-47`）。核心类型 `!pto.tile_buf<loc, dtype, rows, cols, …>` 的 `loc` 覆盖存储层次：`vec`（UB）/ `mat`（L1）/ `left`（L0A）/ `right`（L0B）/ `acc`（L0C）/ `bias`（`PTO_IR_manual.md:149-164`，枚举 `include/PTO/IR/PTOAttrs.td:48-54`）。外加 `tensor_view` / `partition_view` 描述 GM 上的全局张量切片。

**算子族。** `PTO_TOp` 在 `include/PTO/IR/PTOOps.td` 中约 100 条 `t*`（例如 `TAddOp` 在 `PTOOps.td:3405`）。手册分类汇总约 113 个公开 op（含指针/同步等非 `t*` 骨架，`PTO_IR_manual.md:10783-10808`）。计算侧大致是：

- **搬运：** `tload` / `tstore` / `tprefetch` / `tmov` / `ttrans`
- **逐元素：** `tadd` / `tsub` / `tmul` / `tdiv` 及 `tadds` 等标量变体、`tabs` / `tneg` / `texp` / `tlog` / `trelu` / `tlrelu` / `tprelu`、位运算、移位、`tcvt`、`tcmp`
- **按行 / 列：** `trowsum` / `tcolmax` / `trowexpandadd` / `tcolexpand` 等（2D 归约与广播；VMI 没有「行 / 列」这个轴）
- **Cube：** `tmatmul` / `tgemv` 及 `.acc` / `.bias` / `.mx`
- **索引与乱序：** `tgather` / `tgatherb` / `tscatter`、`thistogram`、`tmrgsort` / `tsort32`
- **量化：** `tquant` / `tquant.mx` / `tdequant`
- **CV 双核：** `tpush` / `tpop` / `talloc` / `tfree` 以及 `*_to_aiv` / `*_from_aic`
- **通信：** `comm.tput` / `tget` / `tbroadcast` / `treduce` 等

周围还有非 `t*` 骨架：`alloc_tile`、`make_tensor_view`、`set_flag` / `get_buf`、标量 `load_scalar`。这些三层都会用，不算某一层的专属表面。

**真正能编过的范围比 ODS 更窄：**

| 路径 | 实际覆盖 |
| --- | --- |
| **emitc**（A2/A3/A5） | `PTOToEmitC` 把 `pto.tadd` 等直接译成 pto-isa 的 `TADD(...)`（`PTOToEmitC.cpp:6877-6880`）。**不读 TileOps。** A2/A3 的主战场。 |
| **vpto + A5** | `ExpandTileOp` 用 `lib/TileOps/a5/`（约 110 个模板，**目录只有 a5**）展开成物理 VPTO（`Passes.td:564-571`）。 |
| **vpto + A2/A3** | **不展开 TileOps**（`ptoas.cpp:3157-3163`）。`LowerPTOToUBufOps` 把一部分 eltwise 打成 `pto.ub.vadd` 等（文件头写明 `tadd/tsub/tmul/tdiv`，`lib/PTO/Transforms/LowerPTOToUBufOps.cpp:9-12`；UB 微指令还含 max/min/exp/gather，`include/PTO/IR/VPTOUbOps.td:60-68`）。复杂 tile（matmul、row/col、sort）在这条路上 **没有** A5 那种模板展开；端到见 6.5。 |

三条路径都 **没有** tile → VMI：A5 vpto 跳过 VMI 直接到物理微指令；emitc 到 C++；A2/A3 到 `pto.ub.*`。

**Tile 做不到、要下沉的：** 任意长度逻辑 SIMD（`128xf32` 跨两个物理寄存器）、物理 `part=EVEN/ODD`、SIMT 线程模型、手写 `vecscope` 里的流水线微指令。要这些语义必须**自己写** VMI 或 VPTO 微指令，编译器不会从 `pto.tadd` 生成 `pto.vmi.vadd`。

#### VMI：逻辑向量，只覆盖 Vector

**合同。** 对 `N` 个 lane 做 elementwise，不暴露 256B 切分。`K = ⌈N·bitwidth(T)/2048⌉` 条物理 vreg 由 layout assignment 决定（`docs/isa/vmi-isa/00-architecture-overview.md:34-45`）。这是 VMI 存在的理由：例如 `!pto.vmi.vreg<128xf32>` 是 2 个物理寄存器；`ui8` histogram 逻辑结果 `256×ui16`，硬件一次只能吐 `128×ui16`（`docs/designs/vmi-introduction.md:14-17`）。

**公开统一表面（作者该写的）约 55 条**（`docs/isa/vmi-isa/10-appendices.md:7-64`）：

- Load/store：`vload` / `vstore` / `vsstb`（**UB ptr**，不是 GM DMA）
- Eltwise：`vadd` / `vmul` / `vdiv`（仅 fp）/ `vmax` / `vand` / … 及 `vadds` 等 vec-scalar
- Compare/select：`vcmp` / `vsel` / `vselr`
- Reduce/broadcast：`vcadd` / `vcmax` / `vcmin`、`vbrc`（可带 `{group=C}`）
- Convert：`vcvt`、`vinterpret_cast`
- SFU：`vexpdif` / `vaxpy` / `vlrelu` / `vprelu` / `vmull` / `vmula` / histogram / `vgather*` / `vscatter`
- Predicate：`create_mask` / `pset` / `plt`
- Rearrange：`vintlv` / `vdintlv`

ODS 里还有一大坨 **legacy / 内部**（`pto.vmi.addf`、`load`、`ensure_layout`…，`include/PTO/IR/VMIOps.td` 前半）。那是 lowering 中间态，不是 PTODSL 公开 API（`ptodsl/docs/user_guide/14-vmi-virtual-instruction-set.md:17-20`）。

**类型范围（公开表面，`00-architecture-overview.md:66-68`）：**

| T | 合法逻辑 L（文档） |
| --- | --- |
| f32 / i32 族 | 1, 2, 4, 8, 64, 128, 256 |
| f16 / bf16 / i16 族 | 同上 |
| i8 / fp8 族 | 同上 |

PTODSL 还要求 `lanes` 为 64 的倍数（`ptodsl/docs/user_guide/14-vmi-virtual-instruction-set.md:48-49`）。内部 IR 可能接受其它正数 L，那不是对外类型构造选项。

**明确不在 VMI 范围：**

- **Cube**（没有 `mad` / L0）
- **SIMT**
- **GM↔UB DMA**（`mte_gm_ub`）；只有 `!pto.ptr<T, ub>` 上的向量 load/store
- **二维 tile / 行列表义**（没有 `trowsum`）
- **核间通信、CV pipe**
- **EmitC**
- A5 上 `vload` **不能带 mask**；MERGE predication 是编译器模拟，不是硬件原生（`00-architecture-overview.md:140-148`）

#### VPTO 微指令：物理 ISA，覆盖最全

**合同。** 一条指令 = 一条（或明确的 x2）256B 物理寄存器。`N * bitwidth(T) = 2048`（`docs/vpto-spec.md:879-890`）。mask 必须是 `b8` / `b16` / `b32`，并与元素宽度对齐。作者要写 `pto.vecscope`、`dist`、`part` 等硬件可见细节。

**规格分组（`docs/vpto-spec.md:1317-1338`，A5）：**

| 组 | 代表 | 大约条数 |
| --- | --- | --- |
| Pipeline sync | `set_flag` / `get_buf` / `rls_buf` | 5 |
| DMA | `mte_gm_ub` / `mte_ub_gm` / `mte_ub_l1`… | 4+ |
| Vector ld/st | `vlds` / `vsts` / `vgather2` / `vscatter` | ~23 |
| Predicate | `plds` / `pset_b*` / `pnot` | ~25 |
| Unary / binary / vec-scalar | `vadd` / `vexp` / `vadds` | ~30 |
| Convert / reduce / cmp | `vcvt` / `vcadd` / `vcmp` | ~20 |
| Rearrange | `vintlv` / `vdintlv`（`vintlvv2` **非 A5**） | 2 |
| Cube | `mad` / `mad_mx` / `mte_l1_l0a` / FIXPIPE | 20 |
| **SIMT** | `simt_launch` / `vote_*` / `atomic_*` / `syncthreads` | **~65** |
| 标量查询 | `get_block_idx` / `addptr` / `ld_dev` | 10 |

这是三层里 **唯一** 同时覆盖：物理向量、Cube 微指令、SIMT、以及完整 MTE 地址空间（gm / ub / l1 / l0）的一层。元素类型见 `vpto-spec.md:1442-1452`（i8–i64、f16 / bf16 / f32；i64 → 32 lane）。低精度 fp8 等更多出现在 tile / VMI 表面，VPTO 侧往往拆成具体 `vcvt` / `part`。

**A2/A3：** 不是这套 A5 `pto.vadd` 为主。vpto 缩短路径产出 `pto.ub.vadd` 一类 UB 微指令（`VPTOUbOps.td`），march `dav-c220-vec`（`ptoas.cpp:3215-3217`）。**EmitC 不认** `pto.vadd` / `pto.vlds`。

#### 重叠与选型

同一件「向量加法」三层都能写，粒度完全不同：

- Tile：`pto.tadd` 两个 `16×64xf32` tile（可能含 valid、padding）
- VMI：`pto.vmi.vadd` 两条 `!pto.vmi.vreg<128xf32>`（编译器拆 EVEN/ODD）
- VPTO：两条 `pto.vadd`，各吃一个 `!pto.vreg<64xf32>`，还要 mask / vecscope

```text
                    Cube / 2D tile / DMA tile / 通信
                              ▲
                              │  只有 Tile 级（+ VPTO cube 微指令）
                              │
     逻辑宽向量 / 跨寄存器 layout     物理 256B / SIMT / 显式 DMA
              ▲                              ▲
              │  只有 VMI                    │  只有 VPTO
              │                              │
              └──────── Vector eltwise ──────┘
```

选层：

1. 要 **matmul / gemv / 行列表 / GM tile 搬运 / 双核 pipe** → 写 tile 级；A5 走 vpto+TileOps，A2/A3 走 emitc。
2. 要 **逻辑长度 ≠ 一个物理寄存器**（f16→f32 拆 part、histogram 256 bin）→ 写 VMI，且必须 vpto。
3. 要 **抠流水线、SIMT、手写 DMA/mad** → 写 VPTO 微指令。
4. 只是 A5 上普通 eltwise、且能塞进一个 tile → tile 最省事；VMI 更接近「我按 N 个 lane 想」，VPTO 最接近硬件。

不要指望「写 `pto.tadd` 就会自动变成 `pto.vmi.*`」。三层都是 PTO IR，但 lowering 是 tile → 物理 VPTO（或 EmitC / `pto.ub.*`），VMI 只消化作者写出的 `pto.vmi.*`（3.2.1）。

### 3.3 PTODSL：tracing 构建 PTO IR

PTODSL 是本仓库自带、随根 `ptoas` wheel 一起发布的 Python DSL（`ptodsl/ptodsl/` 包，`ptodsl/README.md:13-33` 列了核心模块；顶层 `ptoas` Python 包本体在 `ptodsl/ptoas/`，由 `tools/ptoas/CMakeLists.txt:184-191` 装进 wheel）。它构造 PTO IR 的方式值得先立一个正确认知：**不是 AST 解释器，也不是 C++ JIT——是 tracing**：把用户的 Python 函数当成回调执行一遍，执行期间每个 DSL 调用直接在当前 `InsertionPoint` 发出对应的 MLIR op。一句话：Python 负责"编排"，MLIR builder 负责"落 IR"。

**输入形态（用户写什么）。** 典型输入是一个带类型注解的 Python 函数加 `@pto.jit` 装饰器。以 `test/dsl-st/vdiv_i16.py:35-110` 为例（节选）：

```python
@pto.jit(
    name="vdiv_i16_kernel",
    kernel_kind="vector",
    target="a5",
    mode="explicit",
    insert_sync=False,
)
def vdiv_i16_kernel(
    lhs_ptr: pto.ptr(pto.i16, "gm"),
    rhs_ptr: pto.ptr(pto.i16, "gm"),
    out_ptr: pto.ptr(pto.i16, "gm"),
):
    ...
    lhs_view = pto.make_tensor_view(lhs_ptr, shape=[1, 1, 1, 1, COLS],
                                    strides=[total, total, total, total, 1])
    lhs_tile = pto.alloc_tile(shape=[1, COLS], dtype=pto.i16, addr=0,
                              valid_shape=[1, COLS], blayout="RowMajor")
    pto.tile.load(lhs_part, lhs_tile)
    with pto.tileop():
        mask16 = pto.pset_b16(pto.MaskPattern.ALL)
        lhs_v = pto.vlds(lhs_tile[0, 0:])
        quotient = pto.vdiv(lhs_v, rhs_v, mask16)
        pto.vsts(quotient, dst_tile.as_ptr(), 0, mask16, dist="NORM_B16")
    pto.tile.store(dst_tile, out_part)
```

签名里的 `pto.ptr(pto.i16, "gm")` 注解决定了入口 ABI；装饰器参数里 `backend` 合法值只有 `vpto|emitc`（`ptodsl/ptodsl/_jit.py:70`，与第 1 章 1.4 的两后端铁律一致），`kernel_kind` 取 `cube|vector` 等。cube 侧例子见 `test/dsl-st/gemv_mx_pipeline.py:239-256`（`kernel_kind="cube"`，体内 `pto.tile.gemv_mx(...)`）。

**处理（tracing 四步）。** 从 `kernel.compile()` 到 IR 成型，链路集中在 `KernelCompiler.compile()`（`ptodsl/ptodsl/_kernel_compilation.py:104-142`）：

1. **AST 重写**：`tracing_callback()` 先经 `rewrite_jit_function`（`ptodsl/ptodsl/_kernel_compilation.py:96-101`）把 Python 的 `for range(...)` / `if` 改写成设备侧控制流友好的形态（实现于 `ptodsl/ptodsl/_ast_rewrite.py`，可经 `ast_rewrite=False` 关闭用于调试）。这是 tracing 前唯一一次"改用户代码"，之后回调按改写后的语义执行。
2. **构造 tracing runtime**：`compile()` 以改写后的回调构造 `SignatureTracingRuntime`（`ptodsl/ptodsl/_kernel_compilation.py:126-131`）——它持有模块骨架描述 `KernelModuleSpec` 与签名，本身还带 specialization 缓存（同一 kernel 不同 constexpr 绑定各存一份）。
3. **`build_module()` 执行 trace**：`TracingRuntime.build_module()`（`ptodsl/ptodsl/_tracing/runtime.py:80-97`）是 IR 成型核心——`make_context()` 建 MLIR Context → `create_kernel_module(spec, arg_types)` 搭模块骨架 → 在 entry block 的 `InsertionPoint` 里激活 session、执行用户回调 `trace_entry`（期间 `_ops.py` 的包装函数逐个发 `pto.*` op，如 `ptodsl/ptodsl/_ops.py:3153-3155` 的 `tload` 一行即 `_pto.TLoadOp(...)`）→ `verify_module` 校验。
4. **校验收尾**：session 层做追踪态校验（如打开的子核/循环帧是否闭合，`ptodsl/ptodsl/_tracing/session.py:1259-1265`）。

**输出形态（模块布局）。** PTODSL 可以产出三种顶层布局，由 `ModuleStyle` 枚举定义（`ptodsl/ptodsl/_tracing/module_builder.py:19-24`）：`FLAT_AICORE`（单 module 平铺，softlib 内部使用）/ `NESTED`（外层 module 内嵌一个子 module）/ `BACKEND_PARTITIONED`（外层容器 + 按后端分区的子 module）。`KernelModuleSpec` 默认 `backend="vpto"`、`module_style=NESTED`（`ptodsl/ptodsl/_tracing/module_builder.py:35,39`），而 `@pto.jit` 对用户 kernel 显式选 `BACKEND_PARTITIONED`（`ptodsl/ptodsl/_jit.py:301`）。关键在子 module 的属性装配 `_apply_child_module_attrs`（`ptodsl/ptodsl/_tracing/module_builder.py:74-84`）：每个子 module 携带 `pto.target_arch` / `pto.backend` /（满足条件时）`pto.kernel_kind`——**这正是 driver 侧混合后端容器判定与 child job 拆分所消费的属性**（`isBackendPartitionedContainer` 与 `collectChildJobs`，见 2.4；混合后端编译全貌见第 5 章 5.3）。多子 module 场景由 session 按符号逐个创建 child（`ptodsl/ptodsl/_tracing/session.py:1243-1257`，调 `create_container_child_module`）。

到这里 PTO IR 只存在于内存。它有两条去路：**打印成 `.pto` 文本走 CLI**（`KernelHandle` 支持 `print` 即默认特化的 MLIR 文本），或走 3.4 的原生库路径——编译本身是子进程调 `ptoas` CLI，Python 进程随后 `dlopen` 得到的 `.so`。

### 3.4 PTODSL 的原生库编译路径

**输入形态。** 内存中的 PTO IR `ModuleOp`（来自 3.3 的 tracing）。

**处理。** `ptodsl/ptodsl/_runtime/native_build.py` 的模块 docstring 一句话说明全流程："MLIR → ptoas → bisheng native library build"（`ptodsl/ptodsl/_runtime/native_build.py:8`）。三步：

1. **子进程调 ptoas CLI**：`_run_ptoas`（`ptodsl/ptodsl/_runtime/native_build.py:42-70`）拼装并执行 `ptoas --pto-arch=<arch> [--pto-backend=..] [--pto-level=..] [--enable-insert-sync] --enable-tile-op-expand <mlir> -o <kernel_object>`——注意这里**不再是 pybind11 进程内调用**，而是把 IR 写临时文件、起子进程跑 2.1 的 CLI 链路；`--enable-tile-op-expand` 恒开（tile 算子须先经 TileLib 展开，见 3.6）。level 与 sync 的默认值由 `_effective_insert_sync` / `_effective_pto_level` 推导（`ptodsl/ptodsl/_runtime/native_build.py:73-80`）：`mode="explicit"` 时强制 `level3` 且默认关 sync，`auto` 模式反之。`level3` 的含义是跳过 PlanMemory、要求 alloc 带显式 `addr`（见第 2 章选项表与第 4 章），不是"最高优化档"。
2. **生成 launch 包装**：`generate_launch_cpp`（`ptodsl/ptodsl/_runtime/codegen.py:96`）按签名生成 extern-C 的 `ptodsl_launch_<fn>` 宿主入口，把 Python 侧的指针参数翻译成 `__gm__` 设备指针调用。
3. **BiSheng 编译链接**：kernel 对象已经是第 1 步 ptoas `-o` 的产物（`native_build.py:244-251`）；只有 launch.cpp 再经 `_compile_launch_cpp` 编译（`native_build.py:255-261`，flags 来自 `_kernel_compile_flags`，`native_build.py:125-148`，含 `--cce-aicore-arch` 等 CCE 选项）。然后 `_link_shared_library`（`native_build.py:262-267`，实现 `173-197`）以 `--cce-fatobj-link` 把 launch.o 与 kernel 对象链成单个 `.so`。

**输出形态。** 一个可直接 `dlopen` 的共享库 + launch 入口，PTODSL 用它支撑 `compiled[grid, stream](...)` 风格的宿主调用（`test/dsl-st/common.py:197-201` 的 `kernel.compile()` 后 `compiled[1, stream](*inputs)` 即消费此产物）。产物按 specialization 哈希缓存在 `~/.cache/ptodsl/`（`ptodsl/ptodsl/_runtime/cache.py:19-23`，可用环境变量 `PTODSL_CACHE_DIR` 覆盖；`ptodsl/README.md:248` 亦有说明），命中即免重编。

### 3.5 TileLang 集成（test/tilelang_st）

TileLang 是仓库外的上游框架，本仓库对它的责任是**系统级（ST）验证设施**：`test/tilelang_st/` 收 TileLang 前端产出的 `.pto` 语料，端到端验证 ptoas 编译产物在 NPU/模拟器上的数值正确性。

**输入形态。** TileLang 产出的 `.pto` 文本。例如 `test/tilelang_st/npu/a5/src/st/smoke/testcase/tadd/tadd.pto` 的 Case 0（`tadd.pto:14-67`，节选）：

```text
module attributes {pto.target_arch = "a5", pto.kernel_kind = #pto.kernel_kind<vector>} {
  func.func @TADD_f32_16x64(%a_ptr: !pto.ptr<f32>, %b_ptr: !pto.ptr<f32>, %c_ptr: !pto.ptr<f32>) attributes {pto.kernel} {
    ...
    %a_part = pto.partition_view %a_view,
      offsets = [%c0, %c0, %c0, %c0, %c0],
      sizes = [%c1, %c1, %c1, %c16, %c64]
      : !pto.tensor_view<1x1x1x16x64xf32> -> !pto.partition_tensor_view<1x1x1x16x64xf32>
    %a = pto.alloc_tile : !pto.tile_buf<vec, 16x64xf32>
    pto.tload ins(%a_part : ...) outs(%a : !pto.tile_buf<vec, 16x64xf32>)
    pto.tadd ins(%a, %b : ...) outs(%c : !pto.tile_buf<vec, 16x64xf32>)
    pto.tstore ins(%c : ...) outs(%c_part : ...)
    return
  }
```

注意它与 PTODSL 产物的形态差异：TileLang 语料是**平铺单 module**（顶层直接 `module attributes`），逐用例一个 `func.func`，依赖 ptoas 侧 TileLib 展开（该文件头注释即写明编译命令带 `--enable-tile-op-expand --pto-backend=vpto`，`tadd.pto:11`）。

**处理。** CMake 宏 `pto_tilelang_st`（`test/tilelang_st/npu/a5/src/st/smoke/testcase/CMakeLists.txt:25-95`）三步流水，其头部注释（`CMakeLists.txt:9-17`）自述流程：

1. **ptoas 编译**：`<NAME>.pto` → `${NAME}_kernel.o`（CMake 步骤 1，`CMakeLists.txt:45-62`，输出变量 `KERNEL_FATOBJ` 在 `CMakeLists.txt:47`；经 `run_ptoas_to_file.cmake` 捕获产物）；
2. **fatobj 链接**：`launch.cpp` + fatobj 以 `--cce-fatobj-link` 链成共享库（`CMakeLists.txt:64-75`；`launch.cpp` 手写宿主侧 `<<<1, nullptr, stream>>>` 启动包装，见 `test/tilelang_st/npu/a5/src/st/smoke/testcase/tadd/launch.cpp:20-27`）；
3. **宿主可执行**：`main.cpp` → 可执行文件，数值比对由外部 `compare.py` 完成（不用 GTest，`CMakeLists.txt:16`）。

vec/cube 变体由 `pto_tilelang_vec_st` / `pto_tilelang_cube_st` 包装（`CMakeLists.txt:97-111`），差异仅在 `--cce-aicore-arch`（`dav-c310-vec` / `dav-c310-cube`）。

**输出形态。** host 可执行 + `.so`，在 NPU 或模拟器上跑出结果与 golden 对比。用例注册表 `ALL_TESTCASES`（`CMakeLists.txt:116-205`）列出 `tadd` … `tmatmul` 共 88 个用例，新增 tile 算子时在此登记即可被 `TEST_CASE` 选择器发现（`CMakeLists.txt:207-217`）。

### 3.6 TileLib 与 SoftOps（lib/TileOps、lib/SoftOps）

第 4 章会讲 Pass 管线，这里先讲清两个"库形态前端"——它们不是用户直接面对的入口，而是**编译期被 pass 按需物化的算子模板库**，但其实现语言就是 PTODSL，故归入前端一章。

**TileLib（tile 级算子模板）。** 输入形态是 `lib/TileOps/a5/` 下的 Python 模板（122 个文件），每个模板用 `@tilelib.tile_template` 装饰器声明元数据并给出 PTODSL 实现。例如 `lib/TileOps/a5/tmatmul.py:16-23`：

```python
@tilelib.tile_template(op="pto.tmatmul", target="a5", name="template_tmatmul",
                       dtypes=MATMUL_DTYPES, iteration_axis="none",
                       op_engine="cube", op_class="other", id=0, loop_depth=1,
                       is_post_update=False, tags=("cube", "matmul"))
def template_tmatmul(lhs: pto.Tile, rhs: pto.Tile, acc: pto.Tile):
    m, k = lhs.valid_shape
    _, n = rhs.valid_shape
    pto.mad(lhs.as_ptr(), rhs.as_ptr(), acc.as_ptr(), m, n, k, disable_gemv=True)
```

处理链路：`ExpandTileOp` pass（`include/PTO/Transforms/Passes.td:564-589`）把 `pto.tadd` 等 tile 算子替换为对模板函数的 `func.call`；`InsertTemplateAttributes`（`Passes.td:548-562`）在融合前先查询合法候选并把精简的 `candidates` 属性贴到算子上。pass 与 Python 之间是**进程内服务桥**：C++ 侧接口是纯虚的 `TileLibService`（`include/PTO/Transforms/TileLibService.h:42-53`，只有 `getMetadata` / `materialize` 两个方法，刻意不依赖 pybind11 以便原生测试复用）；`tools/ptoas/NativeModule.cpp:30-91` 的 `PythonTileLibService` 实现该接口，转发到 `ptodsl.tilelib._compiler_runtime` 的 `metadata` / `materialize`（`ptodsl/ptodsl/tilelib/_compiler_runtime.py:19-73`），后者用 `_TemplateTrace` 重新 trace 模板得到 `(module, entry_symbol)`——**全程不落 MLIR 文本、不走守护进程**。TileOps 目录随包安装到 `ptoas/_runtime/share/ptoas/TileOps`（`tools/ptoas/CMakeLists.txt:195-203`），运行时由 `ptodsl/ptoas/_cli.py:34-47` 定位 TileOps 目录，再在 `ptodsl/ptoas/_cli.py:59-67` 临时插入 `sys.path`（见 2.1）。

**SoftOps（标量软算子）。** 同构但更小：`lib/SoftOps/`（目前 `trig.py`、`div_int.py`）放 A5 SIMT 标量函数的 PTODSL 实现（如 `lib/SoftOps/trig.py:15-22` 的三角函数归约多项式），由 `PTOExpandSoftLib.cpp`（`lib/PTO/Transforms/PTOExpandSoftLib.cpp:11-15`，注释明言"实现留在 lib/SoftOps、以 PTODSL 编写，本 pass 只做选择与物化"）在 VPTO 边界物化，Python 侧入口是 `ptodsl.softlib._compiler_runtime`（`ptodsl/ptodsl/softlib/_compiler_runtime.py:9`，同样经 `tools/ptoas/NativeModule.cpp:110` 的 `PythonSoftLibService` 桥接，安装到 `ptoas/_runtime/share/ptoas/SoftOps`，`tools/ptoas/CMakeLists.txt:205-212`）。输出形态：模板/软函数体被 inline 进调用点，此后的 IR 与手写等价。

一句话总结这节的设计动机：**tile 级与标量级算子的"参考实现"用 PTODSL 写、随 ptoas 分发、编译期按需物化**——pass 层只保留选择与内联逻辑，算法库的迭代不需要重编编译器。

### 3.7 PyPTO 与 CuTile 定位

**PyPTO** 指通过 Python 绑定直接**构建** PTO IR 的底层形态，不是一条独立的编译入口。绑定本体是 `lib/Bindings/Python/PTOModule.cpp`（入口 `populatePTODialectBindings`，`lib/Bindings/Python/PTOModule.cpp:131`，含 `register_dialect` 与全部类型/属性绑定），编译进 `PTOASCompilerImplementation`（`tools/ptoas/CMakeLists.txt:118`），配 CAPI 层 `lib/CAPI/Dialect/PTO.cpp` + `include/pto-c/Dialect/PTO.h`。根 `README.md:12` 把它与 PTODSL、CuTile 并列为支持的 Python 框架。

**输入形态。** 手写 `ptoas.mlir` 绑定：`from ptoas.mlir.dialects import pto` 后用 `InsertionPoint` 逐个构造 `pto.*` op。仓库样例 `test/samples/MatMul/tmatmulk.py:37-267` 手工构建 `ModuleOp`、`MakeTensorViewOp`、`AllocTileOp`、`TLoadOp`、`TMatmulOp` 直到 `TStoreOp`——即 **没有 `@pto.jit` 这层糖的裸 MLIR Python API**（`ptodsl/README.md:29-31` 的 `*_lowlevel.py` 示例同款）。PTODSL 的 `_ops.py` 与 tracing（3.3）就是在这套绑定之上的便利层。

**处理。** `register_dialect` + 在 Context 里建 `ModuleOp`；`PTOModule.cpp` 不调用 `runPTOAS`。

**输出形态。** 内存中的 `ModuleOp`，样例以 `print(m)` 打成文本（`test/samples/MatMul/tmatmulk.py:274-276`）。之后与手写 `.pto` 一样走 3.2 的 `ptoas` CLI。

**CuTile** 是外部/上游框架，本仓库无对应目录与代码，仅 `README.md:12` 在前端支持列表中提及——定位为"经同一套 Python 绑定接入的第三方消费者"，与 PyPTO 共享绑定但不在本仓库维护。新开发者若在仓库里找 CuTile 实现会找不到，这是预期行为。

### 3.8 小结（前端选择建议）

- **写新 kernel**：默认 PTODSL（`@pto.jit`），签名类型注解即 ABI，自动享 specialization 缓存与 `~/.cache/ptodsl/` 原生库缓存；控制流要求超出 DSL 表达力时再降级到 PyPTO 裸绑定（参考 `ptodsl/examples/tadd_lowlevel.py` 与 `test/samples/MatMul/tmatmulk.py`）。
- **手写/修改 IR 做实验**：直接写 `.pto` 文本跑 lit（`test/lit/pto/` 有 700+ 现成用例可作语法参考），或用 `tools/ptobc` CLI 探二进制形态。选后端前先认清文件里是 tile / VMI / VPTO 微指令中的哪一层（或混写），对照 3.2.1 / 6.6：VMI 与 `pto.vadd` 只能走 `--pto-backend=vpto`。选层（要不要 cube / 逻辑宽向量 / SIMT）对照 3.2.2 / 6.7。
- **验证新 tile 算子的端到端行为**：在 `test/tilelang_st/npu/a5/src/st/smoke/testcase/CMakeLists.txt:116-205` 注册新用例，三步 CMake 流水自动覆盖"编译→链接→跑数"。
- **扩充算子模板**：`lib/TileOps/a5/` 加模板文件并声明元数据，`lib/SoftOps/` 放标量软实现；pass 侧无需改动。
- 所有前端殊途同归：产出 PTO IR `ModuleOp`（或其文本/二进制序列化）后，统一进入第 4 章的公共编译阶段。同一 `ModuleOp` 里可以混有 3.2.1 的三层 op，分叉发生在第 5 章，不在解析器。

## 4. 公共阶段（解析/验证、Pass 管线编排、level 分档）

第 3 章把各前端归一成 PTO IR（MLIR `ModuleOp`）。本章站在 `compilePTOASModule`（`tools/ptoas/ptoas.cpp:3355`）内部，讲这条 **共享主管线**：输入仍是 tile 级 `pto.*` IR，经过校验、规范化、内存规划与（可选）同步插入，在 `tools/ptoas/ptoas.cpp:3794` 处分叉到 emitc / vpto。两条后端各自的 lowering 与发射是第 5 章的事；VMI 是 vpto 路径内部的 IR 层，本章不展开。选项定义与默认值见第 2 章 2.5，这里只讲它们如何变成 Pass 装入条件。

叙述节奏与前几章一致：**输入 IR 形态 → 处理 → 输出 IR 形态**。

### 4.1 组件地图（`include/PTO` 与 `lib/PTO`）

共享阶段的实现几乎全部落在 `include/PTO/`（声明 / ODS）与 `lib/PTO/`（C++ 实现）两棵树上。看代码时先按这张地图找文件，再进 `compilePTOASModule` 看编排。

```text
include/PTO/
  IR/            dialect ODS 与工具头
                 PTODialect.td (PTO_Dialect, include/PTO/IR/PTODialect.td:25)
                 PTOOps.td / PTOAttrs.td / PTOTypeDefs.td / PTOInterfaces.td
                 VMI 层: VMIOps.td / VMIAttrs.td / VMITypeDefs.td / VMIUtils.h
                 VPTO 层: VPTOOps.td / VPTOTypeDefs.td / VPTOUbOps.td /
                           VPTOInterfaces.td / VPTOScheduling.h / VPTOAddressSemantics.h
                 工具: PTOSyncUtils.h / PTOMultiBuffer.h / PTOTypeUtils.h
  Transforms/    Pass 定义与子框架头
                 Passes.td（TableGen 全量 pass，1587 行）/ Passes.h
                 InsertSync/  GraphSyncSolver/  TileFusion/  VPTOScheduler/
                 TileLibService.h / SoftLibService.h / VPTOLLVMEmitter.h
  Analysis/      PTOAddressAnalysis.h / PTOValueEvolutionAnalysis.h
  Support/       CANNVersion.h / CodeConstants.h / PythonExecutable.h
  Compiler/      CompilerApi.h（导出宏）

lib/PTO/
  IR/            PTO.cpp / VMI.cpp / VPTO.cpp / VPTOUbOps.cpp / PTOAttrs.cpp …
  Analysis/      地址分析与 value evolution
  Transforms/    ~100 个 cpp + 子目录
                 TileFusion/ InsertSync/ BufidSync/ GraphSyncSolver/ VPTOScheduler/
```

相关但不在 `lib/PTO/` 下的两块，第 3 章已经讲过：`lib/TileOps/`（TileLib 模板）与 `lib/SoftOps/`（标量软算子）由 `ExpandTileOp` / `PTOExpandSoftLib` 在编译期物化；C API 在 `lib/CAPI/Dialect/PTO.cpp`。VMI / VPTO 的 ODS 虽然和 PTO 同住 `include/PTO/IR/`，它们的 lowering 管线在第 5 章。

`include/PTO/IR/CMakeLists.txt:15-32` 从 `PTOOps.td` 生成 dialect / op / type / attr / enum 的 `*.h.inc`，目标名 `PTOOpsIncGen`（`include/PTO/IR/CMakeLists.txt:53`）；实现库是 `PTOIR`（`lib/PTO/IR/CMakeLists.txt:15`）。Pass 的 TableGen 见下一节。

### 4.2 Pass 注册机制

PTO 自有 Pass 走标准 MLIR TableGen 注册链，再由驱动在解析命令行之前挂上：

1. **定义**：`include/PTO/Transforms/Passes.td` 里每个 `def Foo : Pass<"pto-...">` 给出 CLI 名、summary、constructor。
2. **生成**：`include/PTO/Transforms/CMakeLists.txt:14-18` `mlir_tablegen(Passes.h.inc -gen-pass-decls -name PTO)`，目标 `PTOPassesIncGen`。
3. **声明 / 注册宏**：`include/PTO/Transforms/Passes.h:34-35` `#define GEN_PASS_DECL` 展开工厂声明；同文件 `Passes.h:163-165` `#define GEN_PASS_REGISTRATION` 展开 `mlir::pto::registerPTOPasses()`。
4. **挂到驱动**：`registerPTOASPassesAndCLOptions()`（`tools/ptoas/ptoas.cpp:347`）先注册 MLIR 通用包（conversion / arith / func / math / memref / scf / tensor / transforms），再调用 `mlir::pto::registerPTOPasses()`（`tools/ptoas/ptoas.cpp:357`），最后 `registerPassManagerCLOptions()` 让 `--mlir-print-ir-after-all` 一类调试开关生效。driver 在 `runPTOASDriver` 开头调用该函数（见 2.2）。独立的 pass runner `tools/pto-test-opt/pto-test-opt.cpp:33` 也调同一 `registerPTOPasses()`。

并非主管线上每一个步骤都来自 TableGen。`createSerialFrontendPipeLoweringPass`（`tools/ptoas/ptoas.cpp:1428`，包装 `PTOAssignDefaultFrontendPipeId` + `PTOLowerFrontendPipeOps`）和 `--emit-pto-ir` 用的 `SerialAutoSyncPass`（`tools/ptoas/ptoas.cpp:1346`）是 `ptoas.cpp` 里的 `PassWrapper`，不经 `Passes.td`。`createPlanMemoryModernPass` 同样是手写 Pass（`lib/PTO/Transforms/PTOPlanMemoryModern.cpp:1809`），不在 `Passes.td` 里。加新的可独立跑的 PTO Pass：写入 `Passes.td` 即可被 `registerPTOPasses` 捕获；只在主管线里串行包装的，才需要像 `SerialFrontendPipeLoweringPass` 那样就地写。

### 4.3 `compilePTOASModule` 主流水线（12 步）

**输入 IR 形态。** 进入 `compilePTOASModule` 的已经是验证过的 PTO IR `ModuleOp`（文本解析或 PTOBC 解码，见 2.3 / 3.2）。最简输入仍是第 3 章用过的 `test/lit/pto/empty_func.pto`——空 `func.func @hello`，主管线几乎是空转，默认 emitc 出口变成 `AICORE void hello()`。带 tile 的输入会在管线里被规范化、规划地址、按需插同步，例如 `test/lit/pto/basic_float_tile_native.pto` 的 `pto.trelu` 等。

**处理之前：校验与预规范化。** 函数开头先做与后端无关的门禁，失败直接 `return 1`，还不会建主管线 `pm`：

- SCF `for` 常量步长必须为正（`validateSCFForConstantSteps`，`tools/ptoas/ptoas.cpp:3360`）；栈上 struct 来源校验（`validateStructProvenance`，`tools/ptoas/ptoas.cpp:3367`）。
- 解析 `--pto-level`（`tools/ptoas/ptoas.cpp:3389-3394`，`parseBuildLevel` 在 `tools/ptoas/ptoas.cpp:726`）；回写 `pto.target_arch`（`tools/ptoas/ptoas.cpp:3404`）；`mlir::verify`（`tools/ptoas/ptoas.cpp:3407`）。
- A5 专属 CLI gate（4.7）与 `pto.tassign` / 同步互斥 / level3 显式 `addr` 校验（4.5、4.6）。
- **预 PassManager** `preBackendPM`（`tools/ptoas/ptoas.cpp:3594-3605`）：`PTOMaterializeTileOpSections` → `PTONormalizeUncoveredTileSections` → `PTOValidatePhysicalSectionBoundaries`，把 tileop / section 边界收成后端都能消费的形态。

随后 `hasUnexpandedTileOps`（`tools/ptoas/ptoas.cpp:896`，调用点 `tools/ptoas/ptoas.cpp:3607`）探测模块里是否还有待展开的 tile 算子。若 effective backend 是 vpto **且没有**待展开 tile op，主管线整段跳过，直接 `runVPTOBackendPipeline`（`tools/ptoas/ptoas.cpp:3609-3619`）——这是给已经是低层 IR 的输入开的快路径，细节见第 5 章。其余情况进入下面的共享 `pm`（`tools/ptoas/ptoas.cpp:3623`）。

**12 步顺序。** 行号均指 `tools/ptoas/ptoas.cpp`。条件 Pass 在「职责」列写明 gate；无条件的默认总是装入。

| 步 | Pass / 动作 | 行号 | 职责 |
| --- | --- | --- | --- |
| 1 | `PTOCanonicalizeIR` | 3635-3637 | **仅 vpto**：把 rank-2 视图描述符规范成右对齐 rank-5（`include/PTO/Transforms/Passes.td:631`）。emitc 靠 `InferPTOLayout` 补 stride，不在 IR 层做这一步。 |
| 2 | `createSerialFrontendPipeLoweringPass` | 3638 | 串行包装：缺省 `id=0`（`PTOAssignDefaultFrontendPipeId`）再把前端 TPUSH/TPOP 降成内部 pipe IR（`PTOLowerFrontendPipeOps`，实现 `tools/ptoas/ptoas.cpp:1397-1428`）。`PTOVerifyTFree` 在下一行被注释掉（`tools/ptoas/ptoas.cpp:3639`），当前主管线不跑。 |
| 3 | `PTOInferValidatePipeInit` → `LoweringSyncToPipe` → `InferPTOLayout` → `PTOA5NormalizeTMov` → `PTOValidateIntToPtrUses` | 3640-3653 | 推断/校验 pipe init `nosplit`；高层 `record_event`/`wait_event` 降到 `set_flag`/`wait_flag`；推断 GlobalTensor ND/DN/NZ 布局（可被 `--disable-infer-layout` 关掉，`tools/ptoas/ptoas.cpp:3642`）；**非 a2/a3** 规范化有风险的 vec→vec col_major `pto.tmov`；限制 `inttoptr` 结果用途。 |
| 4 | `InsertTemplateAttributes` | 3658-3660 | **vpto + 非 a2/a3 + 确有待展开 tile op**：向 TileLib 查询合法模板候选，写成算子上的 `candidates` 属性，供后续 `ExpandTileOp` 选用（展开本身在 vpto 后端，`tools/ptoas/ptoas.cpp:3166`，见第 5 章）。 |
| 5 | A5 融合前端 | 3675-3685 | **A5 + `--enable-op-fusion` + level≠1**（`enableA5FusionPath`，`tools/ptoas/ptoas.cpp:3441`）。emitc：`FusionPlan` + `OpScheduling` + `MarkLastUse`（3675-3679）。vpto：`FusionPlan` + `OpScheduling` + `FusionRegionGen`（3680-3685）。 |
| 6 | `PTOMaterializeImplicitTmp` + `PTORematerializeFixpipeVectorQuant` | 3687-3691 | 把隐式 tmp tile 物化为显式 alloc（level3 时 `requireExplicitTmp=true`，`tools/ptoas/ptoas.cpp:3688-3689`）；在每个 `tpush` 前重物化 fixpipe 向量量化绑定，避免 PlanMemory 过早复用 scaling tile。 |
| 7 | `PlanMemory` / `PlanMemoryModern` | 3699-3713 | **level1/level2 才跑**（`effectiveLevel != Level3`）。`--plan-memory-impl=legacy\|modern` 二选一（3708-3712）；modern 且未显式指定排序开关时默认按大小排序（3703-3706）。level3 跳过，调用方必须自带物理地址（4.5）。 |
| 8 | `PTOResolveReservedBuffers` + `PTORemoveIdentityTMov` | 3714-3715 | 解析 `reserve_buffer` 地址与 peer pipe `flag_base`；删除源目的相同的自拷贝 `pto.tmov`。 |
| 9 | 四种 AutoSync 互斥四选一 | 3723-3757 | `InsertSync` / `BufidSync` / `BarrierAll` / `GraphSolver` 至多装一个；`--emit-pto-ir` 时退化为模块级 `SerialAutoSyncPass`。细节见 4.6。同步在 `PTOResolveBufferSelect` **之前**跑，以便看见带 slot 身份的 `pto.multi_tile_get`。 |
| 10 | `PTOResolveBufferSelect` → CSE / Inline / Canonicalize | 3761-3789 | 把 `subview` / `multi_tile_get` 解析成带地址的 `alloc_tile`。若 `--emit-pto-ir`（变量 `emitMlirIR`，`tools/ptoas/ptoas.cpp:639`），此处立刻 `pm.run` 并 dump PTO IR 返回（3770-3780），**不**再跑 CSE、也不进后端分叉。否则续装 CSE、`PTOInlineBackendHelpers`、Canonicalize、再 CSE（3782-3789）。emitc 额外插入 `NarrowUnusedMultiResultProvenance`（3762-3764、3785-3787）。 |
| 11 | vpto 分叉 | 3794-3815 | 先 `pm.run` 共享管线，可选 seam IR dump，再 `runVPTOBackendPipeline`（`tools/ptoas/ptoas.cpp:3271`）→ `emitVPTOBackendResult`。VMI→VPTO 语义管线在此函数内部，见第 5 章 5.2。 |
| 12 | emitc 分叉 | 3818-3849 | 先 `pm.run` 共享管线，再另建 `emitcPM`（3833）：`EmitPTOManual(A3\|A5)` + `FormEmitCExpressionsCompatPass` + CSE，随后翻译成 C++。见第 5 章 5.1。 |

共享段的数据流可以记成：

```text
  PTO IR ModuleOp（第 3 章产出）
            |
            v
  校验 / A5 gate / level3 addr / tassign
            |
            v
  preBackendPM（section 规范化）          tools/ptoas/ptoas.cpp:3594
            |
            +-- vpto 且无待展开 tile op --> 跳过共享 pm --> 第 5 章 VPTO
            |
            v
  共享 pm（上表第 1–10 步）               tools/ptoas/ptoas.cpp:3623
            |
            +-- --emit-pto-ir --> dump PTO IR，返回（3770）
            |
     +------+------+
     | vpto        | emitc
     v             v
  第 11 步      第 12 步
  （第 5 章）    （第 5 章）
```

**输出 IR 形态。** 共享 `pm` 跑完时，IR 仍是 PTO dialect：tile alloc 已带物理地址（level1/2 由 PlanMemory 填，level3 由前端写死），`multi_tile_get` 已展开成 addressed handle，可选地插入了同步。真正改方言发生在分叉之后——emitc 变成 `emitc.*` 再译 C++，vpto 进入 VMI 再进 VPTO。

### 4.4 Pass 分类速览

主管线用到的只是 `Passes.td` 里的一小段。全库按职责可分成五类（再加第 5 章才会碰到的 VMI / VPTO 物理层）。**不必在这里逐 Pass 展开**：定义、summary 与 constructor 以 `include/PTO/Transforms/Passes.td` 为准，附录 A 会做成按 CLI 名检索的速查表。

| 类别 | 代表 Pass（`Passes.td`） | 在主管线中的位置 |
| --- | --- | --- |
| 规范化 / 校验 | `PTOCanonicalizeIR`（631）、`InferPTOLayout`（126）、`PTOA5NormalizeTMov`（137）、`PTORemoveIdentityTMov`（150）、`LoweringSyncToPipe`（232）、`PTOAssignDefaultFrontendPipeId`（485）、`PTOLowerFrontendPipeOps`（468）、`PTOInferValidatePipeInit`（502）、预 PM 的 section 三件套 `PTOMaterializeTileOpSections` / `PTONormalizeUncoveredTileSections` / `PTOValidatePhysicalSectionBoundaries`（355-427）、`PTOValidateIntToPtrUses`（732） | 预 PM + 第 1–3、8 步 |
| 内存规划 | `PlanMemory`（211，legacy）；modern 为手写 `PlanMemoryModern`（`lib/PTO/Transforms/PTOPlanMemoryModern.cpp:1809`，不在 `Passes.td`）；`PTOResolveReservedBuffers`（525）、`PTOResolveBufferSelect`（748）、`PTOMaterializeImplicitTmp`（195）、`PTORematerializeFixpipeVectorQuant`（179） | 第 6–8、10 步 |
| 同步 | `PTOInsertSync`（27）、`PTOInjectBarrierAllSync`（44）、`PTOBufidSync`（63）、`PTOGraphSyncSolver`（92） | 第 9 步，四选一（4.6） |
| TileFusion | `FusionPlan`（269）、`OpScheduling`（302）、`PTOMarkLastUse`（314）、`PTOFusionRegionGen`（326）；lowering 后的 `PTOLowLevelLoopFusion` 等在 vpto 后端（`tools/ptoas/ptoas.cpp:3171`） | 第 5 步（A5 前端）；后段见第 5 章 |
| TileOp / 模板展开 | `InsertTemplateAttributes`（548）在共享管线；`ExpandTileOp`（564）在 `lowerPTOToVPTOBackend`（`tools/ptoas/ptoas.cpp:3166`）；随后 `PTOInlineLibCall`（676） / `FoldTileBufIntrinsics`（591） | 第 4 步只贴候选；真正展开在第 5 章 |

`ConvertToPTOOp`（`Passes.td:118`）、`InferPTOMemScope`（`Passes.td:173`）、`PTOVerifyTFree`（`Passes.td:716`）等定义在 `Passes.td` 里，但当前 `compilePTOASModule` 主管线并不装入（`PTOVerifyTFree` 仅保留一行注释，`tools/ptoas/ptoas.cpp:3639`）。查「这个 Pass 会不会跑」时，以 4.3 的 12 步表为准，不要只看 TableGen 清单。

### 4.5 `--pto-level` 分档与 README 冲突

`--pto-level` 取值 `level1|level2|level3`，默认 `level2`（`defaultBuildLevel`，`tools/ptoas/ptoas.cpp:722`；选项表见 2.5）。它**不是**「优化档越高越激进」——level3 的含义是 **调用方自己规划本地内存，编译器跳过 PlanMemory**。

| 档 | PlanMemory | alloc / reserve_buffer 约束 | 其它 |
| --- | --- | --- | --- |
| level1 | 跑（第 7 步） | `alloc_tile` **禁止**带 `addr`（`tools/ptoas/ptoas.cpp:3569-3577`）；`reserve_buffer` 不允许显式 `base`（`validateReserveBufferLevelRules`，`tools/ptoas/ptoas.cpp:802-824`） | `--enable-op-fusion` 被忽略并告警（`tools/ptoas/ptoas.cpp:3419-3422`），融合路径要求 level≠1（`tools/ptoas/ptoas.cpp:3441-3443`） |
| level2（默认） | 跑 | 同 level1：地址由 PlanMemory 分配 | 融合在 A5 上可用 |
| level3 | **跳过**（`tools/ptoas/ptoas.cpp:3699` `if (effectiveLevel != PTOBuildLevel::Level3)`） | 每个 `alloc_tile` / `alloc_multi_tile` **必须**带 `addr`（`tools/ptoas/ptoas.cpp:3548-3568`）；`reserve_buffer` 必须 `auto = false` 且显式 `base`（`tools/ptoas/ptoas.cpp:827-831`） | `pto.tassign` **仅** level3 合法（`tools/ptoas/ptoas.cpp:3498-3502`） |

level3 输入长这样——物理基址写在 IR 里，PlanMemory 不再填。语料 `test/lit/pto/multi_tile_level3_aligned_slot_stride.pto:13-21`：

```text
module {
  func.func @aligned_l0_slot_stride() attributes {pto.entry} {
    %base = arith.constant 0 : i64
    %slot = arith.constant 1 : index
    %multi = pto.alloc_multi_tile addr = %base
      : !pto.multi_tile_buf<... count=2>
    %selected = pto.multi_tile_get %multi[%slot] : ...
    ...
  }
}
```

`addr = %base` 就是 level3 的契约；同一文件的 RUN 行是 `--pto-level=level3 --emit-pto-ir`，FileCheck 盯 `pto-resolve-buffer-select` 之后的地址计算。

> **冲突标注（代码优先）。** `README.md:275` 写「level3 会禁用 PlanMemory/InsertSync」。
> 这是 **PTODSL 默认组合** 的概括，不是 C++ 里的单一 gate：
>
> - **PlanMemory**：C++ 确实在 `tools/ptoas/ptoas.cpp:3699` 按 `effectiveLevel != Level3` 跳过。这一半 README 说对了。
> - **InsertSync**：C++ **没有**「level3 ⇒ 关闭 InsertSync」的分支。`createPTOInsertSyncPass` 只看 `--enable-insert-sync`（`tools/ptoas/ptoas.cpp:3723-3728`）。level3 与 InsertSync 可以同时开——lit 就是这么写的，例如 `test/lit/pto/plan_memory_reused_tstore_sync.pto:14` 的 `ptoas --pto-level=level3 --enable-insert-sync`。
> - 与 InsertSync 真正互斥的是 **`pto.tassign`**：存在该 op 时强制 `--enable-insert-sync` 关闭（`tools/ptoas/ptoas.cpp:3504-3508`，错误信息 `"pto.tassign requires --enable-insert-sync to be disabled"`）；其它三种同步模式同样被拒绝（`tools/ptoas/ptoas.cpp:3519-3533`）。
> - PTODSL `mode="explicit"` 把两件事绑在一起：`_effective_pto_level` 返回 `"level3"`，`_effective_insert_sync` 在调用方未显式传 `insert_sync` 时返回 `False`（`ptodsl/ptodsl/_runtime/native_build.py:73-80`；消费点 `native_build.py:216-220`）。`_run_ptoas` 只在 `insert_sync is True` 时才往命令行加 `--enable-insert-sync`（`native_build.py:60-61`），因此 explicit 模式下默认不会打开 InsertSync。第 3 章 3.4 已提过这组默认值。
>
> 读 README 时请把它理解成「explicit / level3 工作流通常既跳过 PlanMemory、也不开 InsertSync」，排查 C++ 行为时以 `ptoas.cpp:3699` 与 `--enable-insert-sync` 开关为准。

### 4.6 四种同步模式（互斥）

四个 CLI 开关默认全关（定义见 2.5：`tools/ptoas/ptoas.cpp:524/542/552/558`），主管线用 if-else 链 **至多装一个**（`tools/ptoas/ptoas.cpp:3723-3757`）。编译前还有一次计数校验：四个 flag 同时开超过一个就报 `"mutually exclusive"`（`tools/ptoas/ptoas.cpp:3510-3518`）。一个都不开则第 9 步为空，IR 里已有的同步保持原样。

| 模式 | CLI | 装入点（`ptoas.cpp`） | 正常 Pass | `--emit-pto-ir` 退化 | 职责（一句话） |
| --- | --- | --- | --- | --- | --- |
| InsertSync | `--enable-insert-sync` | 3728 | `createPTOInsertSyncPass` | `SerialAutoSyncPass::InsertSync`（3724-3726） | 分析 Cube / Vector / MTE 数据依赖，插入 set/wait（`Passes.td:27-32`） |
| BufidSync | `--enable-bufid_sync` | 3737 | `createPTOBufidSyncPass` | `SerialAutoSyncPass::Bufid`（3731-3733） | **仅 A5**：交叉流水局部 buffer 依赖改写成 `get_buf`/`rls_buf`（`Passes.td:63-69`） |
| BarrierAll | `--enable-inject-barrier-all-sync` | 3744 | `createPTOInjectBarrierAllSyncPass` | `SerialAutoSyncPass::BarrierAll`（3740-3742） | 在每个有内存副作用的 pipe op 前保守插入 `PIPE_ALL` barrier（`Passes.td:44-51`） |
| GraphSolver | `--enable-graph-sync-solver` | 3754 | `createPTOGraphSyncSolverPass` | `SerialAutoSyncPass::GraphSolver`（3747-3750） | 图着色式 set/wait/barrier 求解（`Passes.td:92-98`） |

`--emit-pto-ir` 走模块级 `SerialAutoSyncPass`（`tools/ptoas/ptoas.cpp:1346-1393`），按函数串行跑同一个底层 Pass，避免 nested adaptor 与 dump 顺序打架。正常编译则 `addNestedPass<func::FuncOp>`。同步必须发生在 `PTOResolveBufferSelect` 之前（注释写明 slot 身份，`tools/ptoas/ptoas.cpp:3717-3721`），所以它是第 9 步而不是第 10 步之后。

`pto.tassign` 与全部四种模式都不兼容（InsertSync：`tools/ptoas/ptoas.cpp:3504`；BarrierAll：3519；GraphSolver：3524；BufidSync：3529）。手工 `tassign` 的 IR 需要调用方自己保证同步。

### 4.7 A5 专属 gate

三个 CLI 在非 A5 上是硬错误，拦在主管线 `pm` 构建之前（`tools/ptoas/ptoas.cpp:3395-3418`）：

| 选项 | 判定 | 错误信息 |
| --- | --- | --- |
| `--enable-bufid_sync` | `enableBufidSync && arch != "a5"`（3395-3398） | `--enable-bufid_sync requires --pto-arch=a5` |
| `--vpto-scheduler` | 模式不是 `off` 且 `arch != "a5"`（3399-3402） | `--vpto-scheduler requires --pto-arch=a5` |
| `--enable-op-fusion=true` | `requestedEnableOpFusion && arch != "a5"`（3415-3418） | `--enable-op-fusion=true requires --pto-arch=a5` |

补充两点，避免把「能开」和「会跑」混为一谈：

- **op-fusion 还要 level≠1** 才会真正装入第 5 步（`enableA5FusionPath`，`tools/ptoas/ptoas.cpp:3441-3447`）。level1 + `--enable-op-fusion=true` 在 A5 上只告警、不报错（`tools/ptoas/ptoas.cpp:3419-3422`）。emitc / vpto 前端融合 Pass 集合不同，见 4.3 第 5 步；vpto lowering 之后还有一段融合收尾（`tools/ptoas/ptoas.cpp:3171`），留到第 5 章。
- **`--vpto-scheduler` 本身不插入共享 `pm`**。它在 `prepareVPTOForEmission` 尾段、发射校验之前才装调度 Pass（`tools/ptoas/ptoas.cpp:3137-3143`，见 2.5）。本章列出它只因为它与另两个选项共用同一组 A5 入口检查。

`--enable-unroll-after-loop-fusion` 另有 A5 + op-fusion + vpto 三重约束（`tools/ptoas/ptoas.cpp:3424-3434`），属于融合收尾，不在共享阶段展开。

### 4.8 小结（分叉到第 5 章）

- **跟读顺序**：`include/PTO/Transforms/Passes.td`（Pass 是什么）→ `registerPTOASPassesAndCLOptions`（`tools/ptoas/ptoas.cpp:347`，怎么注册）→ `compilePTOASModule`（`tools/ptoas/ptoas.cpp:3355`，怎么编排）→ 4.3 的 12 步表（跑了哪些）。
- **level3 ≠ 最高优化档**：它跳过 PlanMemory、要求显式 `addr`。InsertSync 是否开启看 `--enable-insert-sync`（以及 PTODSL `mode="explicit"` 的默认值），不是 level3 的 C++ 副作用。与 `README.md:275` 的差异以 4.5 的冲突标注为准。
- **同步四选一、A5 三门禁** 都发生在共享 `pm` 构建前或第 9 步；配错 arch / 同时开两个同步模式会在进入 Pass 之前失败。
- 共享 `pm` 的出口仍是 PTO IR。`--emit-pto-ir` 在此处结束；否则 `tools/ptoas/ptoas.cpp:3794` 按 effective backend 走进 emitc 的 `emitcPM` 或 vpto 的 `runVPTOBackendPipeline`（内含 VMI 层）。第 5 章从这个分叉点写起。


## 5. 后端分叉（5.1 EmitC 路径 / 5.2 VPTO 路径含 VMI 层 / 5.3 mix-backend）

第 4 章把共享主管线写到 `tools/ptoas/ptoas.cpp:3794` 的 emitc / vpto 分叉；分叉图与 job 编排见 1.4 / 2.4。本章从该分叉点写起，把两条后端的 lowering 与发射走完。铁律不变：**`--pto-backend` 只接受 `emitc|vpto`**（`tools/ptoas/ptoas.h:44`，解析 `tools/ptoas/driver.cpp:278`）。VMI 是 VPTO 路径**内部的 IR 层**，不是第三条 CLI 后端；mix-backend 是「子模块各自走 emitc 或 vpto、再把 fatobj 链在一起」的组合模式，也不是第三后端。一份 `.pto` 里可以同时出现 tile 级 PTO、VMI、VPTO 微指令，哪一层能喂哪条后端见 3.2.1 / 6.6，各层支持范围见 3.2.2 / 6.7。

### 5.1 EmitC 路径

**输入 IR 形态。** 共享 `pm` 跑完后仍是 PTO dialect：tile alloc 已带物理地址，`multi_tile_get` 已展开（见 4.8）。分叉前再做一次 seam IR dump（`tools/ptoas/ptoas.cpp:3823-3828`），然后对 provenance location 做两次收窄（`narrowUnusedMultiResultProvenanceLocs` / `splitDerivedSingleResultProvenanceLocs`，`tools/ptoas/ptoas.cpp:3830-3831`），才进入 EmitC 专属管线。

EmitC **只消化 tile 级 PTO**。`PTOToEmitCTypeConverter` 转换 `TileBufType` / `TensorViewType`（`lib/PTO/Transforms/PTOToEmitC.cpp:1010-1024`），pattern 把 `pto.tadd` 写成 `TADD(...)`（`PTOToEmitC.cpp:6877-6880`）。同一文件里 **没有** `vmi`、`VaddOp`、`VldsOp` 的 conversion。手写 `pto.vmi.*` 或物理 `pto.vadd` / `pto.vlds` 喂默认 emitc 会在 `EmitPTOManualPass`（`PTOToEmitC.cpp:13701`）留下未转换 op 而失败——这类输入必须走 5.2。三层对照表见 3.2.1 / 6.6。

最简输入仍是 `test/lit/pto/empty_func.pto`——空 `func.func @hello`，默认 `--pto-backend=emitc`，FileCheck 盯生成的 `AICORE void hello()`。带 tile 的输入例如 `test/lit/pto/tmrgsort_format2_subview_valid_cols.pto`，FileCheck 要求产物含 `#include "pto/pto-inst.hpp"`。

**处理：`emitcPM`。** 另建 PassManager `emitcPM`（`tools/ptoas/ptoas.cpp:3833`），只装三步：

| 步 | Pass | 行号（`ptoas.cpp`） | 职责 |
| --- | --- | --- | --- |
| 1 | `createEmitPTOManualPass(PTOArch::A3\|A5)` | 3835-3839 | PTO → EmitC 方言转换。A2/A3 走 A3 变体，否则 A5。实现 `lib/PTO/Transforms/PTOToEmitC.cpp:13701`（`EmitPTOManualPass`），工厂 `PTOToEmitC.cpp:14287`。 |
| 2 | `FormEmitCExpressionsCompatPass` | 3840 | 手写 Pass（定义 `tools/ptoas/ptoas.cpp:184-259`）：把带 `CExpression` trait 的 op 收成 `emitc.expression`，并按 LLVM 19 发射器语义折叠；含条件运算符的表达式强制 `doNotInline`，避免三元表达式被摊进算术而改语义。 |
| 3 | CSE | 3841 | 去掉 lowering 后重复的 EmitC 表达式。 |

`emitcPM.run`（`tools/ptoas/ptoas.cpp:3846`）之后、译 C++ 之前还有一段 **EmitC IR 收尾**（`tools/ptoas/ptoas.cpp:3851-3860`）：把函数形参名字提示贴回、再拆 provenance location、删空的 `emitc.expression`、物化控制流操作数、规范化整数属性、按依赖重排函数、标注 provenance hint。多 block 函数会让 `translateToCpp` 把变量声明提到函数顶部（`shouldDeclareVariablesAtTop`，`tools/ptoas/ptoas.cpp:3085-3089`、消费点 `tools/ptoas/ptoas.cpp:3868`）。

**`pto/pto-inst.hpp` include 机制。** `EmitPTOManualPass::runOnOperation` 做的第一件事就是往模块 body 开头插两条 EmitC 语句（`lib/PTO/Transforms/PTOToEmitC.cpp:13769-13772`）：

```cpp
builder.create<emitc::IncludeOp>(
    loc, "pto/pto-inst.hpp", /*is_standard_include=*/false);
builder.create<emitc::VerbatimOp>(
    loc, builder.getStringAttr("using namespace pto;"));
```

`is_standard_include=false` 决定译出的是 `#include "pto/pto-inst.hpp"`（引号 include），不是尖括号。生成的 C++ 通过这套 **pto-isa C++ 头文件库**调用设备内建；pto-isa 是外部依赖，路径由 `PTO_ISA_PATH` / `PTO_ISA_ROOT` 提供（探测 `tools/ptoas/ObjectEmission.cpp:261-264`，头文件检查 `hasPTOISAHeader`，`ObjectEmission.cpp:225`）。仓库里现成的产物形态见 `test/samples/MatMul/tmatmulk.cpp:9-11`：

```cpp
#include "pto/pto-inst.hpp"
using namespace pto;
__global__ AICORE void RunTMATMULSplitK(...) {
```

**处理：`translateToCpp` 与 marker 后处理。** `mlir::emitc::translateToCpp`（`tools/ptoas/ptoas.cpp:3869`）把 EmitC IR 写成字符串。随后一串文本重写把 lowering 留下的 marker 换成合法 C++ 成员调用（`tools/ptoas/ptoas.cpp:3875-3886`）：

| 函数 | 作用 |
| --- | --- |
| `rewriteTileGetSetValueMarkers`（`ptoas.cpp:1877`） | `PTOAS__TILE_SET_VALUE` 等 → `SetValue` / `GetValue` / `data` 等成员 |
| `rewriteAsyncEventMarkers`（`ptoas.cpp:1889`） | 异步 event 的 Wait/Test，以及 prefetch session 字段 |
| `rewritePtrScalarMarkers` / `rewriteScalarGMStoreFlushMarkers` / `rewriteEventIdArrayMarkers` / `rewriteGlobalTensorMetadataMarkers` | 指针标量、GM store flush、event id 数组、GlobalTensor 元数据 |
| `pto::rewriteLastUseMarkersInCpp`（`include/PTO/Transforms/CppPostprocess.h:17`，实现 `lib/PTO/Transforms/CppPostprocess.cpp`） | last-use 标注，供 A5 融合路径的 `MarkLastUse` 落到 C++ |
| `rewriteAddPtrTraceMarkers` / `rewriteMalformedVerbatimSemicolons` / `rewriteScalarConstantDecls` / `rewriteHoistedGlobalTensorDecls` / `rewriteNameHintMarkers` | 跟踪、分号修复、常量声明、提升的 GlobalTensor、名字提示 |

**输出 IR 形态。** 结果 kind 为 `Text`，`result.textOutput` 即完整 C++ 源码（`tools/ptoas/ptoas.cpp:3888-3889`）。单后端由 `EmitCBackendJob::run`（`tools/ptoas/driver.cpp:1037`）收回 Text，driver 再 `writeTextOutput` 落到 `-o`（见 2.2）。mix 子模块不会把这段 C++ 当最终产物，而是立刻交给 `emitFatobjCCE`（见 5.3）。

> **冲突标注（代码优先）。** `README.md:11` 写代码生成「支持将 PTO IR 下降到 `EmitC` / `Linalg` Dialect」。
> 在 `tools/ptoas/ptoas.cpp` 与 `tools/ptoas/driver.cpp` 中检索 `linalg` **零命中**；`emitcPM` 只装 `EmitPTOManual` + `FormEmitCExpressionsCompatPass` + CSE，`EmitPTOManualPass` 的 dependent dialect 是 emitc / func / arith / memref / affine / cf / pto（`lib/PTO/Transforms/PTOToEmitC.cpp:13711-13714`），没有 linalg。
> **EmitC 路径不经过 linalg。** 读 README 时请把「EmitC / Linalg」理解为过时并列写法；排查 lowering 以 `emitcPM`（`tools/ptoas/ptoas.cpp:3833`）为准。

### 5.2 VPTO 路径（含 VMI 层）

VPTO 路径的入口是 `runVPTOBackendPipeline`（`tools/ptoas/ptoas.cpp:3271`），由 `compilePTOASModule` 在 effective backend 为 vpto 时调用（`tools/ptoas/ptoas.cpp:3794-3815`）。有一条快路径：vpto **且没有**待展开 tile op 时，共享 `pm` 整段跳过（`tools/ptoas/ptoas.cpp:3609-3619`，见 4.3），直接进本函数——给已经是低层 / 手写 VMI 的输入用。

**VMI 不是第三条 CLI 后端。** 它是本路径内部的 IR 层：类型 mnemonic 为 `vmi.vreg` / `vmi.mask`（`include/PTO/IR/VMITypeDefs.td:18-39`），op 前缀 `pto.vmi.*`。`README.md:278-280` 写明「VPTO backend 总是启用 VMI -> VPTO 语义 pipeline；public function signature 不能直接暴露 `!pto.vmi.*` 类型」。概念与实现细节不在本章复述，见 `docs/designs/vmi-introduction.md`、`docs/designs/vmi-implementation-manual.md`。本章只讲它在管线中的位置。

#### 5.2.1 VMI 如何进入

两条入口，产物都是带 `!pto.vmi.vreg` / `!pto.vmi.mask` 的 PTO 模块：

1. **PTODSL 正式 API。** `ptodsl/ptodsl/_vmi_namespace.py` 模块 docstring 自称 `"Public PTODSL namespace for formal VMI APIs"`（`_vmi_namespace.py:8`）。Python 侧 `pto.vmi.vload` / `vstore` / `vadd` 等包装最终发 `pto.vmi.*` MLIR op。
2. **手写 `.pto`。** lit 语料在 `test/lit/vmi_new/`，端到端用例在 `test/vpto/cases/vmi_new/`。CLI 冒烟 `test/lit/vmi_new/vmi_ptoas_cli_pipeline.pto:12-23`：

```text
module attributes {pto.target_arch = "a5"} {
  module attributes {pto.backend = "vpto", pto.kernel_kind = #pto.kernel_kind<vector>} {
    func.func @vmi_ptoas_cli_pipeline(
        %scalar: f32,
        %dst: !pto.ptr<f32, ub>,
        %offset: index) {
      %value = pto.vmi.vbrc %scalar
          : f32 -> !pto.vmi.vreg<128xf32>
      pto.vmi.vstore %value, %dst[%offset]
          : !pto.vmi.vreg<128xf32>, !pto.ptr<f32, ub>
      return
    }
  }
}
```

`RUN` 行是 `ptoas --pto-arch=a5 --pto-backend=vpto --emit-vpto`；FileCheck 要求出口变成 `pto.vdup` / `pto.vsts`，且 **不再出现** `pto.vmi.` / `!pto.vmi.`。更完整的 kernel 形态见 `test/vpto/cases/vmi_new/mask-select-store/kernel.pto:44-57`：在 `pto.vecscope` 里 `vload` → `create_mask` → `vadd` → `vsel` → `vstore`。

第三种入口是 **已经手写的物理 VPTO 微指令**（`pto.vadd` / `pto.vlds` / `pto.vecscope`）。VMI 语义管线对它们基本空转，随后 `prepareVPTOForEmission` 按发射契约校验。

Tile 级 `pto.tadd` 等并不手写 VMI：它们先在 `lowerPTOToVPTOBackend`（`tools/ptoas/ptoas.cpp:3149`）里经 `ExpandTileOp`（`tools/ptoas/ptoas.cpp:3166`）展开成模板函数体。模板内部是 **物理 VPTO 微指令**（`pto.vecscope`、`pto.vlds`、`pto.vadd`、`pto.vsts`，`include/PTO/Transforms/Passes.td:564-571`），不是 `pto.vmi.*`。A2/A3 在 `LowerPTOToUBufOps` 之后提前 `return`（`tools/ptoas/ptoas.cpp:3157-3163`），不走 TileOp 展开与后续 A5 融合收尾。一份 `.pto` 混有这三层时的汇合顺序见 3.2.1。

#### 5.2.2 `runVPTOBackendPipeline` 骨架

**输入 IR 形态。** 共享阶段出口的 PTO IR，或快路径下的手写 VMI 模块。

**处理。** `runVPTOBackendPipeline`（`tools/ptoas/ptoas.cpp:3271-3300`）顺序固定：

1. 快路径补一次 `PTOCanonicalizeIR`（`tools/ptoas/ptoas.cpp:3275-3277`）。
2. `VPTOSplitCVModulePass` → `VPTONormalizeContainerPass`（`tools/ptoas/ptoas.cpp:3278-3279`）：按 cube/vector 拆子模块、规范化容器。
3. 若有待展开 tile op，跑 `lowerPTOToVPTOBackend`（`tools/ptoas/ptoas.cpp:3280-3282`）：`LowerPTOToUBufOps`（`ptoas.cpp:3157`）、（非 A2/A3）`ExpandTileOp`（`ptoas.cpp:3166`）+ inline + `FoldTileBufIntrinsics`（`ptoas.cpp:3170`），A5 融合收尾见 `tools/ptoas/ptoas.cpp:3171-3200`（第 4 章 4.3 第 5 步的后段）。
4. nest 到 kernel `ModuleOp`：先给 `pto.simt_entry` 物化 `no_inline`（`ApplySIMTEntryNoInlinePass`，定义 `tools/ptoas/ptoas.cpp:163`，装入 `ptoas.cpp:3289`），再 `Inliner`（`ptoas.cpp:3290`），让 private helper 参与同一次 layout 决策。
5. **`appendVMISemanticPipeline`**（`tools/ptoas/ptoas.cpp:3291`，实现 `tools/ptoas/ptoas.cpp:3303-3338`）——VMI → VPTO 的固定语义管线，始终启用。
6. `prepareVPTOForEmission`（`tools/ptoas/ptoas.cpp:3292`，实现 `tools/ptoas/ptoas.cpp:3093-3147`）：同步合法化、循环展开、SIMT 物化、指针规范化、SoftLib 展开等发射前收尾。

同一模块里若混有 tile / VMI / 已有物理微指令：第 3 步先把 tile 展开成 VPTO 微指令，第 5 步再把残留 `pto.vmi.*` 降成 VPTO 微指令，第 6 步看到的已经是统一的物理 VPTO。作者侧对照表见 3.2.1。

**A5 scheduler 的插入点。** `--vpto-scheduler` 不进共享 `pm`（见 4.7）。它在 `prepareVPTOForEmission` 尾段、`PTOExpandSoftLib` / inline / Canonicalizer / CSE **之后**、`VPTOCombineReductions` 与 `PTOValidateVPTOEmissionIR` **之前**才装入（`tools/ptoas/ptoas.cpp:3137-3146`）。模式 `analyze|on` 映射到 `createVPTOSchedulerPass` 的 `"analyze"` / `"on"`（`tools/ptoas/ptoas.cpp:3138-3142`）；非 A5 在主管线入口就被拒绝（`tools/ptoas/ptoas.cpp:3399-3402`）。

#### 5.2.3 `appendVMISemanticPipeline` 固定序列

下表按 `tools/ptoas/ptoas.cpp:3303-3338` 的装入顺序。Canonicalizer / CSE 是阶段之间的清理，不算独立语义 Pass，但仍按代码出现。

| 序 | Pass | 行号 | 职责（一句话） |
| --- | --- | --- | --- |
| 1 | `VMINormalizeSignlessIntToUnsigned` | 3306-3307 | 给符号敏感的 VMI op 物化 unsigned carrier，避免后续 verifier / layout 看见 signless 元素类型 |
| 2 | `VMILowerUnifiedToLegacy` | 3311 | 展开 unified VMI（如 grouped `vci` → 只产 contiguous 的 legacy `group_iota`） |
| 3 | Canonicalizer | 3312 | 清理 |
| 4 | `VMILegalizeArithSelect` | 3313 | 合法化 `arith.select` 与 VMI 值的交互（本管线共跑三次：3313 / 3320 / 3335） |
| 5 | `PTOValidateVMIIR` | 3314 | layout 分配前的 VMI IR 校验 |
| 6–7 | Canonicalizer + CSE | 3315-3316 | 清理 |
| 8 | `VMIPreAssignmentCombine` | 3317 | layout 前合并可合并的 VMI 模式 |
| 9–10 | Canonicalizer + CSE | 3318-3319 | 清理 |
| 11 | `VMILegalizeArithSelect` | 3320 | 第二次 select 合法化 |
| 12 | `VMIMaskGranularityAssignment` | 3321 | 给 mask 选定粒度（pred / b8 / b16 / b32） |
| 13 | `VMILayoutRematerializeWeakProducers` | 3322 | 弱 producer 重物化，避免错误的 layout 共享 |
| 14 | `VMILayoutAssignment` | 3323 | **核心**：给 `!pto.vmi.vreg` / `!pto.vmi.mask` 分配物理 layout |
| 15–16 | Canonicalizer + CSE | 3324-3325 | 清理 |
| 17 | `VMILayoutRematerialize` | 3326 | 按 consumer 需求重物化 layout |
| 18–19 | Canonicalizer + CSE | 3327-3328 | 清理 |
| 20 | `VMILayoutFold` | 3329 | 折叠多余的 layout 转换 |
| 21–22 | Canonicalizer + CSE | 3330-3331 | 清理 |
| 23 | `VMILayoutSinkMaterialization` | 3332 | 下沉 materialization，靠近真正的 consumer |
| 24–25 | Canonicalizer + CSE | 3333-3334 | 清理 |
| 26 | `VMILegalizeArithSelect` | 3335 | 第三次 select 合法化 |
| 27 | `PTOValidateVMILayoutIR` | 3336 | layout 已分配的 VMI IR 校验 |
| 28 | **`VMIToVPTO`** | 3337 | layout-assigned VMI → 物理 VPTO |

**`VMIToVPTO` 转换机制。** TableGen 定义 `include/PTO/Transforms/Passes.td:1260-1282`，summary 为 `"Convert layout-assigned VMI IR to physical VPTO IR"`：用 MLIR 原生 1:N dialect conversion，把 VMI 聚合数据/mask 类型拆成有序的物理 VPTO register / mask 列表，并改写控制流与函数签名。Pass 本体 `lib/PTO/Transforms/VMIToVPTO.cpp:14381-14412`：先 `verifyVMIToVPTOInputIR` / `verifySupportedVMIToVPTOOps`，再 `applyPartialOneToNConversion` + `VMIToVPTOTypeConverter`（`VMIToVPTO.cpp:14401`），最后 `verifyNoResidualVMIIR`。工厂 `createVMIToVPTOPass` 在 `VMIToVPTO.cpp:14416`。

独立跑该 Pass 的最小例子是 `test/lit/vmi_new/vmi_to_vpto_broadcast.pto`：输入已带 `#pto.vmi.layout<contiguous>` 的 `pto.vmi.vbrc`，经 `pto-test-opt -vmi-lower-unified-to-legacy -vmi-to-vpto` 变成两条 `pto.vdup` + `pto.pset_b32 "PAT_ALL"`，且不再残留 `pto.vmi.`。

**输出 IR 形态（语义管线出口）。** 模块里是物理 VPTO（`pto.vdup` / `pto.vlds` / `pto.vsts` / `pto.vecscope` 等），类型是 `!pto.vreg` / `!pto.mask`，不再有 `!pto.vmi.*`。随后 `prepareVPTOForEmission` 再做发射合法性校验。

#### 5.2.4 `emitVPTOBackendResult` 三分支

管线跑完由 `emitVPTOBackendResult`（`tools/ptoas/ptoas.cpp:3221-3269`）按 CLI 三选一。`--emit-vpto` / `--emit-vpto-llvm-ir` 在非 vpto 后端下直接报错（`tools/ptoas/ptoas.cpp:3382-3388`）。

| 分支 | 开关 | 代码 | 产物 |
| --- | --- | --- | --- |
| VPTO IR 文本 | `--emit-vpto` | `ptoas.cpp:3224-3231` | kind=`Text`：把最终 VPTO `ModuleOp` `print` 到 `-o` |
| LLVM IR 文本 | `--emit-vpto-llvm-ir` | `ptoas.cpp:3233-3244` | kind=`Text`：`lowerVPTOModuleToLLVMIRText` 输出 `.ll` |
| 对象模式（默认） | 两开关都关 | `ptoas.cpp:3246-3268` | kind=`VPTOObject`：可选 host stub 源码 + **cube / vector 两个 LLVM 模块** |

对象模式细节：

- host stub 仅当 `emitHostStub` 为真时生成（`tools/ptoas/ptoas.cpp:3250-3255`）。driver 侧判据是模块里是否存在 PTO entry 函数（`hasPTOEntry`，`tools/ptoas/driver.cpp:863`；单后端 `tools/ptoas/driver.cpp:1097`，child `tools/ptoas/driver.cpp:972`）。stub 源码由 `emitVPTOHostStubSource`（`tools/ptoas/VPTOHostStubEmission.cpp`）遍历 entry、剥 `_mix_aiv` / `_mix_aic` 得到逻辑名（`VPTOHostStubEmission.cpp:34-42`），参数映射如 `PtrType` / `MemRefType` → `"__gm__ void *"`（`VPTOHostStubEmission.cpp:75-80`）。
- `lowerVPTOModuleToLLVMModules`（声明 `include/PTO/Transforms/VPTOLLVMEmitter.h:58-61`）产出 `result.vptoCubeModule` / `result.vptoVectorModule`（`tools/ptoas/ptoas.cpp:3257-3264`）。
- 发射器按 CANN 版本分派：`lib/PTO/Transforms/VPTOLLVMEmitterDispatcher.cpp:52-65`。`usesCANN900Lowering`（`VPTOLLVMEmitterDispatcher.cpp:16-21`）在 **非 C220** 且 CANN **≥ 正式 9.0.0**（`CANNVersion::release(9, 0, 0)`）时走 `lowerVPTOModuleToLLVMModulesCANN900`，否则 `lowerVPTOModuleToLLVMModulesBeta1`。A2/A3 的 march 是 `dav-c220-vec`（`tools/ptoas/ptoas.cpp:3215-3217`），因此走 Beta1 路径。这与下一节 ABI 后缀的阈值（9.0.0-**beta.2**）不是同一条线。

文本两分支在 `VPTOBackendJob::run` 里直接成功返回（`tools/ptoas/driver.cpp:1103-1104`），由 driver `writeTextOutput` 落盘。对象模式要求显式 `-o`（`tools/ptoas/driver.cpp:1111-1115`），再 `emitVPTOLLVMFatobj`（`tools/ptoas/driver.cpp:1128`）→ `emitFatobjLLVM`。

#### 5.2.5 对象模式：fatobj 五步

`emitFatobjLLVM`（`tools/ptoas/ObjectEmission.cpp:1117-1151`）把 cube/vector LLVM 模块与 host stub 收成**单个** fatobj。工件封装在 `VPTOFatobjArtifacts`（`tools/ptoas/ObjectEmission.cpp:328-489`）。CLI 单 vpto 路径的五步是：

```text
  cube LLVM module          vector LLVM module         host stub .cpp
         |                         |                         |
         v                         v                         |
   write .ll                  write .ll                      |
         |                         |                         |
         v                         v                         |
  bisheng -c -x ir          bisheng -c -x ir                 |
  --cce-aicore-arch=        --cce-aicore-arch=               |
    dav-c310-cube             dav-c310-vec                   |
         |                         |                         |
         |                    可选 VFSIMT patch               |
         |                         |                         |
         +-----------+-------------+                         |
                     v                                       |
              ld.lld -r 合并 device .o                       |
                     |                                       |
                     +----------------+----------------------+
                                      v
                         BiSheng cc1 -fcce-fatobj-compile
                         -fcce-include-aibinary <merged.o>
                                      |
                                      v
                              单个 fatobj 写到 -o
```

逐步对应代码：

1. **写 host stub。** `emitStubSource`（`ObjectEmission.cpp:333-340`）把 stub C++ 落到临时 `.cpp`。
2. **cube 设备对象。** `emitCubeObject`（`ObjectEmission.cpp:350-364`）→ `emitVPTOCubeDeviceObject`（`ObjectEmission.cpp:1096`）：先按 ABI 后缀改 LLVM 符号（`applyVPTOLLVMABINames`，`ObjectEmission.cpp:1056`），写 `.ll`，再 `compileDeviceLLVMToObject`（`ObjectEmission.cpp:528-580`）调 `bisheng`：`--cce-aicore-only -O2 -dc -cce-bitcode-is-aicore -c -x ir`，arch 默认 `dav-c310-cube`（`getTargetCPU`，`ObjectEmission.cpp:305-310`）。
3. **vector 设备对象。** `emitVectorObject`（`ObjectEmission.cpp:366-405`）同理，arch `dav-c310-vec`；随后按 `--vpto-fix-vfsimt-size` 可选 `verifyAndPatchVFSIMTSize`（`ObjectEmission.cpp:396-403`），修 0xffff VF_SIMT 尺寸。
4. **合并 device object。** `mergeDeviceObjects`（`ObjectEmission.cpp:407-426`）用 **`ld.lld`**（`toolchain.ldLldPath`）`-m aicorelinux -r --allow-multiple-definition`（`ObjectEmission.cpp:783-807`）。
5. **host stub → fatobj。** `compileHostStubToFatobj`（`ObjectEmission.cpp:441-448`，调用点 `ObjectEmission.cpp:1147`）转发 `compileHostStubToObject`（`ObjectEmission.cpp:679-780`）：可执行文件是 **`toolchain.bishengCc1Path`**（`ASCEND_HOME_PATH/tools/bisheng_compiler/bin/bisheng`，`ObjectEmission.cpp:876-877`），参数含 `-cc1`、`-fcce-fatobj-compile`、`-fcce-include-aibinary <mergedDeviceObj>`（`ObjectEmission.cpp:693-768`）。**默认 CLI 单 vpto 路径用 BiSheng cc1 把 stub 与已合并的 device object 一次编成 fatobj，不走 `cce-ld`。**

`cce-ld` 出现在 `repackFatObj`（`ObjectEmission.cpp:451-477`）：它把已经编好的 host stub `.o` 与 device 再 pack 一次。该函数只被 `emitFatobjLLVMWithRuntime`（`ObjectEmission.cpp:1228`）调用，**不是** `emitFatobjLLVM` / CLI `-o` 单 vpto 路径。

**ABI 后缀。** `vptoPublicABISuffix`（`ObjectEmission.cpp:924-934`）按 CANN 是否 ≥ `kCANN900Beta2Version`（`include/PTO/Support/CANNVersion.h:61`，即 9.0.0-beta.2）切换：

| 目标 | ≥ 9.0.0-beta.2 | 更旧 |
| --- | --- | --- |
| Vector | `.vector` | `_mix_aiv` |
| Cube | `.cube` | `_mix_aic` |

`applyVPTOLLVMABINames`（`ObjectEmission.cpp:1056-1073`）给每个外部 linkage 的定义函数追加该后缀；已经带 `_mix_aiv` / `_mix_aic` / `.vector` / `.cube` 的名字跳过。这正是 kernel-side wrapper 要求 vector callee 导出 `foo.vector`、cube 导出 `foo.cube` 的来源（见 `docs/kernel_side_wrapper_fatobj_link_guide_zh.md`）。

### 5.3 mix-backend

mix 不是第三后端，而是 **两个后端的组合编译**：外层容器里每个子 `module` 带自己的 `pto.backend`，各自走完 5.1 或 5.2 产临时 fatobj，最后链成 `-o` 上的单个 fatobj。判定规则与 job 编排总览见 2.4；实现细节（mix-kernel vs mix-backend、属性继承、符号可见性）见 `docs/designs/mix-kernel-mix-backend-compile-flow.md`。本节只钉它在主编译链上的位置。

**输入 IR 形态。** backend-partitioned 容器：外层 `module` 只含子 `module`，子模块属性分叉。lit 语料 `test/lit/vpto/backend_mixed_requires_output_file.pto:11-26`：

```text
module attributes {pto.target_arch = "a5"} {
  module attributes {pto.backend = "emitc"} {
    func.func @emitc_child() attributes {pto.aicore} {
      return
    }
  }

  module attributes {
    pto.backend = "vpto",
    pto.kernel_kind = #pto.kernel_kind<vector>
  } {
    func.func @vpto_child() {
      return
    }
  }
}
```

未给文件路径的 `-o -` 会在进入编译前失败（`"mixed pto.backend fatobj mode requires an explicit file path passed with -o"`）。mix 还拒绝一切调试 IR dump flag（`tools/ptoas/driver.cpp:1281-1287`）。

**处理。** `runPTOASJobs` 在非 single-backend 时走 mix 分支（`tools/ptoas/driver.cpp:1311-1331`）：

1. **`collectChildJobs`**（`tools/ptoas/driver.cpp:1151-1188`）。对每个子 `ModuleOp`：`buildBackendChildCompileUnit` 归一化编译单元；effective backend 优先级为 CLI 覆盖 > 子模块 `pto.backend` > 默认（`tools/ptoas/driver.cpp:1175-1177`）。`PTOBackend::VPTO` → `VPTOBackendChildJob`（`tools/ptoas/driver.cpp:959`），否则 `EmitCBackendChildJob`（`tools/ptoas/driver.cpp:907`）。
2. **每个 ChildJob 产一枚临时 fatobj。**
   - EmitC child（`tools/ptoas/driver.cpp:915-950`）：`compilePTOASModule(..., EmitC)` 得到 C++ 文本（即 5.1 的出口），`emitFatobjCCE`（`tools/ptoas/ObjectEmission.cpp:1012`）写临时 `.cpp`，再 `compileCppDeviceSourceToFatobj`（`ObjectEmission.cpp:623-660`）以 `bisheng -xcce --cce-aicore-enable-tl --cce-aicore-arch=dav-c310 -dc -c` 编成 fatobj。
   - VPTO child（`tools/ptoas/driver.cpp:968-998`）：`compilePTOASModule(..., VPTO)` 得到 `VPTOObject`（即 5.2 的对象模式），`emitVPTOLLVMFatobj` → `emitFatobjLLVM`（五步同 5.2.5）。
3. **`FatobjLinkJob::run`**（`tools/ptoas/driver.cpp:1008-1030`）。至少两枚 fatobj（`tools/ptoas/driver.cpp:1014-1017`），调用 `linkFatobjs`（`tools/ptoas/ObjectEmission.cpp:1177`）→ `linkFatobjFiles`（`ObjectEmission.cpp:810-832`）：

```text
bisheng --cce-fatobj-link --cce-aicore-arch=dav-c310 -r -o <output> <fatobj>...
```

`--cce-fatobj-link` 只出现在「链接多个已有 fatobj」这一步（以及 PTODSL / tilelang_st 的宿主侧链接，见 3.4 / 3.5）。它不参与 5.2.5 的单 vpto CLI 发射。

**输出形态。** kind=`MixedObject`（`tools/ptoas/driver.cpp:1320`），最终 fatobj 已写在 `-o`；driver 对 `MixedObject` 直接返回成功（见 2.2）。各 child 的中间 C++ / LLVM 模块不落用户可见路径。

跟读建议：先看 1.4 图 3 底部的 mix 框，再进 `collectChildJobs` → 两个 `*ChildJob::run` → `FatobjLinkJob`；属性与 mix-kernel（同一 VPTO child 内再拆 cube/vector）的差别以 `docs/designs/mix-kernel-mix-backend-compile-flow.md` 为准。

## 6. 路径对比总表（arch × backend × level 矩阵）

本章是查阅表，不复述管线。选项定义与默认值见第 2 章 2.5；level / 同步 / A5 入口检查见第 4 章 4.5–4.7；emitc / vpto / mix 的 lowering 与发射见第 5 章；一份 `.pto` 里三层 IR 的写法见 3.2.1，各层支持范围见 3.2.2。这里只回答五件事：**哪组 `arch × backend × level` 能走进 `compilePTOASModule`、可选 flag 在哪种 arch 上会被硬拒绝、默认产物是什么、哪一层输入 IR 能喂哪条后端、三层各覆盖哪些硬件能力。**

判定口径（贯穿 6.2 / 6.3）：

- **✅ 可用**：CLI 接受该组 `--pto-arch` / `--pto-backend` / `--pto-level`，且 `compilePTOASModule`（`tools/ptoas/ptoas.cpp:3355`）对该 backend 有明确分叉（vpto：`ptoas.cpp:3794-3815`；emitc：`ptoas.cpp:3833-3849`）。
- **⚠️ 有条件**：组合本身能进管线，但某个可选 flag 另有 arch / level / kernel_kind 约束；条件写在单元格或 6.3，不把整条路径打成不支持。
- **❌ 不支持**：代码在主管线构建前 `return 1` 的硬错误。未在代码里找到拒绝点的组合 **不** 标 ❌。
- **⚠️ 待验证**：CLI 与 `compilePTOASModule` 都放行，但端到端对象发射 / 上板是否成功无法从入口 gate 单独证伪。这些放 6.5，不写进主矩阵的 ❌。

CLI 只承认两个后端：`enum class PTOBackend { EmitC, VPTO }`（`tools/ptoas/ptoas.h:44-47`），`--pto-backend` 解析只接受 `emitc|vpto`（`tools/ptoas/driver.cpp:278-293`）。VMI 是 VPTO 路径内部的 IR 层；mix-backend 是子模块各自查本表再链接 fatobj（第 5 章 5.3），都不是第三后端。

### 6.1 如何读表

主矩阵的三维彼此独立：`--pto-arch` 取值 `a2|a3|a5`（默认 `a3`，`tools/ptoas/ptoas.cpp:644-648`；非法值在 driver 拒绝，`tools/ptoas/driver.cpp:151-152` / `233-234`），`--pto-backend` 取值 `emitc|vpto`（默认 `emitc`），`--pto-level` 取值 `level1|level2|level3`（默认 `level2`，解析 `tools/ptoas/ptoas.cpp:3389-3394`）。**代码里没有「某 arch 禁止某 backend」或「某 level 禁止某 backend」的交叉拒绝。** 因此 3×2×3 = 18 格在默认 flag 下全部 ✅；A5 专属选项、level3 的内存契约写在备注和 6.3，不改变「能编译」这一格。

读表时把「能开」和「会跑」分开：

- 主矩阵问的是 **默认路径能不能走**。`--enable-op-fusion`、`--vpto-scheduler`、`--enable-bufid_sync` 关着时，a2/a3 与 a5 都能进对应 backend。
- 6.3 问的是 **把可选 flag 打开之后** 入口 gate 怎么判。非 A5 上打开上述三个选项是硬错误（第 4 章 4.7），但那是选项的条件，不是把整格标成不支持。
- A2 与 A3 在 `compilePTOASModule` 里走同一条 `isA2A3Arch` 分支（`tools/ptoas/ptoas.cpp:269-272`）：EmitC 用 `PTOArch::A3` 变体（`ptoas.cpp:3835-3839`；`PTOArch` 枚举本身只有 `A3` / `A5`，`include/PTO/IR/PTO.h:149-152`），VPTO 在 `LowerPTOToUBufOps` 后提前返回（`ptoas.cpp:3157-3163`）。这是路径缩短，不是 CLI 拒绝。

### 6.2 主矩阵

单元格按 6.1 的口径填写。level 三档共用同一 backend 分叉，差别只在共享 `pm` 是否跑 PlanMemory、以及 alloc 是否必须带 `addr`（第 4 章 4.5）。典型产物列的是 **单后端默认出口**；调试开关切到 VPTO IR / `.ll` 见 6.4。

| arch | backend | level1 | level2（默认） | level3 | 典型产物（默认） |
| --- | --- | --- | --- | --- | --- |
| a2 | emitc | ✅ 可用 | ✅ 可用 | ✅ 可用[^pm] | C++ 源码 |
| a2 | vpto | ✅ 可用[^a23v] | ✅ 可用[^a23v] | ✅ 可用[^pm][^a23v] | fatobj |
| a3 | emitc | ✅ 可用 | ✅ 可用 | ✅ 可用[^pm] | C++ 源码 |
| a3 | vpto | ✅ 可用[^a23v] | ✅ 可用[^a23v] | ✅ 可用[^pm][^a23v] | fatobj |
| a5 | emitc | ✅ 可用 | ✅ 可用 | ✅ 可用[^pm] | C++ 源码 |
| a5 | vpto | ✅ 可用 | ✅ 可用 | ✅ 可用[^pm] | fatobj |

A5 格子在默认 flag 下就是 ✅：融合、scheduler、bufid_sync 都是 opt-in，不开也能走完 5.1 / 5.2。把它们打开之后的条件见 6.3，不要把「A5 上才能开融合」读成「A5 格子是有条件的」。

level1 / level2 禁止 alloc 自带 `addr`（`tools/ptoas/ptoas.cpp:3569-3577`）；这是输入 IR 契约，不是「level1 格子不可用」。`pto.tassign` 仅 level3 合法（`tools/ptoas/ptoas.cpp:3498-3502`），level1/2 遇到该 op 会报错，但缺省 IR 不含 `tassign`，格子仍是 ✅。

**表注（gate 证据）**

[^pm]: level3 **跳过** PlanMemory：`if (effectiveLevel != PTOBuildLevel::Level3)`（`tools/ptoas/ptoas.cpp:3699-3713`）。同时每个 `alloc_tile` / `alloc_multi_tile` 必须带 `addr`（`tools/ptoas/ptoas.cpp:3548-3568`）。这是 level3 的预期契约（调用方自己规划本地内存），不是「level3 不可用」。InsertSync 是否开启看 `--enable-insert-sync`，C++ **没有**「level3 ⇒ 关 InsertSync」分支（第 4 章 4.5 与 `README.md:275` 的冲突标注）。

[^a23v]: A2/A3 的 VPTO lowering 在 `LowerPTOToUBufOps` 之后提前 `return`，不跑 `ExpandTileOp` 与 A5 融合收尾（`tools/ptoas/ptoas.cpp:3157-3163`）。CLI 仍接受 `--pto-backend=vpto`，`compilePTOASModule` 仍走 vpto 分叉（`ptoas.cpp:3794`）和 `runVPTOBackendPipeline`。手写低层 / 无待展开 tile 的输入与这条缩短路径对齐；tile-native 输入的端到端对象发射标 6.5，不在本表标 ❌。A2/A3 的 VPTO march 固定为 `dav-c220-vec`（`tools/ptoas/ptoas.cpp:3215-3217`）。

其它入口证据，供和 6.3 对照，避免把可选 flag 误读成主矩阵的拒绝：

1. `--pto-arch` 合法集合 `a2|a3|a5`：`isSupportedPTOASArch`（`tools/ptoas/driver.cpp:112-114`），与 `isSupportedPTOASTargetArch`（`tools/ptoas/ptoas.cpp:274-277`）一致。
2. 两后端分派：`parseDriverBackend`（`tools/ptoas/driver.cpp:278-293`）→ `runPTOASJobs`（`driver.cpp:1302-1308`）→ `compilePTOASModule` 的 vpto / emitc 分叉（`ptoas.cpp:3794-3815` / `3833-3849`）。**没有**按 arch 再切掉其中一条。
3. `--enable-bufid_sync` / `--vpto-scheduler` / `--enable-op-fusion=true` 非 A5 硬错误：`tools/ptoas/ptoas.cpp:3395-3418`（第 4 章 4.7）。这三项默认关闭，故 a2/a3 主矩阵仍为 ✅。
4. `--enable-op-fusion` 真正装入前端融合 Pass 还要 `level != level1`：`enableA5FusionPath`（`tools/ptoas/ptoas.cpp:3441-3447`）；level1 在 A5 上只告警（`ptoas.cpp:3419-3422`）。
5. `pto.tassign` 仅 level3：`tools/ptoas/ptoas.cpp:3498-3502`。

mix 模式下每个 child 用自己的 `pto.backend`（可被 CLI 覆盖）独立落入上表一格，最后 `linkFatobjs` 合成一个 fatobj（第 5 章 5.3）。mix 本身不新增 arch × level 组合。

### 6.3 选项 gate 表

下表只列 **对 arch 有入口检查、或虽全 arch 合法但与 level / backend 交叉** 的可选 flag。定义与默认值仍以 2.5 为准；同步四选一的 Pass 装入见 4.6，A5 三门禁见 4.7。主矩阵的格子不因本表变成 ❌。

| 选项 | a2 | a3 | a5 | 与 backend / level 的交叉 | 证据 |
| --- | --- | --- | --- | --- | --- |
| `--enable-bufid_sync` | ❌ 硬错误 | ❌ 硬错误 | ⚠️ 有条件：与其它三种 AutoSync 互斥；有 `pto.tassign` 时拒绝 | 与 `--pto-backend` 无关，在共享 `pm` 第 9 步装入 | 非 A5：`ptoas.cpp:3395-3398`；互斥计数 `ptoas.cpp:3510-3518`；tassign `ptoas.cpp:3529-3533`；装入 `ptoas.cpp:3730-3738` |
| `--vpto-scheduler`（`analyze\|on`） | ❌ 硬错误 | ❌ 硬错误 | ⚠️ 有条件：仅 A5 Vector kernel 真正分析；显式 cube 模块跳过且不报错 | CLI **不**按 backend 拒绝；Pass 只插入 `prepareVPTOForEmission`（VPTO 发射前）。emitc + a5 打开该 flag 不会在入口失败，但也不会跑调度 Pass | 非 A5：`ptoas.cpp:3399-3402`；插入点 `ptoas.cpp:3137-3143`；Pass 再校验 `pto.target_arch=a5`：`lib/PTO/Transforms/VPTOScheduler/VPTOSchedulerPass.cpp:256-268`；`pto.kernel_kind` 非 Vector 则 `return`：`VPTOSchedulerPass.cpp:269-272`（TableGen 说明 `include/PTO/Transforms/Passes.td:1324-1328`） |
| `--enable-op-fusion=true` | ❌ 硬错误 | ❌ 硬错误 | ⚠️ 有条件：level2/3 才装入融合 Pass；level1 仅告警、路径仍走 | emitc 前端：`FusionPlan`+`OpScheduling`+`MarkLastUse`；vpto 前端：`FusionPlan`+`OpScheduling`+`FusionRegionGen`；vpto lowering 后还有融合收尾 | 非 A5：`ptoas.cpp:3415-3418`；level1 告警：`ptoas.cpp:3419-3422`；装入谓词：`ptoas.cpp:3441-3447`、`3675-3685`；后段：`ptoas.cpp:3171-3200` |
| `--enable-unroll-after-loop-fusion` | ❌ 硬错误（需 a5 + fusion） | ❌ 硬错误 | ⚠️ 有条件：还要求 `--pto-backend=vpto` | 属于融合收尾，不改变主矩阵格子 | `ptoas.cpp:3424-3434` |
| `--enable-insert-sync` / `--enable-inject-barrier-all-sync` / `--enable-graph-sync-solver` | ✅ 可用 | ✅ 可用 | ✅ 可用 | 与 bufid_sync 四选一；`pto.tassign` 与四种模式都不兼容 | 互斥 `ptoas.cpp:3510-3518`；tassign `ptoas.cpp:3504-3533`；装入 `ptoas.cpp:3723-3757` |
| `--emit-vpto` / `--emit-vpto-llvm-ir` / seam IR 两项 | ✅（须 vpto） | ✅（须 vpto） | ✅（须 vpto） | 非 vpto 后端硬错误，与 arch 无关 | `ptoas.cpp:3382-3388`；发射三分支见 5.2.4 |

`--vpto-scheduler` 的两层检查不要混为一谈：driver 入口只拦 **非 A5**（`ptoas.cpp:3399-3402`）；Pass 内部再要求模块（或祖先）带 `pto.target_arch = "a5"`，并对显式 cube `pto.kernel_kind` 静默跳过（`VPTOSchedulerPass.cpp:269-272`）。无 `pto.kernel_kind` 的模块仍会分析，方便 `pto-test-opt` 单测（`Passes.td:1324-1328`）。非 A5 上显式 `analyze|on` 一定报错，不会走到 Pass。

`--enable-vfsim-costmodel-optimization` 没有硬错误：条件不满足时告警并忽略（`ptoas.cpp:3449-3455`），不列入上表的 ❌。

### 6.4 产物形态

产物由 **backend + 发射开关** 决定，不随 arch / level 改 kind。arch 只影响 lowering 变体（A2/A3 的 `PTOArch::A3` / `dav-c220-vec`，A5 走 A5 / 默认 c310），level 只影响共享 `pm` 是否规划内存。下表对应第 5 章已写过的出口，这里只做对照。

| 模式 | 开关 | 结果 kind | 用户可见产物 | 代码 |
| --- | --- | --- | --- | --- |
| emitc 单后端（默认） | `--pto-backend=emitc` | `Text` | **C++ 源码**（含 `#include "pto/pto-inst.hpp"`） | `translateToCpp` 后写入 `result.textOutput`（`tools/ptoas/ptoas.cpp:3868-3889`）；driver `writeTextOutput`。流程见 5.1 |
| `--emit-pto-ir` | 走共享 `pm` 的 emitc / vpto | `Text` | **PTO IR** 文本，尚未进 emitc/vpto 方言 | `ptoas.cpp:3770-3780`（第 4 章 4.3 第 10 步）。vpto 且无待展开 tile 时跳过共享 `pm`（`ptoas.cpp:3609-3619`），该 dump 不会发生 |
| vpto 单后端（默认对象模式） | `--pto-backend=vpto`，且 `--emit-vpto` / `--emit-vpto-llvm-ir` 都关 | `VPTOObject` | **fatobj**（`-o` 必填） | `emitVPTOBackendResult` 对象分支（`ptoas.cpp:3246-3268`）→ `emitFatobjLLVM`（第 5 章 5.2.5） |
| vpto + `--emit-vpto` | 须 vpto | `Text` | **VPTO IR** 文本 | `ptoas.cpp:3224-3231`（5.2.4） |
| vpto + `--emit-vpto-llvm-ir` | 须 vpto | `Text` | **LLVM IR `.ll` 文本** | `ptoas.cpp:3233-3244`（5.2.4） |
| mix-backend | 子模块混用 emitc / vpto | `MixedObject` | **单个 fatobj**；child 的中间 C++ / LLVM 不落用户路径 | `driver.cpp:1320`；`linkFatobjs`（5.3） |
| emitc child（mix 内部） | 子模块 `pto.backend=emitc` | 临时 fatobj | 先产 C++，再 `emitFatobjCCE` | `driver.cpp:915-950`（5.3） |

主矩阵「典型产物」列取上表加粗的默认出口：emitc → C++ 源码，vpto → fatobj。若要 VPTO IR 或 `.ll`，必须显式加 `--emit-vpto` / `--emit-vpto-llvm-ir`，这两个开关在 emitc 上会按 6.3 最后一行报错。

### 6.5 待验证项

下列事项 **不能** 从「CLI 接受 + `compilePTOASModule` 有路径」推出端到端成功，因此不写进 6.2 的 ❌。前三条是入口 / lowering 的已知缺口；上板覆盖留给第 7 章。

> **待确认：** A2/A3 + `--pto-backend=vpto` + **仍含待展开 tile op** 的输入，对象发射是否成功。已知事实：`lowerPTOToVPTOBackend` 在 A2/A3 上跳过 `ExpandTileOp`（`tools/ptoas/ptoas.cpp:3157-3163`），共享管线也不给 A2/A3 贴 `InsertTemplateAttributes`（`ptoas.cpp:3658-3660`）。无待展开 tile 的快路径（`ptoas.cpp:3609-3619`）与手写低层 VPTO/VMI 不依赖这次展开。tile-native 语料在 A2/A3+vpto 上会落到哪一种失败（或意外成功），需要对着具体 `.pto` 跑一遍，不在入口 gate 里预先标死。

> **待确认：** `--pto-arch=a2` 与 `--pto-arch=a3` 在 EmitC 产物上是否有可观察差异。CLI 把二者都当合法 arch（`driver.cpp:112-114`），但 `PTOArch` 只有 `A3`/`A5`（`include/PTO/IR/PTO.h:149-152`），`emitcPM` 对 A2/A3 一律 `createEmitPTOManualPass(PTOArch::A3)`（`ptoas.cpp:3835-3837`）。若存在只在 a2 字符串上分叉的行为，应在 `isA2A3Arch` / `normalizeArch` 之外再搜，当前 `compilePTOASModule` 未再分支。

> **待确认：** `--vpto-scheduler=analyze|on` 配 `--pto-backend=emitc --pto-arch=a5` 是否应当报错。现状是入口只检查 arch（`ptoas.cpp:3399-3402`），Pass 只在 VPTO 的 `prepareVPTOForEmission` 插入（`ptoas.cpp:3137-3143`），因此 emitc 路径会 **静默忽略** 该 flag。这与 `--emit-vpto` 在非 vpto 上硬错误（`ptoas.cpp:3382-3388`）不一致；是否为有意设计，本文不臆造。

上板、pto-isa 头文件探测、npu_validation 是否覆盖全部 18 格，属于第 7 章，不在本表用 ❌ 提前下结论。

### 6.6 IR 层 × 后端

6.2 问的是 arch × backend × level 能不能进管线；本表问的是 **输入 `.pto` 里主要是哪一层 IR**。解析器不按层分派（`parseTextualModule`，`tools/ptoas/driver.cpp:187`）；分叉只看 `--pto-backend`。写法与汇合顺序见 3.2.1，各层能表达什么见 3.2.2 / 6.7，EmitC / VPTO 消费面见 5.1 / 5.2。

| 输入 IR 层 | emitc | vpto（A5） | vpto（A2/A3） |
| --- | --- | --- | --- |
| Tile 级 PTO（`pto.tadd` / `!pto.tile_buf`） | ✅ `PTOToEmitC` → C++（`PTOToEmitC.cpp:6877`） | ✅ `ExpandTileOp` → 物理 VPTO（`ptoas.cpp:3166`，`Passes.td:564-571`） | ⚠️ 跳过 `ExpandTileOp`（`ptoas.cpp:3157-3163`）；tile-native 端到端见 6.5 |
| VMI（`pto.vmi.*` / `!pto.vmi.vreg`） | ❌ 无类型转换、无 pattern | ✅ `appendVMISemanticPipeline` 始终启用（`ptoas.cpp:3291`） | ✅ 无待展开 tile 时走快路径（`ptoas.cpp:3609-3619`），同样跑 VMI 管线 |
| VPTO 微指令（`pto.vadd` / `pto.vlds` / `pto.vecscope`） | ❌ 无 `VaddOp` / `VldsOp` pattern | ✅ 接近发射形态，经 `PTOValidateVPTOEmissionIR` | ✅ 与手写低层对齐 |

混写时 vpto 侧先把 **tile 展开成物理 VPTO 微指令**、再把 **残留的手写 `pto.vmi.*`** 降成物理 VPTO，最后校验（`ptoas.cpp:3280-3292`）。tile **不会**经过 VMI（3.2.1）。emitc 侧没有这套汇合，混进 VMI 或 `pto.vadd` 会在 `EmitPTOManualPass` 失败。kernel 对外签名不要带 `!pto.vmi.*`（`README.md:278-280`）。三层都是同一个 `pto` dialect。

### 6.7 三层能力速查

6.6 问「喂哪条后端」；本表问 **这一层能不能表达这类程序**。叙述与选型见 3.2.2，不在这里复述算子清单。

| 能力 | Tile 级 PTO | VMI | VPTO 微指令 |
| --- | --- | --- | --- |
| Vector eltwise（add / mul / exp …） | ✅ 二维 tile | ✅ 逻辑 `N×T` | ✅ 物理 256B vreg |
| 逻辑宽度跨多个物理寄存器 | ❌ 自己切 tile | ✅ layout 1:N | ❌ 作者手拆 `part` |
| 二维行 / 列归约与广播 | ✅ `trow*` / `tcol*` | ❌ | ❌（须先展开） |
| Cube（matmul / gemv / L0） | ✅ `tmatmul` / `tgemv` | ❌ | ✅ `mad` / `mte_l1_l0*` |
| GM tile DMA（`tload` / `tstore`） | ✅ | ❌（仅 UB `vload`/`vstore`） | ✅ `mte_gm_ub` 等 |
| SIMT | ❌ | ❌ | ✅ ~65 ops |
| 核间通信 / CV pipe | ✅ `comm.*` / `tpush` | ❌ | 部分同步微指令，不是 tile 通信表面 |
| A5 + emitc | ✅ | ❌ | ❌ |
| A5 + vpto | ✅ TileOps 展开 | ✅ 语义管线 | ✅ |
| A2/A3 + emitc | ✅ 主战场 | ❌ | ❌ |
| A2/A3 + vpto | ⚠️ 仅部分 eltwise → `pto.ub.*`（6.5） | ✅ 快路径 | ✅ `pto.ub.*` / 手写低层 |
| 自动降到 VMI | ❌ 无 `PTOToVMI`；A5 直接到 `pto.vadd` | — | — |
| 同属 `pto` dialect | ✅ | ✅ | ✅ |

同一件向量加法三层都能写，粒度不同：tile 是两个 `tile_buf`，VMI 是 `!pto.vmi.vreg<128xf32>`，VPTO 是带 mask 的 `!pto.vreg<64xf32>`。选层口诀见 3.2.2 末。三层都是 PTO IR；`--pto-backend=vpto` 是后端名，不是第四种 IR。

## 7. 运行时与上板（pto-isa 调用、npu_validation、fatobj 链接）

第 5 章把两条后端写到**编译器出口**：emitc 交出带 `#include "pto/pto-inst.hpp"` 的 C++ 源码，vpto（及 mix）交出 fatobj。本章从这份产物接着往下走——头文件从哪来、怎么编成可执行文件、怎么在模拟器或 NPU 上对结果。编译器内部的 lowering 与 fatobj 五步发射不在这里复述，见第 5 章 5.1 / 5.2.5 / 5.3。

两条后端的运行时故事不同，不能混着记：

| 后端 | 编译器出口（第 5 章） | 本章消费方式 |
| --- | --- | --- |
| **emitc** | C++ 源码 | 依赖外部 **pto-isa** 头文件；`test/npu_validation` 从 `.cpp` 生成验证目录，BiSheng 编设备代码后上板 |
| **vpto** | fatobj | 设备代码已在 fatobj 里；`test/vpto` 把 fatobj 与 `launch.cpp` 做 `--cce-fatobj-link`，再编 host 可执行文件。SIM / NPU 双模式 |
| **mix**（组合，非第三后端） | 单个 fatobj（child 各自编完再链） | 与 vpto 同一类「拿 fatobj 上板」；ABI 符号仍按 vector/cube 分 |

VMI 仍只是 VPTO 路径内部的 IR 层，上板阶段看不到它。无卡、只想确认 `.cpp` 能过 BiSheng 的，走 `docs/no_npu_compile_only_guide_zh.md`（`README.md:339` 也指向它），不是本章的 NPU 执行路径。

### 7.1 pto-isa 外部依赖

**pto-isa 不在本仓库。** `.gitmodules` 只登记了 `3rdparty/PTO-Gym` 与 `3rdparty/VfSimulator`，没有 pto-isa。EmitC 生成的 C++ 通过 `pto/pto-inst.hpp` 调用设备内建（插入点见 5.1，`lib/PTO/Transforms/PTOToEmitC.cpp:13769-13772`）；仓库里现成的产物形态见 `test/samples/MatMul/tmatmulk.cpp:9-11`。要把这份 C++ 真正编成设备对象，编译器必须能找到那套头文件。

路径探测发生在装配 CANN 工具链时：`CANNToolchain::create`（`tools/ptoas/ObjectEmission.cpp:866`）调用 `discoverCppIncludeDirs`（`ObjectEmission.cpp:892-895`），结果写入 `toolchain.cppIncludeDirs`（字段 `tools/ptoas/ObjectEmission.h:57`）。`discoverCppIncludeDirs`（`ObjectEmission.cpp:256-282`）的顺序是：

1. 环境变量 **`PTO_ISA_PATH`**，未设再试 **`PTO_ISA_ROOT`**（`ObjectEmission.cpp:261-264`，经 `getEnvPath`，`ObjectEmission.cpp:156`）。
2. `addPTOISAIncludeDirs`（`ObjectEmission.cpp:240-254`）对该根下的 `include/` 和仓库根本身各探测一次；`hasPTOISAHeader`（`ObjectEmission.cpp:225-227`）检查的是「该目录下是否存在 `pto/pto-inst.hpp`」，通过才把目录加进 `-I`。顺带加入 `<根>/tests/common`（若存在）。
3. 再加 `$ASCEND_HOME_PATH/include` 与 `$ASCEND_DRIVER_PATH/kernel/inc`（driver 默认 `/usr/local/Ascend/driver`，`ObjectEmission.cpp:268-271`）。

两个告警（`ObjectEmission.cpp:273-280`）把失败模式钉死：

- 两个环境变量都空：`Warning: PTO_ISA_PATH/PTO_ISA_ROOT is not set; C++ device object emission may fail to include pto/pto-inst.hpp.`
- 设了根但目录里找不到头文件：`Warning: no PTO-ISA include directory containing pto/pto-inst.hpp was found under <path>.`

这些 `-I` 只喂给 **把 C++ 编成设备对象** 的路径：`compileCppDeviceSourceToObject` / `compileCppDeviceSourceToFatobj` 遍历 `toolchain.cppIncludeDirs` 拼 `-I`（`ObjectEmission.cpp:611-613`、`652-654`）。这正是 mix 里 emitc child 的 `emitFatobjCCE`（5.3），以及任何需要现场编译 EmitC 源码的步骤。vpto 单后端默认对象模式走的是 LLVM `.ll` → BiSheng，**不**经过这套 C++ include；因此「没设 `PTO_ISA_PATH`」首先打中的是 emitc 产物的设备编译，不是 vpto fatobj 发射。

上板脚本是第二处消费者。`test/npu_validation` 生成的 `CMakeLists.txt` 要求 `-DPTO_ISA_ROOT=` 或在邻近目录搜到 `pto-isa` 仓库（`test/npu_validation/scripts/generate_testcase.py:3240-3256`），并把 `${PTO_ISA_ROOT}/include` 与 `tests/common` 加进 include（`generate_testcase.py:3295-3298`）。生成器自己的兼容层也 `#include <pto/pto-inst.hpp>`（`generate_testcase.py:52`）。没有检出 pto-isa、没有把根路径告诉编译器或 CMake，EmitC 产物过不了设备编译。

### 7.2 npu_validation（EmitC 产物上板）

**输入形态。** ptoas `--pto-backend=emitc` 写出的 `.cpp`，例如 `test/samples/MatMul/tmatmulk.cpp`。生成器吃的是这份 C++，不是 `.pto`。

**处理。** 入口 `test/npu_validation/scripts/generate_testcase.py`。`README.md:346-361` 给出从仓库根出发的用法：

```bash
# A2/A3
python3 test/npu_validation/scripts/generate_testcase.py \
  --input test/samples/MatMul/tmatmulk.cpp \
  --run-mode npu \
  --soc-version Ascend910B1

# A5
python3 test/npu_validation/scripts/generate_testcase.py \
  --input test/samples/MatMul/tmatmulk.cpp \
  --run-mode npu \
  --soc-version Ascend950

test/samples/MatMul/npu_validation/tmatmulk/run.sh
```

`--run-mode` 只接受 `sim|npu`（默认 `npu`），`--soc-version` 默认 `Ascend910`（`generate_testcase.py:3505-3506`）。未传 `--output-root` 时，输出落在 sample 旁的 `npu_validation/<testcase>/`（`generate_testcase.py:2474-2478`）。

**输出形态：验证套件。** `README.md:364` 列出目录里会生成：

`{name}_kernel.cpp` / `main.cpp` / `golden.py` / `compare.py` / `run.sh` / `CMakeLists.txt`

代码还额外写出 **`launch.cpp`**（`generate_testcase.py:3192`）——host 侧 `kernel<<<block, nullptr, stream>>>` 包装，CMake 把它与 kernel 编进同一个共享库（`generate_testcase.py:3303`）。README 那一行没点名 `launch.cpp`，以生成器为准。另有 `outputs.txt`、`validation_meta.env` 给 runner 用，不是用户手改的入口。

五件源文件的分工：

| 文件 | 谁写 | 职责 |
| --- | --- | --- |
| `{name}_kernel.cpp` | `generate_testcase.py:3158` | 经兼容层改写后的设备 kernel（含 pto-isa include） |
| `launch.cpp` | `generate_testcase.py:3192` | `extern "C"` 启动函数，设备侧 `<<<...>>>` |
| `main.cpp` | `generate_testcase.py:2934` | ACL 分配 / 拷入 / 调 launch / 拷回 |
| `golden.py` | `generate_testcase.py:3111` | 默认随机输入、输出全零占位（`README.md:365`） |
| `compare.py` | `generate_testcase.py:3468` | 对比 `golden*.bin` 与 `output*.bin`（`README.md:366`） |

`run.sh`（`generate_testcase.py:3495`）按 `--run-mode` / `--soc-version` 填模板后直接跑，无需再传参（`README.md:359-360`）。A5 类 SoC（名字含 `950` 或 `a5`）用 `REGISTER_BASE`，其余用 `MEMORY_BASE`（`generate_testcase.py:3198-3201`）。

这条路径验证的是 **emitc 出口能否在 NPU 上跑通**，不经过 vpto fatobj。

### 7.3 test/vpto（VPTO 产物，SIM / NPU）

`test/vpto/` 是 VPTO 后端的端到端用例集，不是 npu_validation 的另一份生成器。目录布局：

```text
test/vpto/
  cases/          用例根（脚本默认 CASES_ROOT）
    kernels/
    micro-op/
    vmi_new/      手写 VMI / VPTO kernel（与 5.2.1 语料对应）
    onboard-only/ 仅 NPU，SIM 下跳过
    elementwise-1d-2d-equivalence.py
  npu_validation/common/   host 侧公共头
  scripts/run_host_vpto_validation.sh
```

**输入形态（五件固定文件）。** 非 PTODSL 用例目录必须同时提供（`run_host_vpto_validation.sh:147-150`）：

`kernel.pto` + `launch.cpp` + `main.cpp` + `golden.py` + `compare.py`

缺任何一件，`validate_case_path` 直接失败。PTODSL 用例外：目录里有 `kernel.py`，或单个 `*.py` 用例文件（排除 `golden.py` / `compare.py` / `kernel.py` / `_` 前缀，`run_host_vpto_validation.sh:106-119`）。无 `ptoas.flags` 的例子：`test/vpto/cases/micro-op/unary-vector/vrelu/`（正好五件，无 flags）。

**`ptoas.flags` 约定。** 每个用例目录可放一行编译参数。脚本逻辑（`run_host_vpto_validation.sh:359-363`）是**整行替换**，不是与环境变量合并：

- 有 `ptoas.flags` → `read -a ptoas_args < ptoas.flags`，只用这一行；
- 没有 → 用环境变量 `PTOAS_FLAGS`，默认 `--pto-arch a5 --pto-backend=vpto`（`run_host_vpto_validation.sh:20-21`）。

典型单后端：`test/vpto/cases/vmi_new/kernels/dynamic-quant-perchannel-f16-8x256/ptoas.flags:1` 为 `--pto-arch a5 --pto-backend=vpto`。需要额外 Pass 的会把 flag 写进同一行，例如 `test/vpto/cases/micro-op/vector-load-store/soft-post-update-step4-combined/ptoas.flags:1` 带 `--enable-vpto-soft-postupdate`。

mix 用例会**故意不写** `--pto-backend`，让 driver 按子模块 `pto.backend` 属性走进 mix（见 2.4 / 5.3）。`test/vpto/cases/micro-op/backend/mixed-external-vadd/ptoas.flags:1` 只有 `--pto-arch a5`；对应 `kernel.pto` 里一个 child `pto.backend = "emitc"`（`test/vpto/cases/micro-op/backend/mixed-external-vadd/kernel.pto:10`）、另一个 `pto.backend = "vpto"`（`test/vpto/cases/micro-op/backend/mixed-external-vadd/kernel.pto:27-29`）。若在 flags 里写死 `--pto-backend=vpto`，会盖掉 mix 判定。

**处理：`run_host_vpto_validation.sh`。** 必填 `WORK_SPACE`、`ASCEND_HOME_PATH`；`PTOAS_BIN` 默认 `install/bin/ptoas`（`run_host_vpto_validation.sh:20`、`56-58`）。`DEVICE` 默认 `SIM`（`run_host_vpto_validation.sh:25`）。非 PTODSL 用例四步（`run_host_vpto_validation.sh:365-382`）：

1. `ptoas ${ptoas_args} kernel.pto -o kernel.fatobj.o` ——vpto 对象模式，产物就是第 5 章 5.2.5 的 fatobj（mix 则是 5.3 链好的那一枚）；
2. BiSheng 编 `launch.cpp` → `launch.o`（`--cce-aicore-arch=dav-c310`，`run_host_vpto_validation.sh:229-246`）；
3. `bisheng --cce-fatobj-link -shared` 把 fatobj 与 launch.o 链成 `lib<case>_kernel.so`（`run_host_vpto_validation.sh:266-275`）；
4. 编 `main.cpp` 成 host 可执行文件，跑 `golden.py`，执行 kernel，再 `compare.py`。

**SIM / NPU。** 分叉在链接库，不在 ptoas：

| `DEVICE` | 链接 | 模拟器库 |
| --- | --- | --- |
| `SIM`（默认） | `-lruntime_camodel`（`run_host_vpto_validation.sh:257-261`、`284-288`） | 未设 `SIM_LIB_DIR` 时在 `$ASCEND_HOME_PATH` 下搜 `simulator/dav_3510/lib`（`run_host_vpto_validation.sh:78-95`） |
| `NPU` | `-lruntime`（`run_host_vpto_validation.sh:262-263`、`289-290`） | 不用 camodel |

`bisheng` 默认 `$ASCEND_HOME_PATH/bin/bisheng`（`run_host_vpto_validation.sh:98`）。`onboard-only/` 前缀的用例在 `DEVICE=SIM` 且非 `COMPILE_ONLY=1` 时被跳过或直接 `die`（`run_host_vpto_validation.sh:158-161`、`182-185`）。PTODSL 用例不走上述四步：SIM 调 `scripts/sim_dsl.sh`，NPU 直接 `python3` 跑脚本（`run_host_vpto_validation.sh:322-329`）。

### 7.4 fatobj 上板与 direct-call ABI

7.3 的第 3 步已经是「fatobj + launch 再链一次」。kernel-side C++ wrapper 要调的是**已经编好的 vector/cube callee**，链路写在 `docs/kernel_side_wrapper_fatobj_link_guide_zh.md`（目的见该文件 `docs/kernel_side_wrapper_fatobj_link_guide_zh.md:1-5`）。ptoas 如何把 VPTO 收成一枚 fatobj 见 5.2.5，这里只写**出编译器之后**的三 TU → bundle → host 链接，不重复那五步。

**三个 TU。** 手写实验里是 `vec_callee.cpp`、`cube_callee.cpp`、`caller.cpp`（另加 host `main.cpp`）。BiSheng 分别按 arch 编成可参与分离编译的 CCE object（`docs/kernel_side_wrapper_fatobj_link_guide_zh.md:227-248`）：

```text
vec_callee.cpp   bisheng -dc -xcce --cce-aicore-arch=dav-c310-vec
                 --> vec_callee.o          提供 vec_callee.vector

cube_callee.cpp  bisheng -dc -xcce --cce-aicore-arch=dav-c310-cube
                 --> cube_callee.o         提供 cube_callee.cube

caller.cpp       bisheng -dc -xcce --cce-aicore-arch=dav-c310
                 --> caller.o              mixed：AIV 引 .vector，AIC 引 .cube
```

`-dc` 生成可参与 device separate compilation 的 object；caller 与 callee 都要用，否则 device-side direct-call 链接不可靠（`docs/kernel_side_wrapper_fatobj_link_guide_zh.md:291-293`）。

**fatobj-link → `bundle.o`。**（`docs/kernel_side_wrapper_fatobj_link_guide_zh.md:250-255`）

```bash
bisheng -fPIC --cce-fatobj-link -r \
  -o bundle.o caller.o vec_callee.o cube_callee.o
```

`--cce-fatobj-link -r` 对多枚 CCE device object 做 relocatable fatobj link，得到 host 链接器能消费的 `bundle.o`（`docs/kernel_side_wrapper_fatobj_link_guide_zh.md:295-297`）。这与 mix 的 `linkFatobjs`（5.3）是同一类 BiSheng 开关，场景不同：这里链的是 wrapper + 已编好的 callee，不是 ptoas 内部多 child。

**host `g++`。** 先把 `main.cpp` 编成 `main.o`（`docs/kernel_side_wrapper_fatobj_link_guide_zh.md:257-265`），再与 `bundle.o` 链可执行文件（`docs/kernel_side_wrapper_fatobj_link_guide_zh.md:267-275`）：

```bash
g++ main.o bundle.o -o rdc_cv_sectioned_real_demo \
  -L ${ASCEND_HOME_PATH}/lib64 \
  -Wl,-rpath,${ASCEND_HOME_PATH}/lib64 \
  -lprofapi -lruntime -lascendcl -ltiling_api -lplatform -lc_sec -lnnopbase \
  -lstdc++ -ldl -lpthread -lm
```

整条链压缩成一张图：

```text
  vec_callee.o   cube_callee.o   caller.o
           \          |          /
            --cce-fatobj-link -r
                      |
                  bundle.o
                      |
         main.o ------+---- g++ -lruntime -lascendcl --> 可执行文件
```

**Direct-call ABI。** sectioned caller 在 `ASCEND_IS_AIV` 分支调 vector callee、在 `ASCEND_IS_AIC` 分支调 cube callee（`docs/kernel_side_wrapper_fatobj_link_guide_zh.md:27-35`）。device linker 要的符号是 `vec_callee.vector` / `cube_callee.cube`（`docs/kernel_side_wrapper_fatobj_link_guide_zh.md:38-50`）。落到 PTOAS-vpto 上就是：

```text
vector direct-call callee: foo.vector
cube   direct-call callee: foo.cube
```

只导出 `foo`、`foo_mix_aiv`、`foo_mix_aic` 或 `foo.vector.thread` 都不满足这条 ABI（`docs/kernel_side_wrapper_fatobj_link_guide_zh.md:52-59`）。这与 `vptoPublicABISuffix`（`tools/ptoas/ObjectEmission.cpp:924-934`）对齐：CANN ≥ 9.0.0-beta.2 用 `.vector` / `.cube`，否则 `_mix_aiv` / `_mix_aic`。`applyVPTOLLVMABINames`（`ObjectEmission.cpp:1056-1073`）给外部 linkage 的定义函数追加该后缀。新 ABI 下 ptoas 打出的 fatobj 才能当 7.4 的 callee 被 kernel-side wrapper direct-call；旧后缀对应旧 CANN，对不上 `.vector` / `.cube` 的 caller。

7.3 的 `launch.cpp` 走的是 host 侧 `<<<...>>>` 启动，不是本节的 device-side `foo.vector` direct-call。两种链接都消费 vpto fatobj，符号契约不同。

### 7.5 小结

- **emitc 上板**要两样外部分：pto-isa 头（`PTO_ISA_PATH` / `PTO_ISA_ROOT`，`ObjectEmission.cpp:261-264`）和 CANN/BiSheng。缺头文件时设备编译失败；用 `test/npu_validation/scripts/generate_testcase.py` 从 `.cpp` 生成套件，`run.sh` 上板。
- **vpto 上板**拿的是 5.2.5 已经编好的 fatobj。用例目录五件（`kernel.pto` + `launch.cpp` + `main.cpp` + `golden.py` + `compare.py`）；`ptoas.flags` 整行替换默认 `--pto-arch a5 --pto-backend=vpto`。`DEVICE=SIM|NPU` 只换 runtime 库。mix 用例不要在 flags 里写死 `--pto-backend`。
- **kernel-side wrapper** 是第三种消费：三个 TU → `--cce-fatobj-link -r` → `bundle.o` → host `g++`。direct-call 要求 `foo.vector` / `foo.cube`（新 ABI），细节见 `docs/kernel_side_wrapper_fatobj_link_guide_zh.md:227-275`，发射侧见 5.2.5 的 `vptoPublicABISuffix`。
- 无卡 compile-only 不走本章执行路径，见 `docs/no_npu_compile_only_guide_zh.md`。发布形态（wheel / tarball 里带不带这套运行时）见第 8 章。

## 8. 发布形态（ptoas wheel / ptoas-vmi wheel / tarball）

编译器 CLI 只有两条后端（`--pto-backend` 取 `emitc|vpto`，见第 2 章），但**发布物**有三条线：主线 Python wheel `ptoas`、VMI Python wheel `ptoas-vmi`、compiler-only 二进制 tarball `ptoas-bin-*.tar.gz`。`ptoas-vmi` 不是第三条 CLI 后端，而是同一套源码、同一套 `ptoas` 命令的独立发行包（PyPI/GitHub Release 上的项目名不同）。VMI 作为 IR 层的位置仍在第 5 章 5.2；本章只讲它怎么被打成 wheel。

Linux 轮子与 tarball 由 `.github/workflows/build_wheel.yml` 产出；macOS 有平行 workflow `.github/workflows/build_wheel_mac.yml`，分派规则同构，下文以 Linux 这份为准。

### 8.1 两条 release 线

约定写在 `README.md:183-184`：tag `ptoas-vX.Y` 发主工具链，tag `vmi-vA.B.C` 发 `ptoas-vmi` distribution。创建 VMI tag 之前，要先把 `packaging/ptoas-vmi/pyproject.toml.patch` 里的静态版本改成同一个 `A.B.C`（`README.md:184-185`）。

分派发生在 workflow 的 `Resolve PTOAS distribution version` 步（`.github/workflows/build_wheel.yml:99-148`）。`release` 事件按 tag 前缀 `case`：

| Tag 模式 | `PTOAS_PYTHON_PACKAGE_NAME` | `PTOAS_CLI_VERSION` | 期望版本从哪读 | 证据 |
| --- | --- | --- | --- | --- |
| `vmi-v*` | `ptoas-vmi` | `vmi ${PTOAS_VERSION}`（tag 去掉 `vmi-v` 前缀） | `packaging/ptoas-vmi/prepare_source.py --print-version` | `.github/workflows/build_wheel.yml:103-108` |
| `ptoas-v*` | `ptoas` | `${PTOAS_VERSION}`（tag 去掉 `ptoas-v` 前缀） | `.github/scripts/compute_ptoas_version.py --mode release` | `.github/workflows/build_wheel.yml:109-114` |
| `v*` | `ptoas` | `${PTOAS_VERSION}`（tag 去掉 `v` 前缀） | 同上 | `.github/workflows/build_wheel.yml:115-120` |
| 其它 | （失败） | — | — | `.github/workflows/build_wheel.yml:121-124`，要求 tag 以 `v` / `ptoas-v` / `vmi-v` 开头 |

`v*` 是主线的兼容前缀，与 `ptoas-v*` 走同一条包；`vmi-v*` 必须写在 `case` 最前，否则会被 `v*` 吃掉。tag 剥出来的 `PTOAS_VERSION` 必须等于「期望版本」，否则整步失败（`.github/workflows/build_wheel.yml:126-129`）。

非 `release` 事件：

- `workflow_dispatch` 且 `inputs.release_kind=vmi` → 与 `vmi-v*` 相同，包名 `ptoas-vmi`（`.github/workflows/build_wheel.yml:130-133`）。这类手动跑是 dry-run，**不会**发布 nightly（`.github/workflows/build_wheel.yml:38-39`）。
- 其它 `workflow_dispatch` → 主线 `ptoas`（`.github/workflows/build_wheel.yml:134-137`）。
- `push` / `pull_request` / `schedule` → 主线 `ptoas`，版本走 `compute_ptoas_version.py --mode dev`（`.github/workflows/build_wheel.yml:138-141`）。因此 **nightly 只打 `ptoas` wheel，不打 `ptoas-vmi`**。

job 的 `if` 用 `startsWith(github.ref_name, 'v')` 放行 release tag（`.github/workflows/build_wheel.yml:50`）。`vmi-v*` 也以 `v` 开头，所以会进同一个 `build_wheel` job，再由上面的 `case` 改包名。

wheel 文件名遵循 PEP 427：项目名 `ptoas-vmi` 会归一成 `ptoas_vmi-*.whl`（`README_en.md:168`）。

### 8.2 ptoas-vmi staging

主线直接从仓库根 `pyproject.toml` 构建。VMI 线**不改工作区**里那份 `pyproject.toml`：先把当前 Git revision `git archive` 到 staging 目录，只在副本上打 metadata patch，再从副本 `pip wheel`（`README.md:186-188`）。普通门禁和 release **都不**生成、发布 sdist。

脚本 `packaging/ptoas-vmi/prepare_source.py`（模块文档：`packaging/ptoas-vmi/prepare_source.py:10`）做这件事：

1. 默认输出目录 `.work/ptoas-vmi-source`（`packaging/ptoas-vmi/prepare_source.py:28`）。仓库内路径必须落在 `.work/` 下（`packaging/ptoas-vmi/prepare_source.py:79-82`）。
2. `git archive` 指定 revision（默认 `HEAD`）到 tar，排除 `.agents` / `.claude` / `.codex`（`packaging/ptoas-vmi/prepare_source.py:33-37`、`139-152`），解到临时目录。
3. 在临时目录里 `git apply packaging/ptoas-vmi/pyproject.toml.patch`（`packaging/ptoas-vmi/prepare_source.py:173-178`），然后校验 staged `pyproject.toml` 含 `name = "ptoas-vmi"`、静态 `version`、`PTOAS_CLI_VERSION_LABEL = "vmi"`、`sdist.inclusion-mode = "manual"`（`packaging/ptoas-vmi/prepare_source.py:182-190`）。
4. 用临时目录替换 staging 目录。工作区根的 `pyproject.toml` 全程只读。

`--print-version` 不准备树，只从 patch 里用正则读**唯一**一行 `+version = "X.Y.Z"`（`packaging/ptoas-vmi/prepare_source.py:29`、`61-65`）。不是一条就报错。

patch 相对根 `pyproject.toml` 改了这些（当前 HEAD 的 `packaging/ptoas-vmi/pyproject.toml.patch`）：

| 改动 | patch 行 | 作用 |
| --- | --- | --- |
| `name = "ptoas"` → `"ptoas-vmi"` | `packaging/ptoas-vmi/pyproject.toml.patch:9` | 发行包名 |
| 去掉 `dynamic = ["version"]`，写入静态 `version = "0.1.6"` | `packaging/ptoas-vmi/pyproject.toml.patch:10` | VMI 版本与 CMake 主线脱钩；`0.1.6` 是撰写时的 live 值，发版前随 tag 更新 |
| description 追加 `with VMI support` | `packaging/ptoas-vmi/pyproject.toml.patch:11` | 包描述 |
| `sdist.inclusion-mode = "manual"` | `packaging/ptoas-vmi/pyproject.toml.patch:19` | 配合「不发 sdist」 |
| `PTOAS_CLI_VERSION_LABEL = "vmi"` | `packaging/ptoas-vmi/pyproject.toml.patch:24` | CMake 给 CLI 版本串加 `vmi` 前缀（见 8.6） |
| 删除 `[tool.scikit-build.metadata.version]` 的 regex provider | `packaging/ptoas-vmi/pyproject.toml.patch:26-30` | 不再从 `CMakeLists.txt` 抽版本 |

构建步：若包名已是 `ptoas-vmi`，先跑 `prepare_source.py --output-dir ${PTO_SOURCE_DIR}/.work/ptoas-vmi-source`，再 `python -m pip wheel` 那个 staging 目录；否则 wheel 源就是仓库根（`.github/workflows/build_wheel.yml:244-258`）。

staging **只改发行元数据**，不改编译器源码。VMI→VPTO 语义管线在主线 `ptoas` wheel 里同样始终启用（见第 5 章 5.2）；`ptoas-vmi` 的意义是独立版本号与 GitHub Release 通道，不是多出来的 `--pto-backend`。

### 8.3 互斥安装

两个 wheel **不能**装进同一个 Python 环境（`README.md:177-179`）。原因不是 CLI 后端冲突，而是安装落点相同：

- 顶层 Python 包都叫 `ptoas`。根 `pyproject.toml` 把 `ptodsl/ptoas` 映射成 wheel 里的 `ptoas` 包（`pyproject.toml:48-50`）；VMI patch 不改 `[tool.scikit-build.wheel.packages]`，所以 `ptoas-vmi` 仍然安装同名顶层包。
- console script 都叫 `ptoas`，入口 `ptoas._cli:main`（`pyproject.toml:37-38`）。patch 同样不改 `[project.scripts]`。VMI 线只是让 `ptoas --version` 打出 `ptoas vmi A.B.C`（`README.md:175-176`），命令名不变。

混装会互相覆盖文件；卸其中一个也可能把另一个撕坏。主线 wheel 会同时带上 PTODSL（`README.md:174`，`import ptodsl` 应直接可用）。需要 VMI 发行包时，换一个干净环境装 `ptoas-vmi`，不要 `pip install` 叠上去。

### 8.4 nightly

定时任务（UTC `cron: "10 17 * * *"`，`.github/workflows/build_wheel.yml:16-18`）把最新 **主线** wheel 发到 GitHub Release tag `nightly`（`.github/workflows/build_wheel.yml:370-373`：`RELEASE_TAG=nightly`，prerelease，不当 latest）。同一次 schedule 还上传 `ptoas-bin-*.tar.gz` 和 `nightly-manifest-linux.json`（`.github/workflows/build_wheel.yml:412-475`）。Linux / macOS 两份 nightly 错开 30 分钟，共用非取消的 concurrency group，避免资产互相踩（`.github/workflows/build_wheel.yml:35-40`）。

开发者安装（`README.md:216-238`）：

```bash
python tools/install_nightly_wheel.py
```

脚本 `tools/install_nightly_wheel.py` 默认仓库 `hw-native-sys/PTOAS`、tag `nightly`、发行包 `ptoas`（`tools/install_nightly_wheel.py:27-28`、`62-64`）。它用当前解释器的 pip 自带 `packaging`（或 `pip._vendor.packaging`）按 PEP 425 tag 选 wheel，不必预先 `pip install packaging`（`tools/install_nightly_wheel.py:134-148`，`README.md:226-227`）。行为要点：

- `--dry-run` 只打印选中的 URL，不安装（`tools/install_nightly_wheel.py:67-69`、`249-251`）。
- 安装命令是 `python -m pip install --force-reinstall --no-deps`（`tools/install_nightly_wheel.py:257-266`）：替换环境里已有的同名 nightly，**不**重装运行时依赖（`README.md:232-234`）。
- GitHub Release 提供 asset digest 时校验 SHA-256；也可用 `--sha256` 显式指定（`tools/install_nightly_wheel.py:71-74`、`255-256`）。
- 选中资产超过 48 小时未更新会告警（`STALE_WHEEL_AGE`，`tools/install_nightly_wheel.py:30`、`242-247`）。

`--package` 可以改成 `ptoas-vmi`，但 nightly release 按 8.1 只发布 `ptoas`，那时会找不到兼容 wheel。

### 8.5 tarball vs wheel

同一份 `build_wheel` job 在 Python 3.11 那条 matrix 腿上额外打 compiler-only 归档（`.github/workflows/build_wheel.yml:288-304`）：

1. `cmake --install` 两个 component：`PTOAS_Python` 与 `PTOAS_CompilerArchive`（`.github/workflows/build_wheel.yml:292-297`）。
2. 打成 `ptoas-bin-${arch}.tar.gz`（`.github/workflows/build_wheel.yml:303-304`），`arch` 为 `x86_64` 或 `aarch64`。macOS 对应 `ptoas-bin-macos-*.tar.gz`。

归档里**有** `bin/ptoas`、`ptoas/_cli.py`、`ptodsl/__init__.py` 等文件（smoke test 逐项检查，`.github/workflows/build_wheel.yml:316-325`），解压后可以直接跑 CLI。但这**不是**一份 PTODSL-capable 的 Python distribution：

> `ptoas-bin-*.tar.gz` 这类 compiler-only 二进制 tarball 只提供 CLI/toolchain，**不是** PTODSL-capable Python distribution；仅解压 tarball 不能保证 `import ptodsl` 可用。（`README.md:180-182`）

`ptodsl/README.md:73-74` 把边界写得更短：`ptoas` wheel 才是 PTODSL-capable；tarball 不蕴含 `import ptodsl`。`test/dsl-st/README.md:39-40` 也说 tarball 不能替代 Python 安装——`import ptodsl` 失败时应修安装环境，而不是给测试脚本补 `sys.path`。

三种产物对比：

| 产物 | 怎么来 | 装什么 | `ptoas` CLI | `import ptodsl` | 适用 |
| --- | --- | --- | --- | --- | --- |
| `ptoas` wheel | tag `ptoas-v*` / `v*`，或 nightly | pip 发行包 `ptoas` + 顶层包 `ptoas`/`ptodsl` | 有 | **有**（`README.md:174`） | 主线开发、PTODSL、日常 nightly |
| `ptoas-vmi` wheel | tag `vmi-v*` | pip 发行包 `ptoas-vmi`，仍安装顶层 `ptoas` | 有；`--version` 为 `ptoas vmi A.B.C` | 随 wheel 带 PTODSL 源码布局，但与主线互斥，不要混装 | VMI 发行线 |
| `ptoas-bin-*.tar.gz` | 与 wheel 同一次构建的 3.11 腿 | 解压即用的 prefix（`bin/` + 运行时树） | 有 | **不保证**（`README.md:180-182`） | 只要编译器二进制 / 上板环境，不要把它当 `pip install` 替代品 |

### 8.6 版本号

**主线 Python / CLI 版本的单一来源是顶层 CMake `project()` 行。** 撰写时 live 值为：

```cmake
project(ptoas VERSION 0.61)
```

（`CMakeLists.txt:47`。文档开头注明行号基于 2026-08-26 HEAD；发版后这个数字会变，以该行为准，不要沿用过期的 `0.61`。）

`pyproject.toml` 把 PEP 621 版本声明成 `dynamic`（`pyproject.toml:19-20`），由 scikit-build-core 用同一条 regex 从 `CMakeLists.txt` 抽取（`pyproject.toml:57-60`）：

```text
project\s*\(\s*ptoas\s+VERSION\s+(?P<value>[0-9]+\.[0-9]+)\s*\)
```

`.github/scripts/compute_ptoas_version.py:38-45` 用等价正则读「期望 release 版本」，给 8.1 的 tag 校验。`--mode` 虽有 `dev`/`release` 两个选项（`.github/scripts/compute_ptoas_version.py:26-29`），`main()` **并不按 mode 分支**，两种调用都打印同一个 CMake base version；workflow 真正的 tag 对齐是比较剥出的 `PTOAS_VERSION` 与这段输出（`.github/workflows/build_wheel.yml:126-129`）。脚本另有可选 `--check-tag`（`.github/scripts/compute_ptoas_version.py:32-34`），这条 workflow 路径没有用它。

CMake 组装 CLI 字符串的顺序（`CMakeLists.txt:95-108`）：

1. 默认 `PTOAS_CLI_VERSION = ${PROJECT_VERSION}`（即 `0.61` 这一档）。
2. 若当前 checkout 恰好打在匹配 `v[0-9]*.[0-9]*` 的 annotated tag 上，改用该 tag 剥出的版本（`CMakeLists.txt:77-93`、`96-98`）。只认 `vX.Y`，不认 `ptoas-v*` / `vmi-v*`。
3. 若 scikit-build 提供了 `SKBUILD_PROJECT_VERSION_FULL`，改用它（wheel 元数据说了算，`CMakeLists.txt:99-101`）。
4. 若设置了 `PTOAS_CLI_VERSION_LABEL`，前缀成 `"${label} ${version}"`。VMI staging 正是在这里打上 `vmi`（patch 第 24 行；cache 变量声明在 `CMakeLists.txt:60-61`）。
5. `PTOAS_RELEASE_VERSION_OVERRIDE` 若非空则整串覆盖（`CMakeLists.txt:55-56`、`106-108`）。

编译定义把最终字符串打进二进制：`PTOAS_RELEASE_VERSION="${PTOAS_CLI_VERSION}"`（`tools/ptoas/CMakeLists.txt:67-68`）。`ptoas --version` 打印 `ptoas ` + 该宏（`tools/ptoas/driver.cpp:65-67`）。因此主线看到 `ptoas 0.61`，VMI 线看到 `ptoas vmi 0.1.6`（数字随 live patch / CMake 变）。

**VMI 版本不读 CMake。** 它是 patch 里的静态 `version = "X.Y.Z"`（当前 `0.1.6`，`packaging/ptoas-vmi/pyproject.toml.patch:10`），并删掉了上面那条 CMake regex provider。两条线的数字没有「必须相等」的代码约束；发 VMI 时同步的是 tag 与 patch，不是 `CMakeLists.txt:47`。

发版后的自动 bump（只作定位，细节不必跟进 CI）：`ptoas-v*` / 非 VMI 的 `v*` 会改 `CMakeLists.txt`（`.github/workflows/build_wheel.yml:477-510`）；`vmi-v*` 只改 `packaging/ptoas-vmi/pyproject.toml.patch`（`.github/workflows/build_wheel.yml:512-545`）。

### 8.7 小结

- **两条 release 线**：`ptoas-vX.Y`（及兼容 `vX.Y`）→ 包 `ptoas`；`vmi-vA.B.C` → 包 `ptoas-vmi`。分派在 `.github/workflows/build_wheel.yml:99-148`。
- **VMI 构建走 staging**：`prepare_source.py` + `pyproject.toml.patch`，不改工作区 `pyproject.toml`，不发 sdist。
- **互斥**：两个 wheel 抢同一个顶层 `ptoas` 包和 `ptoas` console script（`README.md:177-179`）。
- **nightly**：定时发主线 `ptoas` 到 tag `nightly`；`python tools/install_nightly_wheel.py` 按当前 CPython/平台选轮子（`README.md:216-238`）。
- **tarball 不是 PTODSL 发行包**：`ptoas-bin-*.tar.gz` 能跑 CLI，不能替代 `pip install ptoas`（`README.md:180-182`）。
- **版本**：主线看 `CMakeLists.txt:47`；VMI 看 patch 里的静态 `version`。


## 9. 新开发者上手建议

本章是操作说明，不是再讲一遍管线。读完后应能**不经人指点**，为 emitc、vpto、mix 三条路径各自标出：(a) 入口命令，(b) 后端分叉点，(c) 最终产物形态。铁律与前几章相同：`--pto-backend` 只接受 `emitc|vpto`；**VMI 不是第三条 CLI 后端**，它只出现在 vpto 路径内部（见 5.2）；mix 是组合模式，每个子模块仍走 emitc 或 vpto。

### 9.1 建议阅读顺序

1. **第 1 章**：软件栈位置与两后端分叉总图，先建立「driver 调度、ptoas.cpp 编译」的分工。
2. **第 2 章**：入口链四跳、`buildBackendInfo` 如何判定单后端 / mix、`--pto-backend` 只认两值。
3. **冒烟（emitc）**：在已配置好的环境里跑

   ```text
   ptoas test/lit/pto/empty_func.pto
   ```

   默认 `--pto-backend=emitc`。stdout 应出现 `AICORE void hello()`（`test/lit/pto/empty_func.pto:9`）。这一步确认 CLI wrapper → 原生库 → `compilePTOASModule` → C++ 文本这条最短路径是通的。
4. **第 5 章**：从 `tools/ptoas/ptoas.cpp:3794` 的 emitc / vpto 分叉读起，把 5.1 / 5.2 / 5.3 的产物形态对上 9.2 的定位卡。
5. **`test/vpto` 案例**：先看单 vpto 的 `test/vpto/cases/vmi_new/mask-select-store/`（`ptoas.flags` 为 `--pto-arch a5 --pto-backend=vpto`），再看 mix 的 `test/vpto/cases/micro-op/backend/mixed-external-vadd/`（flags **故意不写** `--pto-backend`，见 7.3）。目录约定与 SIM / NPU 跑法见第 7 章 7.3。

第 3、4、6、8 章按需回查即可：写 kernel 时读第 3 章（`.pto` 里三层 IR 对照 3.2.1，支持范围对照 3.2.2），查 Pass 装入条件读第 4 章与附录 A，查 arch × backend × level 或 IR 层 × 后端读第 6 章（含 6.6 / 6.7），发版读第 8 章。

### 9.2 三条路径定位卡（入口 / 分叉 / 产物）

下表是本章的验收面：拿着这三列就能独立走通每条路径。开关与默认值见第 2 章 2.5；lowering 细节见第 5 章。

| 路径 | (a) 入口命令 | (b) 后端分叉点 | (c) 最终产物形态 |
| --- | --- | --- | --- |
| **emitc**（默认单后端） | `ptoas test/lit/pto/empty_func.pto`（等价于显式 `--pto-backend=emitc`）。带 tile 的 lit 同形，例如 `test/lit/pto/tmrgsort_format2_subview_valid_cols.pto`。 | **两层。** ① driver：`buildBackendInfo`（`tools/ptoas/driver.cpp:1249`）判为单后端 → `EmitCBackendJob::run`（`driver.cpp:1037`）。② 编译器：`compilePTOASModule` 在共享 `pm` 之后走 emitc 分支，另建 `emitcPM`（`tools/ptoas/ptoas.cpp:3833-3849`）。判定点是 `ptoas.cpp:3794` 的 effective-backend 分叉。 | **C++ 源码**（kind=`Text`）：含 `#include "pto/pto-inst.hpp"`，空函数出口是 `AICORE void hello()`。driver `writeTextOutput` 落到 stdout 或 `-o`。`--emit-pto-ir` 会在分叉前 dump PTO IR 并返回，见 4.3 第 10 步。 |
| **vpto**（单后端） | 文本冒烟：`ptoas --pto-arch=a5 --pto-backend=vpto --emit-vpto test/lit/vmi_new/vmi_ptoas_cli_pipeline.pto -o -`（`vmi_ptoas_cli_pipeline.pto:9`）。对象模式（默认、要 `-o` 文件路径）：`ptoas --pto-arch=a5 --pto-backend=vpto test/vpto/cases/vmi_new/mask-select-store/kernel.pto -o kernel.fatobj.o`。 | **两层。** ① driver：`VPTOBackendJob::run`（`driver.cpp:1070`）。② 编译器：同一处 `ptoas.cpp:3794-3815` 走 vpto 分支 → `runVPTOBackendPipeline`（`ptoas.cpp:3271`）→ `emitVPTOBackendResult`（`ptoas.cpp:3221`）。无待展开 tile op 时共享 `pm` 整段跳过（`ptoas.cpp:3609-3619`）。**VMI→VPTO 固定序列在 `appendVMISemanticPipeline`（`ptoas.cpp:3303-3338`），始终启用，不是第三条 `--pto-backend`。** | 由 `emitVPTOBackendResult` 三选一（5.2.4）：`--emit-vpto` → **VPTO IR 文本**；`--emit-vpto-llvm-ir` → **`.ll` 文本**；两开关都关 → **fatobj**（kind=`VPTOObject`，经 `emitFatobjLLVM`，`tools/ptoas/ObjectEmission.cpp:1117`）。对象模式必须显式 `-o` 文件路径（`driver.cpp:1111-1115`）。 |
| **mix**（组合模式，不是第三后端） | `ptoas --pto-arch=a5 test/vpto/cases/micro-op/backend/mixed-external-vadd/kernel.pto -o mixed.fatobj.o`。flags 只有 `--pto-arch a5`（该用例 `ptoas.flags:1`），**不要**再写 `--pto-backend`，否则会盖掉按子模块属性拆分的判定（见 2.4 / 7.3）。lit 否定例：`test/lit/vpto/backend_mixed_requires_output_file.pto` 用 `-o -` 会在编译前失败。 | **发生在 driver，不在 `compilePTOASModule` 里新开第三条 lowering。** `buildBackendInfo` / `resolveSingleBackend`（`driver.cpp:1249` / `1190`）认出 backend-partitioned 容器后，`runPTOASJobs` 走 mix 分支（`driver.cpp:1311-1331`）：`collectChildJobs`（`driver.cpp:1151-1188`）按子模块 `pto.backend` 建 `EmitCBackendChildJob` / `VPTOBackendChildJob`，每个 child **仍调用** `compilePTOASModule`，因而各自再经过 `ptoas.cpp:3794` 的 emitc / vpto 分叉。最后 `FatobjLinkJob::run`（`driver.cpp:1008`）链接。 | **单个 fatobj**（kind=`MixedObject`，已写在 `-o`）。各 child 的中间 C++ / LLVM 不落用户可见路径。链接命令是 `bisheng --cce-fatobj-link ...`（`ObjectEmission.cpp:810-832`，见 5.3）。 |

定位时不要把「driver 分派 job」和「`compilePTOASModule` 装 Pass」混成一个点：前者决定跑几个 job、要不要 `FatobjLinkJob`；后者决定每个 job 内部走 emitc 的 `emitcPM` 还是 vpto 的 `runVPTOBackendPipeline`。mix 用到了两层。

### 9.3 首读文件清单

先读这五个文件，比从 `lib/PTO/Transforms/` 逐个 cpp 扫更有效。每个只记一句「它管什么」：

| 文件 | 为什么先读 |
| --- | --- |
| `tools/ptoas/driver.cpp` | CLI 驱动：读输入、判定单后端 / mix、编排 `EmitCBackendJob` / `VPTOBackendJob` / child job / `FatobjLinkJob`。9.2 的 driver 分叉点都在这里。 |
| `tools/ptoas/ptoas.cpp` 的 `compilePTOASModule`（`ptoas.cpp:3355`） | 共享 Pass 管线与 emitc / vpto 编译器分叉（`ptoas.cpp:3794`）。`appendVMISemanticPipeline`、`emitcPM`、`emitVPTOBackendResult` 也在同一文件。 |
| `include/PTO/Transforms/Passes.td` | TableGen 全量 Pass 定义（CLI 名、summary、constructor）。附录 A 的行号都指向这里；「Pass 是什么」以本文件为准，「会不会跑」以 4.3 的 12 步表为准。 |
| `ptodsl/ptodsl/_tracing/runtime.py` | PTODSL 前端 tracing 入口：`@pto.jit` 最终经这里把 Python 调用变成 PTO IR `ModuleOp`（子模块 `pto.backend` 属性也从 tracing / module_builder 写出，见第 3 章）。 |
| `tools/ptoas/ObjectEmission.cpp` | vpto / mix 的最终对象产物：`emitFatobjLLVM`（五步编 fatobj）、`emitFatobjCCE`（emitc child 的 C++ → fatobj）、`linkFatobjs`（mix 链接）。 |

跟读顺序可以记成：`driver.cpp`（怎么进来、怎么分 job）→ `compilePTOASModule`（怎么编译）→ `Passes.td`（每个 Pass 是什么）→ 需要前端时再读 `runtime.py`，需要对象产物时再读 `ObjectEmission.cpp`。

### 9.4 环境准备

构建与运行环境**不要从本文复制命令**。请直接按仓库根目录 `README.md` 的「构建指南 / 运行环境配置」两节操作（对应 README 第 3、4 节：LLVM/PTOAS 构建、Python 安装合同、运行时路径与 nightly wheel）。那两节是环境的单一事实来源；本文只负责管线路径。

配好之后用 9.1 第 3 步的 `empty_func.pto` 冒烟。若 CLI 找不到或产物不是 `AICORE void hello()`，先回头核对 README 那两节，而不是从第 4 章开始改 Pass。上板、npu_validation、`test/vpto` 的 SIM / NPU 双模式见第 7 章。

### 9.5 跟读时常见分叉（避免走错）

- **默认就是 emitc。** 不加 `--pto-backend` 的单模块 `.pto` 走 9.2 第一行，不是 vpto。
- **vpto 默认产物是 fatobj，不是 VPTO 文本。** 想看 IR 必须加 `--emit-vpto`；想看 `.ll` 加 `--emit-vpto-llvm-ir`。这两个开关在 emitc 上会报错（`ptoas.cpp:3382-3388`）。
- **VMI 和物理 `pto.vadd` 不能喂默认 emitc。** 文件里出现 `pto.vmi.*` / `!pto.vmi.vreg` 或 `pto.vlds` / `pto.vecscope` 时必须 `--pto-backend=vpto`（3.2.1 / 6.6）。`test/lit/vmi_new/vmi_ptoas_cli_pipeline.pto` 的 `RUN` 行就是这个开关组合。
- **tile 不会自动变成 VMI。** `pto.tadd` 在 A5 vpto 上展开成 `pto.vadd`（TileOps），不是 `pto.vmi.vadd`。三层都是 `pto` dialect，但没有 `PTOToVMI` pass（3.2.1）。
- **mix 靠模块布局，不靠第三个 backend 名。** 外层只含子 `module`、且子模块带不同 `pto.backend` 时，driver 才进 mix。PTODSL `@pto.jit` 默认产出这种容器（第 3 章）；手写 IR 可参考 `test/lit/vpto/backend_mixed_requires_output_file.pto:11-26`。
- **查 Pass 先看会不会跑。** `Passes.td` 里有定义不等于主管线会装入（例如 `PTOVerifyTFree` 在 `ptoas.cpp:3639` 被注释掉）。装入顺序以第 4 章 4.3 与第 5 章 5.2.3 为准，附录 A 只做检索。

## 附录 A：Pass 速查表

本表覆盖 `compilePTOASModule` 主管线（第 4 章 12 步 + `preBackendPM`）、同步四选一、TileFusion 前端与 vpto 后段、以及第 5 章 `appendVMISemanticPipeline` 固定序列与 VPTO 后端关键 Pass。全量 TableGen 定义以 `include/PTO/Transforms/Passes.td` 为准；未列入本表的 Pass 仍可在该文件按 `def` / CLI 名检索，不必把 `lib/PTO/Transforms/` 每个 cpp 都当成独立 Pass。

「会不会跑」以第 4 章 4.3 / 第 5 章 5.2 的编排为准。行号列是 `Passes.td` 中对应 `def` 的起始行；**不在 TableGen 里的手写 Pass 改标源文件**。

| Pass 名 | 类别 | 一句话职责 | Passes.td 行号 |
| --- | --- | --- | --- |
| `PTOCanonicalizeIR` | 规范化 | 把 rank-2 视图描述符规范成右对齐 rank-5（仅 vpto） | 631 |
| `PTOAssignDefaultFrontendPipeId` | 规范化 | 前端 pipe op 缺省 `id` 补 0 | 485 |
| `PTOLowerFrontendPipeOps` | 规范化 | 前端 TPUSH/TPOP 降到内部 pipe IR | 468 |
| `PTOInferValidatePipeInit` | 规范化 | 推断/校验 pipe init 的 `nosplit` | 502 |
| `PTOLoweringSyncToPipe` | 规范化 | 高层 `record_event`/`wait_event` 降到 `set_flag`/`wait_flag` | 232 |
| `InferPTOLayout` | 规范化 | 推断 GlobalTensor ND/DN/NZ 布局 | 126 |
| `PTOA5NormalizeTMov` | 规范化 | 规范化有风险的 A5 vec→vec col_major `pto.tmov` | 137 |
| `PTOValidateIntToPtrUses` | 校验 | 限制 `inttoptr` 结果只能给标量 load/store | 732 |
| `PTORemoveIdentityTMov` | 规范化 | 删除源目的相同的自拷贝 `pto.tmov` | 150 |
| `PTOMaterializeTileOpSections` | 规范化 | 校验 tileop helper 并物化单一 compute section | 397 |
| `PTONormalizeUncoveredTileSections` | 规范化 | 收集未被 section 覆盖的顶层段 | 355 |
| `PTOValidatePhysicalSectionBoundaries` | 校验 | 校验 cube/vector section 的 SSA 隔离 | 375 |
| `PTOVerifyTFree` | 校验 | 校验 `tpop`/`tfree` 配对（主管线当前注释掉，`ptoas.cpp:3639`） | 716 |
| `PTOMaterializeImplicitTmp` | 内存规划 | 把隐式 tmp tile 物化为显式 alloc | 195 |
| `PTORematerializeFixpipeVectorQuant` | 内存规划 | 在每个 `tpush` 前重物化 fixpipe 向量量化绑定 | 179 |
| `PlanMemory` | 内存规划 | 本地内存地址规划（legacy；level3 跳过） | 211 |
| `PlanMemoryModern` | 内存规划 | modern 内存规划器（`--plan-memory-impl=modern`） | 不在 `Passes.td`；`lib/PTO/Transforms/PTOPlanMemoryModern.cpp:1809` |
| `PTOResolveReservedBuffers` | 内存规划 | 解析 `reserve_buffer` 地址与 peer pipe `flag_base` | 525 |
| `PTOResolveBufferSelect` | 内存规划 | 把 `subview` / `multi_tile_get` 解析成带地址 handle | 748 |
| `PTOInsertSync` | 同步 | 分析 Cube/Vector/MTE 依赖并插入 set/wait | 27 |
| `PTOInjectBarrierAllSync` | 同步 | 在有内存副作用的 pipe op 前插入 `PIPE_ALL` barrier | 44 |
| `PTOBufidSync` | 同步 | A5 交叉流水局部 buffer 依赖改写成 `get_buf`/`rls_buf` | 63 |
| `PTOGraphSyncSolver` | 同步 | 图着色式 set/wait/barrier 求解 | 92 |
| `FusionPlan` | TileFusion | 生成保守融合分组并注 `pto.fusion.group_id`/`order` | 269 |
| `OpScheduling` | TileFusion | 把融合组压成块内连续 span | 302 |
| `PTOMarkLastUse` | TileFusion | 标注调度后 last-use 位掩码（emitc 融合前端） | 314 |
| `PTOFusionRegionGen` | TileFusion | 把一个 span 包成 `pto.fusion_region`（vpto 融合前端） | 326 |
| `PTOLowLevelLoopFusion` | TileFusion | `fusion_region` 内相邻低层循环巢融合（vpto 后段） | 1421 |
| `PTOFusionPredicateElision` | TileFusion | 消除融合区内冗余 `plt` predicate | 1441 |
| `PTOFusionLoadStoreElision` | TileFusion | 消除融合区内 load/store 往返 | 1456 |
| `PTOVexpdifFusion` | TileFusion | 把 f32 `vsub`+`vexp` 融合成 `vexpdif` | 1471 |
| `PTOUnrollAfterLoopFusion` | TileFusion | 融合后按 cost model 部分展开 innermost `scf.for` | 1483 |
| `PTOFlattenFusionRegion` | TileFusion | 把 `fusion_region` 摊回父 block | 1497 |
| `InsertTemplateAttributes` | TileOp | 向 TileLib 查询合法模板候选，写入 `candidates` | 548 |
| `ExpandTileOp` | TileOp | 把 tile op 展开为 TileLib 模板函数调用 | 564 |
| `FoldTileBufIntrinsics` | TileOp | 内联后折叠 `tile_buf_addr` / `valid_rows` / `valid_cols` 等 | 591 |
| `PTOInlineLibCall` | TileOp | 物化并内联 OP-Lib 实例 | 676 |
| `PTOInlineBackendHelpers` | TileOp | 内联 TileOp helper 等后端共享 helper | 694 |
| `PTOExpandSoftLib` | TileOp | 在 VPTO 发射边界物化 SoftOps | 1284 |
| `LowerPTOToUBufOps` | TileOp | a2/a3 上把 `tadd` 等降为 `pto.ub.vadd` 等 | 655 |
| `VMINormalizeSignlessIntToUnsigned` | VMI | 给符号敏感的 VMI op 物化 unsigned carrier | 1215 |
| `VMILowerUnifiedToLegacy` | VMI | 展开 unified VMI 为 legacy 等价物 | 1233 |
| `VMILegalizeArithSelect` | VMI | 把作用在 VMI 值上的 `arith.select` 还原为 `scf.if` | 1197 |
| `PTOValidateVMIIR` | VMI | layout 分配前的 VMI IR 校验 | 1035 |
| `VMIPreAssignmentCombine` | VMI | layout 前合并可合并的 VMI 模式 | 1074 |
| `VMIMaskGranularityAssignment` | VMI | 给 mask 选定粒度（b8/b16/b32） | 1126 |
| `VMILayoutRematerializeWeakProducers` | VMI | 弱 producer 重物化，避免错误的 layout 共享 | 1109 |
| `VMILayoutAssignment` | VMI | 给 `!pto.vmi.vreg` / `!pto.vmi.mask` 分配物理 layout | 1093 |
| `VMILayoutRematerialize` | VMI | 按 consumer 需求重物化 layout | 1162 |
| `VMILayoutFold` | VMI | 折叠多余的 layout 转换 helper | 1145 |
| `VMILayoutSinkMaterialization` | VMI | 下沉 materialization，靠近真正的 consumer | 1180 |
| `PTOValidateVMILayoutIR` | VMI | layout 已分配的 VMI IR 校验 | 1051 |
| `VMIToVPTO` | VMI | layout-assigned VMI → 物理 VPTO | 1260 |
| `VPTOSplitCVModule` | VPTO | 按 cube/vector section 拆成 kernel 子模块 | 429 |
| `VPTONormalizeContainer` | VPTO | 规范化并校验 VPTO kernel 容器形态 | 452 |
| `VPTOScheduler` | VPTO | A5 Vector kernel 的发射前调度分析 | 1318 |
| `VPTOCombineReductions` | VPTO | 合并等价 mask 的 VPTO reduction 树 | 1559 |
| `PTOValidateVPTOEmissionIR` | VPTO | 发射前 VPTO 合法性校验 | 1301 |

主管线里还有几个**不经 `Passes.td` 的包装 / 手写 Pass**，查表时不要到 TableGen 里找同名 `def`：

| Pass 名 | 类别 | 一句话职责 | 源文件行号 |
| --- | --- | --- | --- |
| `SerialFrontendPipeLoweringPass` | 包装 | 串行跑 `PTOAssignDefaultFrontendPipeId` + `PTOLowerFrontendPipeOps` | `tools/ptoas/ptoas.cpp:1397` |
| `SerialAutoSyncPass` | 包装 | `--emit-pto-ir` 时按函数串行跑四种 AutoSync 之一 | `tools/ptoas/ptoas.cpp:1346` |
| `FormEmitCExpressionsCompatPass` | EmitC | 把带 `CExpression` trait 的 op 收成 `emitc.expression` | `tools/ptoas/ptoas.cpp:184` |
| `EmitPTOManualPass` | EmitC | PTO → EmitC 方言转换（A3/A5 变体） | `lib/PTO/Transforms/PTOToEmitC.cpp:13701` |
| `ApplySIMTEntryNoInlinePass` | VPTO | 给 `pto.simt_entry` 函数物化 `no_inline`，再交给标准 inliner | `tools/ptoas/ptoas.cpp:163` |
| `NarrowUnusedMultiResultProvenancePass` | EmitC | 收窄未使用的多结果 provenance location（emitc 主管线第 10 步） | `tools/ptoas/ptoas.cpp:1316` |

`createPlanMemoryModernPass` 工厂在 `lib/PTO/Transforms/PTOPlanMemoryModern.cpp:1858`，与上表 `PlanMemoryModern` 为同一 Pass。

## 附录 B：术语表

| 术语 | 含义 |
| --- | --- |
| **PTO** | 本仓库**唯一**的核心 IR 方言（`pto.*`）。tile 级、VMI、VPTO 微指令都是这个 dialect 里的层，不是三个 dialect（ODS：`PTOOps.td` include `VMIOps.td` / `VPTOOps.td`，见 3.2.1）。各层支持范围见 3.2.2 / 6.7。语义手册见 `docs/PTO_IR_manual.md`。 |
| **VMI** | Virtual Machine ISA：VPTO 后端**内部的**向量语义 IR 层（`pto.vmi.*`、`!pto.vmi.vreg` / `!pto.vmi.mask`），不是 CLI 第三后端。只覆盖 Vector + UB load/store，经 `appendVMISemanticPipeline` 降到物理 VPTO。概念见 `docs/designs/vmi-introduction.md`。 |
| **VPTO** | 两层含义：① **微指令 IR**（`pto.vdup` / `pto.vlds` / `pto.vsts` 等，类型 `!pto.vreg` / `!pto.mask`），覆盖向量、Cube、SIMT、MTE；② **CLI 后端** `--pto-backend=vpto`。规格见 `docs/vpto-spec.md`。哪一层输入能喂哪条后端见 6.6。 |
| **fatobj** | CANN 设备侧「胖对象」：把 host stub 与 cube/vector 设备目标码打进同一对象文件。vpto 单后端默认产物；mix 的最终产物也是一枚 fatobj。 |
| **PTOBC** | PTO Bytecode：带魔数 `"PTOBC\0"` 的二进制模块格式，与 `.pto` 文本并列作为 `loadInputModule` 的输入（`tools/ptoas/driver.cpp:131-134`）。编解码在 `tools/ptobc/`。 |
| **TileLib** | 编译期按需物化的 tile 级算子模板库（`lib/TileOps/`，PTODSL 编写）。`InsertTemplateAttributes` / `ExpandTileOp` 经 `TileLibService` 查询并展开。 |
| **SoftOps** | 标量软算子库（`lib/SoftOps/`，PTODSL 编写）。`PTOExpandSoftLib` 在 VPTO 发射边界物化，例如三角函数近似。 |
| **CCE** | BiSheng 的 CCE 前端模式（`-xcce`）。emitc child 把生成的 C++ 交给 `bisheng -xcce` 编 fatobj；mix 链接用 `--cce-fatobj-link`。 |
| **BiSheng** | 华为设备侧编译器（`ASCEND_HOME_PATH` 下的 `bisheng` / cc1）。vpto 对象模式用它把 `.ll` 编成设备 `.o`，并用 cc1 `-fcce-fatobj-compile` 合成 fatobj。 |
| **AIV** | AI Vector 核（vector / 向量核）。旧 CANN ABI 后缀 `_mix_aiv`；≥ 9.0.0-beta.2 改为 `.vector`（`ObjectEmission.cpp:924-934`）。 |
| **AIC** | AI Cube 核（cube / 矩阵核）。旧后缀 `_mix_aic`；新 ABI 为 `.cube`。 |
| **seam IR** | 共享 pre-backend 管线跑完、尚未进入 emitc / vpto lowering 时的 PTO IR。由 `--pto-print-seam-ir` / `--pto-seam-ir-file` dump（vpto：`ptoas.cpp:3800-3809`；emitc：`ptoas.cpp:3823-3828`）。 |
| **mix-backend** | 组合编译模式：外层容器里每个子 `module` 带自己的 `pto.backend`，各自走 emitc 或 vpto 产临时 fatobj，再 `linkFatobjs` 合成 `-o`。不是第三条 `--pto-backend` 取值。细节见 `docs/designs/mix-kernel-mix-backend-compile-flow.md`。 |
| **emitc** | `--pto-backend` 的两个合法值之一（默认）。把 PTO IR 经 EmitC 方言译成调用 pto-isa 头文件的 C++ 源码。 |
| **level1 / level2 / level3** | `--pto-level` 三档（默认 level2）。level1/2 跑 PlanMemory，alloc 禁止自带 `addr`；level3 **跳过** PlanMemory，要求 alloc / `reserve_buffer` 自带物理地址。level3 **不是**「最高优化档」。InsertSync 是否开启看 `--enable-insert-sync`，不是 level3 的 C++ 副作用（见 4.5）。 |
| **pto-isa** | 外部 C++ 头文件库（`pto/pto-inst.hpp` 等），EmitC 产物通过它调用设备内建。路径由 `PTO_ISA_PATH` / `PTO_ISA_ROOT` 探测（`ObjectEmission.cpp:261-264`）。 |
| **TileLang** | 上游/外部 DSL，本仓库以 `test/tilelang_st` 的 `.pto` 语料接入：ptoas 编 fatobj → `--cce-fatobj-link` → host 可执行。 |
| **PTODSL** | 本仓库的 Python DSL（`ptodsl/`）。`@pto.jit` 经 tracing 生成 PTO IR，再子进程调 `ptoas`；默认产出带 `pto.backend` 的 partitioned 容器。 |
| **PyPTO** | 通过 Python 绑定（`lib/Bindings/Python/PTOModule.cpp`）直接构建 PTO IR 的底层形态，没有 `@pto.jit` 这层糖。不是独立编译入口；构建完的 `ModuleOp` 仍交给 `ptoas` CLI。 |

