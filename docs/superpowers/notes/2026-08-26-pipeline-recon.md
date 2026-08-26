# PTOAS 编译流水线侦察报告(工作材料)

- 日期:2026-08-26
- 性质:撰写 `docs/ptoas_compilation_pipeline_analysis_zh.md` 的素材,由代码侦察产出
- 配套 spec:`docs/superpowers/specs/2026-08-26-ptoas-compilation-pipeline-analysis-design.md`
- 注意:所有 file:line 基于侦察时 HEAD(a82e6cad),撰写时需按 spec §6 逐一复核

---

## 0. 总体架构一句话

`ptoas` 是一个 **Python 包装的原生编译器**:CLI 可执行文件 `ptoas` 是 Python 脚本(wrapper),真正的驱动逻辑在 C++ 共享库 `PTOASCompiler` 中,入口为 `mlir::pto::runPTOAS()`;`driver.cpp` 负责"解析 → 后端分发 → 任务编排",`ptoas.cpp` 负责核心 pass 流水线(`compilePTOASModule`)。

---

## 1. CLI 入口与驱动

### 1.1 入口链(无独立 C++ main)

- `ptoas` 可执行文件实际是 CMake configure 出来的 Python wrapper:`tools/ptoas/ptoas_wrapper.py:73-91` 的 `main()` → `from ptoas import _cli; _cli.launch(...)`。
- `ptodsl/ptoas/_cli.py:50-67` `launch()` 加载原生模块 `ptoas._core`,把打包的 `TileOps` 资源目录插入 `sys.path` 后调用 `native_module.main(argv)`。
- `tools/ptoas/NativeModule.cpp:174-193` `runPTOASFromPython()` 创建 `ptoas.mlir.ir.Context` 并(释放 GIL 后)调用 `mlir::pto::runPTOAS(...)`。
- `tools/ptoas/driver.cpp:1440-1447` `mlir::pto::runPTOAS()` 两个重载都转进 `runPTOASDriver`。
- 注意:`tools/ptoas/ptoas.cpp:324` 的 `int main(int argc, char **argv);` 只是**前向声明**(无定义,历史遗留);CMake 只构建共享库 + Python 扩展(`tools/ptoas/CMakeLists.txt:110-166`,`PTOASCompilerImplementation` OBJECT 库 + `PTOASPythonCore _core` 扩展)。仓库里真正有 C++ `main` 的是 `tools/ptobc/src/main.cpp:126` 和 `tools/pto-test-opt/pto-test-opt.cpp:25`。

### 1.2 驱动主流程(driver.cpp)

`runPTOASDriver`(`tools/ptoas/driver.cpp:1364-1438`)调用链:

1. `registerPTOASDialects` / `registerPTOASPassesAndCLOptions`(driver.cpp:1367/1371;实现于 `ptoas.cpp:326-362`,其中 `mlir::pto::registerPTOPasses()` 在 357 行注册全部 TableGen pass)。
2. `llvm::cl::ParseCommandLineOptions`(driver.cpp:1382)。
3. `readInputBuffer`(driver.cpp:136 → 1403)读输入。
4. **解析阶段** `loadInputModule`(driver.cpp:223-276):
   - 二进制分支:`isPTOBCBuffer`(driver.cpp:131-134,魔数 `"PTOBC\0"`)→ `decodePTOBCModule`(driver.cpp:167-185,调 `ptobc::decodePTOBCToModule`,见 `tools/ptobc/include/ptobc/ptobc_decode.h`)。
   - 文本分支:`parseTextualModule`(driver.cpp:187-221,MLIR `parseAsmSourceFile` + `applyTextualNameHintsToModule` 把文本 SSA 名恢复为 name hints)。
   - arch 解析:`resolveTextInputArch`(driver.cpp:146-165)支持正则从模块里抓 `pto.target_arch`(driver.cpp:119-129)。
5. **后端分发** `buildBackendInfo`(driver.cpp:1249-1296)→ `resolveSingleBackend`(driver.cpp:1190-1247)。
6. **任务编排** `runPTOASJobs`(driver.cpp:1298-1334):
   - 单后端:`EmitCBackendJob::run`(driver.cpp:1037-1068)或 `VPTOBackendJob::run`(driver.cpp:1070-1126)→ 都调用 `compilePTOASModule`。
   - 混合后端(每个 child module 各自 `pto.backend` 属性):`collectChildJobs`(driver.cpp:1151-1188)构造 `VPTOBackendChildJob`(driver.cpp:959)/ `EmitCBackendChildJob`(driver.cpp:907),各自产出临时 fatobj,最后 `FatobjLinkJob::run`(driver.cpp:1008-1035)调 `linkFatobjs` 合并。
   - driver.cpp:1350-1363 有官方 ASCII 图(`.pto` → EmitC job / VPTO job / 混合 child jobs + Fatobj link job → C++ source 或 fatobj)。
7. 输出:`Text` 结果写文件(driver.cpp:1429-1431 / `writeTextOutput` driver.cpp:1336),`MixedObject` 直接成功返回。

### 1.3 关键命令行选项(全部在 `tools/ptoas/ptoas.cpp`)

| 选项 | 定义行 | 取值/默认 | 对流水线的影响 |
|---|---|---|---|
| `--pto-arch` | ptoas.cpp:644-648 | `a2\|a3\|a5`,默认 `a3` | 写入模块 `pto.target_arch`(ptoas.cpp:3404);A2/A3 走 `dav-c220-vec` march(ptoas.cpp:3215-3217)与 `LowerPTOToUBufOps`(ptoas.cpp:3157-3163);A5 才允许 `--enable-bufid_sync`(3395)、`--vpto-scheduler`(3399)、`--enable-op-fusion`(3415) |
| `--pto-level` | ptoas.cpp:650-654 | `level1\|level2\|level3`,默认 `level2` | 解析于 ptoas.cpp:3389-3394(`parseBuildLevel` 726-744);**level3 跳过 PlanMemory**(见 §2.3);level3 要求 alloc 带 `addr`(3548-3568);`pto.tassign` 仅 level3(3498-3502) |
| `--pto-backend` | ptoas.cpp:656-659 | **仅 `emitc\|vpto`**,默认 `emitc` | `parseDriverBackend`(driver.cpp:278-293)只接受这两个值;`resolveSingleBackend`/`collectChildJobs` 分派(driver.cpp:1178-1185、1302-1309);VPTO 分支走 `runVPTOBackendPipeline`(ptoas.cpp:3794-3815),EmitC 分支走 `emitcPM`(ptoas.cpp:3833-3849) |
| `--emit-vpto` | ptoas.cpp:661-664 | bool | `emitVPTOBackendResult`(ptoas.cpp:3224-3231)直接打印最终 VPTO IR 文本,不产出对象 |
| `--emit-vpto-llvm-ir` | ptoas.cpp:666-669 | bool | ptoas.cpp:3233-3244 调 `lowerVPTOModuleToLLVMIRText` 输出 `.ll` 文本 |
| `--vpto-scheduler` | ptoas.cpp:514-522 | `off\|analyze\|on`,默认 off | 在 `prepareVPTOForEmission` 中、最终 CSE 之后、发射校验之前插入 `createVPTOSchedulerPass`(ptoas.cpp:3137-3143);非 A5 报错(3399-3402) |
| `--emit-pto-ir` | ptoas.cpp:639-642 | bool | 主流水线提前 return,dump PTO IR(ptoas.cpp:3770-3780);同步 pass 退化为串行 `SerialAutoSyncPass`(3723-3757) |
| `--plan-memory-impl` | ptoas.cpp:536-540 | `legacy\|modern` | 3708-3712 选择 `createPlanMemoryPass` 或 `createPlanMemoryModernPass` |
| `--enable-insert-sync` 等 4 个互斥同步模式 | ptoas.cpp:524/542/552/558 | 见 §2.3 | 3710-3757 编排 |
| `--enable-op-fusion` | ptoas.cpp:585-591 | boolOrDefault | A5+level≥2 才生效(3441-3447);EmitC 路径加 `FusionPlan+OpScheduling+MarkLastUse`(3675-3679),VPTO 路径加 `FusionPlan+OpScheduling+FusionRegionGen`(3680-3685),以及 post-lowering fusion(3171-3200) |
| `--cann-output-version` | ptoas.cpp:698-701 | 如 `9.0.0-beta.1` | 覆盖探测到的 CANN 版本,影响公开 ABI 后缀(`ObjectEmission.cpp:924-934`:CANN≥9.0.0-beta.2 用 `.vector`/`.cube`,否则 `_mix_aiv`/`_mix_aic`)与 LLVM 发射器选择(`VPTOLLVMEmitterDispatcher.cpp:16-21`) |

**关于 "native/vmi/vpto/mix"**:`--pto-backend` 只有 `emitc` 和 `vpto`(`tools/ptoas/ptoas.h:44-47` 的 `enum class PTOBackend { EmitC, VPTO }`;driver.cpp:284-292)。"vmi" 不是 backend 取值,而是 VPTO backend 内部的一个 IR 层(`pto.vmi.*`,见 §3.2);"mix/mixed" 指**混合后端容器**模式,由每个 child module 的 `pto.backend` 属性驱动(`parseDriverBackendAttr` driver.cpp:295-318;`buildBackendChildCompileUnit` driver.cpp:559-671),最后 fatobj link 合并。

---

## 2. PTO Dialect 与 Pass 管线

### 2.1 include/PTO 与 lib/PTO 组件

**include/PTO**:
- `IR/`:`PTODialect.td`(25: `def PTO_Dialect`)、`PTOOps.td`、`PTOAttrs.td`、`PTOTypeDefs.td`、`PTOInterfaces.td`;**VMI 层**:`VMIOps.td`、`VMIAttrs.td`、`VMITypeDefs.td`、`VMIUtils.h`;**VPTO 层**:`VPTOOps.td`、`VPTOTypeDefs.td`、`VPTOUbOps.td`、`VPTOInterfaces.td`、`VPTOScheduling.h`、`VPTOAddressSemantics.h`;工具:`PTOSyncUtils.h`、`PTOMultiBuffer.h`、`PTOTypeUtils.h`。
- `Transforms/`:`Passes.td`(全部 TableGen pass 定义,1587 行)、`Passes.h`;子框架头:`InsertSync/`(InsertSyncAnalysis、SyncCodegen、SyncEventIdAllocation、MoveSyncState、RemoveRedundantSync 等)、`GraphSyncSolver/`(GraphSolver、EventIdSolver、SyncSolverIR 等)、`TileFusion/`(FusionAnalysis、FusionOpSemantics)、`VPTOScheduler/`(VPTOSchedDAG、VPTOSchedBoundary、VPTOSchedModel 等 7 个头)、`VPTOLLVMEmitter.h`(34-42 `VPTOEmissionOptions`)、`TileLibService.h`、`SoftLibService.h`、`CppPostprocess.h`。
- `Analysis/`:`PTOAddressAnalysis.h`、`PTOValueEvolutionAnalysis.h`。
- `Support/`:`CANNVersion.h`、`CodeConstants.h`、`PythonExecutable.h`。`Compiler/CompilerApi.h`(导出宏)。

**lib/PTO**:`IR/`(PTO.cpp、VMI.cpp、VPTO.cpp、VPTOUbOps.cpp、PTOAttrs.cpp 等)、`Analysis/`、`Transforms/`(约 100 个 cpp + 5 个子目录 TileFusion/InsertSync/BufidSync/GraphSyncSolver/VPTOScheduler)。另有 `lib/CAPI/Dialect/PTO.cpp`(C 接口)、`lib/TileOps/`(Python TileLib 模板)、`lib/SoftOps/`(Python 软件算子,如 `trig.py`、`div_int.py`)。

### 2.2 Transforms 目录 Pass 清单(按 `lib/PTO/Transforms/CMakeLists.txt:27-152` + `include/PTO/Transforms/Passes.td` 的 summary 归纳)

**共享前置/规范化类**
- `ConvertToPTOOp.cpp` — 其他方言 op 转 PTO op(Passes.td:118-124)
- `PTOCanonicalizeIR.cpp` — rank-2 视图描述符规范化到 rank-5(VPTO-only,Passes.td:631-653)
- `InferPTOLayout.cpp` — 推断 GlobalTensor ND/DN/NZ 布局(Passes.td:126-135)
- `InferPTOMemScope.cpp` — 推断内存 scope
- `PTOA5NormalizeTMovPass.cpp` — A5 风险 vec→vec col_major TMOV 规范化(Passes.td:137-148)
- `PTORemoveIdentityTMov.cpp` — 删除自拷贝 TMOV(Passes.td:150-170)
- `LoweringSyncToPipe.cpp` — 高层 record/wait_event 降到 set_flag/wait_flag(Passes.td:232-244)
- `PTOAssignDefaultFrontendPipeIdPass.cpp` — 前端 pipe op 补默认 id=0(Passes.td:485-500)
- `PTOLowerFrontendPipeOpsPass.cpp` — 前端 TPUSH/TPOP 降到内部 pipe IR(Passes.td:468-483)
- `PTOInferValidatePipeInitPass.cpp` — 推断/校验 pipe init nosplit(Passes.td:502-523)
- `PTOWrapFunctionsInSectionsPass.cpp` — 按 kernel_kind 把函数体包进 cube/vector section(Passes.td:337-353)
- `PTONormalizeUncoveredTileSections.cpp` — 收集未被 section 覆盖的顶层段(Passes.td:355-373)
- `PTOValidatePhysicalSectionBoundaries.cpp` — 校验 section SSA 隔离(Passes.td:375-395)
- `PTOMaterializeTileOpSections.cpp` — 校验 tileop helper 并物化单一 compute section(Passes.td:397-427)
- `PTOValidateIntToPtrUses.cpp` — 限制 inttoptr 结果用途(Passes.td:732-746)
- `PTOVerifyTFreePass.cpp` — 校验 tpop/tfree 配对(Passes.td:716-730;当前在主流水线被注释掉,ptoas.cpp:3639)

**内存规划/同步类**
- `PTOPlanMemory.cpp` — 本地内存地址规划(legacy,Passes.td:211-230;`createPlanMemoryPass` PTOPlanMemory.cpp:2874)
- `PTOPlanMemoryModern.cpp` — modern 内存规划器(`--plan-memory-impl=modern`)
- `OptMemPlanForPipeline.cpp` — 面向 pipeline 的内存规划优化
- `PTOResolveReservedBuffersPass.cpp` — 解析 reserve_buffer 地址与 peer pipe flag_base(Passes.td:525-546)
- `PTOResolveBufferSelect.cpp` — subview/multi_tile_get 解析为带地址 handle(Passes.td:748-772)
- `PTOMaterializeImplicitTmp.cpp` — 物化隐式 tmp tile(Passes.td:195-209)
- `PTORematerializeFixpipeVectorQuant.cpp` — 在每个 tpush 前重物化 fixpipe 向量量化绑定(Passes.td:179-193)
- `PTORemoveRedundantBarrier.cpp` — 冗余 barrier 删除
- `InsertSync/PTOInsertSync.cpp` — **AutoSyncInsert**:Cube/Vector/MTE 数据依赖分析并插入显式同步(Passes.td:27-42;`createPTOInsertSyncPass` PTOInsertSync.cpp:157;内部流水:PTOIRTranslator → InsertSyncAnalysis → MoveSyncState:120 → RemoveRedundantSync:135-139 → SyncEventIdAllocation:144 → SyncCodegen:150)
- `PTOInjectBarrierAllSync.cpp` — 保守地在每个有内存副作用的 pipe op 前插 PIPE_ALL barrier(Passes.td:44-61)
- `BufidSync/BufidSyncPass.cpp` — A5 get_buf/rls_buf 同步(Passes.td:63-85)
- `GraphSyncSolver/PTOGraphSyncSolver.cpp` — 图着色式 set/wait/barrier 求解器(Passes.td:92-116)
- `LowerPTOToUBufOps.cpp` — a2/a3 上 tadd/tsub/tmul/tdiv 降为 `pto.ub.vadd` 等(Passes.td:655-674)

**TileFusion 类(lib/PTO/Transforms/TileFusion/)**
- `PTOPreFusionAnalysis.cpp` / `PTOPrintPreFusionAnalysis.cpp` — 块内融合候选分析(Passes.td:246-267)
- `PTOFusionPlan.cpp` — 生成保守融合分组并注 `pto.fusion.group_id/order`(Passes.td:269-300;`FusionPlanPass::runOnOperation` PTOFusionPlan.cpp:595-646,`createFusionPlanPass` 651-658)
- `PTOOpScheduling.cpp` — 把融合组压成块内连续 span(Passes.td:302-312)
- `PTOMarkLastUse.cpp` — 标注调度后 last-use 位掩码(Passes.td:314-324)
- `PTOFusionRegionGen.cpp` — 把一个 span 包成 `pto.fusion_region`(Passes.td:326-335)
- `PTOLowLevelLoopFusion.cpp` — 融合 region 内相邻低层循环巢(Passes.td:1421-1439)
- `PTOFusionPredicateElision.cpp` / `PTOFusionLoadStoreElision.cpp` / `PTOVexpdifFusion.cpp` / `PTOUnrollAfterLoopFusion.cpp` / `PTOFlattenFusionRegion.cpp`(Passes.td:1441-1507)

**TileOp/模板展开类**
- `InsertTemplateAttributes.cpp` — 从 TileLib 运行时查合法模板候选存到 `candidates` 属性(Passes.td:548-562)
- `ExpandTileOp.cpp` — tile op 展开为 TileLib 模板函数调用(Passes.td:564-589)
- `FoldTileBufIntrinsics.cpp` — 内联后折叠 tile_buf_addr/valid_rows/valid_cols/tensor_view 族(Passes.td:591-629)
- `PTOInlineLibCall.cpp`(`PTOLowerToOpLibCalls.cpp`/`PTOInstantiateAndInlineOpLib.cpp` 一并构建)— OP-Lib 实例化与内联(Passes.td:676-692)
- 其余:`PTOLowerToOpLibCalls.cpp`、`Utils.cpp`、`CppPostprocess.cpp`、`TileLibService.cpp`、`SoftLibService.cpp`、`SlotAffineAnalysis.cpp`、`SIMTPersistentFragmentAnalysis.cpp`、`BufferizableOpInterfaceImpl.cpp`

**VMI 语义层(详见 §3.2)**:`VMINormalizeSignlessIntToUnsigned.cpp`、`VMILowerUnifiedToLegacy.cpp`、`VMILegalizeArithSelect.cpp`、`PTOValidateVMIIR.cpp`、`VMIPreAssignmentCombine.cpp`、`VMIMaskGranularityAssignment.cpp`、`VMILayoutRematerializeWeakProducers.cpp`、`VMILayoutAssignment.cpp`、`VMILayoutRematerialize.cpp`、`VMILayoutFold.cpp`、`VMILayoutSinkMaterialization.cpp`、`VMILayoutPropagation.cpp`、`VMILayoutSupport.cpp`、`VMIControlFlowSupport.cpp`、`VMIToVPTO.cpp`、`PTOValidateVMILayoutIR`(Passes.td:1051 定义,cpp 归并在验证文件里)

**VPTO 物理层**:`VPTOSplitCVModule.cpp`(Passes.td:429-450)、`VPTONormalizeContainer.cpp`(452-466)、`VPTOExpandWrapperOps.cpp`(1344-1359)、`VPTOPtrNormalize.cpp`(1509-1525)、`VPTOPtrCastCleanup.cpp`(1527-1539)、`PTOVPTOPtrBoundary.cpp`(1401-1419)、`VPTOOptimizeVcvt.cpp`(1541-1557)、`VPTOMaskSimplify.cpp`(1573-1585)、`VPTOCombineReductions.cpp`(1559-1571)、`VPTOSoftPostUpdate.cpp`(1361-1384)、`PTOInferVPTOVecScope.cpp`(994-1017)、`PTONarrowVPTOLoopCounters.cpp`(901-928)、`PTOUnrollLoopsPass.cpp`(774-825)、`PTOConvertSCFToCFWithLoopHintsPass.cpp`(858-899)、`PTOAnalyzeSIMTPersistentFragment.cpp`/`PTOMaterializeSIMTPersistentFragment.cpp`/`PTOOutlineSIMTSections.cpp`(930-992)、`PTOValidateVPTOIR.cpp`/`PTOValidateVMIIR.cpp`/`PTOExpandSoftLib.cpp`(1019-1316)、`VPTOScheduler/`(pass 入口 `VPTOScheduler/VPTOSchedulerPass.cpp`,Passes.td:1318-1342)、`VPTOLLVMEmitter*.cpp`(发射器,非 pass)、`VPTOBufferMaterialization.cpp`、`VPTOCANN900LLVMEmitter.cpp`

### 2.3 关键 Pass 的注册与编排位置

- **注册**:TableGen 生成 `Passes.h.inc` 由 `mlir::pto::registerPTOPasses()` 注册,该函数在 `ptoas.cpp:357` 被 `registerPTOASPassesAndCLOptions()` 调用(driver 阶段 driver.cpp:1371);`pto-test-opt` 也在 `tools/pto-test-opt/pto-test-opt.cpp:33` 调用。
- **编排(pipeline builder)就是 `compilePTOASModule`,`tools/ptoas/ptoas.cpp:3355-3891`**。主流水线(`pm`,3623 起)顺序:
  1. VPTO-only `PTOCanonicalizeIR`(3635-3637)
  2. `createSerialFrontendPipeLoweringPass`(3638,内部为 PTOAssignDefaultFrontendPipeId + PTOLowerFrontendPipeOps 的串行包装,ptoas.cpp:1396-1430)
  3. `PTOInferValidatePipeInit`(3640)、`LoweringSyncToPipe`(3641)、`InferPTOLayout`(3642-3664)、A5 `PTOA5NormalizeTMov`(3649-3651)、`PTOValidateIntToPtrUses`(3652)
  4. 模板候选(VPTO+A5 才有)`InsertTemplateAttributes`(3658-3660)
  5. A5 融合前端(EmitC 路径 3675-3679 / VPTO 路径 3680-3685)
  6. `PTOMaterializeImplicitTmp`(3687-3689)、`PTORematerializeFixpipeVectorQuant`(3690)
  7. **PlanMemory(level1/level2 才跑)**:`tools/ptoas/ptoas.cpp:3699-3713` —— `if (effectiveLevel != PTOBuildLevel::Level3) { ... pm.addPass(pto::createPlanMemoryPass(...)); }`(legacy/modern 分派 3708-3712)
  8. `PTOResolveReservedBuffers`(3714)、`PTORemoveIdentityTMov`(3715)
  9. **四种 AutoSync 二选一(互斥)**:`ptoas.cpp:3723-3757`(InsertSync 3728 / BufidSync 3737 / BarrierAll 3744 / GraphSolver 3754;`--emit-pto-ir` 时退化成串行 `SerialAutoSyncPass`,定义于 ptoas.cpp:1346-1393)
  10. `PTOResolveBufferSelect`(3761)→ CSE/InlineBackendHelpers/Canonicalize/CSE(3782-3789)
  11. VPTO 分支:先 run 主 pm(3794-3798),可选 seam IR dump(3800-3809/3828),再 `runVPTOBackendPipeline`(3811)→ `emitVPTOBackendResult`(3814)
  12. EmitC 分支:`emitcPM`(3833-3849)= `createEmitPTOManualPass(A3|A5)` + `FormEmitCExpressionsCompatPass` + CSE → 名字 hint 应用 + C++ 翻译(3851-3890)

**level3 与 PlanMemory/InsertSync 的确切关系**:
- PlanMemory 跳过:`tools/ptoas/ptoas.cpp:3699`(`if (effectiveLevel != PTOBuildLevel::Level3)`)
- InsertSync:**代码里 level3 本身不自动关 InsertSync**,而是 (a) `pto.tassign` 存在时强制要求 `--enable-insert-sync` 关闭(`ptoas.cpp:3504-3508`:"pto.tassign requires --enable-insert-sync to be disabled");(b) PTODSL 侧 `mode="explicit"`(对应 level3)默认 `insert_sync=False` 且 `pto_level=level3`(`ptodsl/ptodsl/_runtime/native_build.py:73-80`)。README 的"level3 会禁用 PlanMemory/InsertSync"说法即来源于此组合(`README.md:275-276`)。
- 另有 level3 相关校验:reserve_buffer 必须显式 base(ptoas.cpp:802-839)、alloc_tile 必须带 addr(3548-3568)。

---

## 3. Lowering 路径

### 3.1 EmitC 路径(PTO IR → EmitC → C++)

链条:主流水线(ptoas.cpp:3623-3789)→ `emitcPM`:
- `pto::createEmitPTOManualPass(PTOArch::A3|A5)`(`ptoas.cpp:3835-3839`);pass 实现在 `lib/PTO/Transforms/PTOToEmitC.cpp:13701`(`EmitPTOManualPass`),工厂 14287-14289。它做的第一件事就是插头文件:`PTOToEmitC.cpp:13769-13772`:

```cpp
builder.create<emitc::IncludeOp>(loc, "pto/pto-inst.hpp", /*is_standard_include=*/false);
builder.create<emitc::VerbatimOp>(loc, builder.getStringAttr("using namespace pto;"));
```

- `FormEmitCExpressionsCompatPass`(ptoas.cpp:3840;定义 ptoas.cpp:184-259,为 LLVM19 兼容手写 EmitC 表达式折叠)。
- `mlir::emitc::translateToCpp`(ptoas.cpp:3869-3873)。
- 文本后处理:`rewriteTileGetSetValueMarkers` / `rewriteAsyncEventMarkers` / `rewriteLastUseMarkersInCpp` 等一串 marker 重写(ptoas.cpp:3875-3886),底层实现在 `lib/PTO/Transforms/CppPostprocess.cpp`。
- **注意**:README.md:11 提到 "EmitC / Linalg Dialect",但代码流水线中未出现任何 linalg pass(grep 无 `linalg` 于 ptoas.cpp/driver.cpp);实际 EmitC 路径不经过 linalg。→ 文档中按 spec §5"代码优先"原则显式标注此冲突。

### 3.2 VMI 层(vmi 类型如何进入、VMI→VPTO)

- **类型进入**:`!pto.vmi.vreg` / `!pto.vmi.mask` 等类型定义在 `include/PTO/IR/VMITypeDefs.td`、`VMIOps.td`、`VMIAttrs.td`;前端可由 PTODSL 的 `ptodsl/ptodsl/_vmi_namespace.py`("Public PTODSL namespace for formal VMI APIs",第 8 行)直接发 `pto.vmi.*` op;也可在 `.pto` 文本里手写(测试见 `test/lit/vmi_new/`、`test/vpto/cases/vmi_new/`)。
- **VMI→VPTO 语义 pipeline 就在 `appendVMISemanticPipeline`,`tools/ptoas/ptoas.cpp:3303-3338`**,由 `runVPTOBackendPipeline`(ptoas.cpp:3291)在 kernel 模块上调用。固定顺序:`VMINormalizeSignlessIntToUnsigned` → `VMILowerUnifiedToLegacy` → `VMILegalizeArithSelect` → `PTOValidateVMIIR` → `VMIPreAssignmentCombine` → `VMILegalizeArithSelect` → `VMIMaskGranularityAssignment` → `VMILayoutRematerializeWeakProducers` → `VMILayoutAssignment` → `VMILayoutRematerialize` → `VMILayoutFold` → `VMILayoutSinkMaterialization` → `VMILegalizeArithSelect` → `PTOValidateVMILayoutIR` → **`VMIToVPTO`**(ptoas.cpp:3337)。
- `VMIToVPTO` pass 本体:`lib/PTO/Transforms/VMIToVPTO.cpp:14381-14412`(`VMIToVPTOPass::runOnOperation` 用 `applyPartialOneToNConversion` + `VMIToVPTOTypeConverter`,14401),工厂 `createVMIToVPTOPass` 在 14416。语义说明见 `include/PTO/Transforms/Passes.td:1260-1282`:"Convert layout-assigned VMI IR to physical VPTO IR"。
- README.md:278-280 明确:"VPTO backend 总是启用 VMI -> VPTO 语义 pipeline;public function signature 不能直接暴露 `!pto.vmi.*` 类型"。

### 3.3 VPTO 路径(--emit-vpto / fatobj / host stub)

- `--emit-vpto`:`emitVPTOBackendResult`(`ptoas.cpp:3221-3269`)。三分支:
  1. `emitVPTO` → 直接把最终 VPTO IR 打印到 `-o`(3224-3231);
  2. `emitVPTOLLVMDialect` → `lowerVPTOModuleToLLVMIRText` 输出 `.ll` 文本(3233-3244);
  3. 默认对象模式 → `emitVPTOHostStubSource`(3250-3255,仅当有 PTO entry)+ `lowerVPTOModuleToLLVMModules` 产出 **cube/vector 两个 LLVM 模块**(3257-3264,声明于 `include/PTO/Transforms/VPTOLLVMEmitter.h:58-61`),结果 kind = `VPTOObject`(3267)。
- LLVM 发射器按 CANN 版本分派:`lib/PTO/Transforms/VPTOLLVMEmitterDispatcher.cpp:52-65`(≥9.0.0 且非 C220 走 `lowerVPTOModuleToLLVMModulesCANN900`,否则 Beta1)。发射的底层是 `llvm.hivm.*` intrinsic(如 `VPTOLLVMEmitter.cpp:528` `"llvm.hivm.MMAD.MX."`、`VPTOLLVMEmitter.cpp:609-657` 一系列 `llvm.hivm.MAD.*`)。
- **VPTOBackendJob 收尾**(driver.cpp:1070-1126):要求 `-o` 显式路径(1111-1115)→ `emitVPTOLLVMFatobj`(driver.cpp:1128-1149)→ `emitFatobjLLVM`。
- **`emitFatobjLLVM`(`tools/ptoas/ObjectEmission.cpp:1117-1152`)做 5 步**(封装在 `VPTOFatobjArtifacts`,ObjectEmission.cpp:328-489):(1) 写 host stub .cpp(333);(2) cube LLVM 模块 → `.ll` → bisheng 编 `.o`(350-364,目标 `dav-c310-cube`,ObjectEmission.cpp:305-313);(3) vector 模块同理 + 可选 `verifyAndPatchVFSIMTSize` 修复 0xffff VF_SIMT 尺寸(366-405);(4) `mergeDeviceObjects` 用 ld.lld 合并(407-426);(5) `compileHostStubToFatobj` 用 `cce-ld` 把 stub+device 合成 fatobj(441-449、1146-1150)。设备编译 flags 见 ObjectEmission.cpp:528-546(`--cce-aicore-only -O2 -dc -cce-bitcode-is-aicore` 等)。
- **`emitFatobjCCE`(ObjectEmission.cpp:1012-1025)**:EmitC 子任务路径 —— 把 EmitC 生成的 C++ 文本写临时文件,`compileCppDeviceSourceToFatobj`(`-xcce --cce-aicore-enable-tl --cce-aicore-only ... -fcce-fatobj-compile`,ObjectEmission.cpp:589-607、699-707)编成 fatobj。
- **`linkFatobjs`(ObjectEmission.cpp:1177-1184)**:混合后端时把多个 fatobj `--cce-fatobj-link` 合并到 `-o`。
- **`VPTOHostStubEmission`**(`tools/ptoas/VPTOHostStubEmission.cpp`,声明 VPTOHostStubEmission.h:24-29):遍历 PTO entry 函数收集 kernel 声明(`collectVPTOKernelStubDecls` 84-120),逻辑名通过剥 `_mix_aiv`/`_mix_aic` 后缀得到(34-42),参数 C 类型映射如 `PtrType/MemRefType → "__gm__ void *"`(75-80),产出供 host launch 的 stub C++ 源。
- **fatobj 形态**:CCE "fat object"——一个可被 host 链接器消费的 `.o`,内含 AIV/AIC(vector/cube)两份设备代码特化 + host stub 符号;ABI 符号后缀由 `vptoPublicABISuffix` 决定(ObjectEmission.cpp:924-934:新 ABI `foo.vector`/`foo.cube`,旧 ABI `foo_mix_aiv`/`foo_mix_aic`,`applyVPTOLLVMABINames` 1056-1073 负责改名)。语义见 `docs/kernel_side_wrapper_fatobj_link_guide_zh.md`(§5)。
- 相关调试 flag:`--pto-print-seam-ir` / `--pto-seam-ir-file`(ptoas.cpp:687-696)dump 后端共享 seam IR(打印/写文件 ptoas.cpp:864-894、3800-3828)。

---

## 4. 前端入口(ptodsl/)

### 4.1 目录职责

- **`ptodsl/ptodsl/`** = PTODSL Python DSL 包(`@pto.jit`),发布时打进根 `ptoas` wheel。核心模块(`ptodsl/README.md:13-33`):`pto.py`(主命名空间)、`scalar.py`、`_jit.py`(`@pto.jit` 装饰器)、`_kernel_compilation.py`(KernelCompiler/CompiledKernelHandle)、`_tracing/`(tracing 运行时)、`_ast_rewrite.py`(Python for/if AST 重写)、`tilelib/`(TileLib 渲染运行时)、`softlib/`。
- **`ptodsl/ptoas/`** = **安装后的顶层 `ptoas` Python 包的源目录**(只有 `__init__.py` 和 `_cli.py`):提供 `ptoas` console script 的 Python 入口(`_cli.py:50-67`)以及构建时被 stage 进 wheel(`tools/ptoas/CMakeLists.txt:184-191` `PTOASRuntimePythonSources ROOT_DIR ptodsl/ptoas`)。即:`ptodsl/ptoas` 是 CLI/runtime 包,`ptodsl/ptodsl` 是 DSL 本体。

### 4.2 PTODSL 如何构建 PTO IR:**tracing(Python 回调 → MLIR builder),不是 AST 解释器也不是 C++ JIT**

- `@pto.jit` 装饰(`ptodsl/ptodsl/_jit.py:8`);backend 合法值 `{vpto, emitc}`(_jit.py:70)。
- `KernelCompiler.compile()`(`ptodsl/ptodsl/_kernel_compilation.py:104-`):先 `rewrite_jit_function` 做 AST 重写(96-101,`_ast_rewrite.py`),再构造 `SignatureTracingRuntime`(126-130)。
- **`TracingRuntime.build_module()`(`ptodsl/ptodsl/_tracing/runtime.py:80-97`)是 IR 构建核心**:创建 MLIR Context(`make_context`)→ `create_kernel_module(spec, arg_types)` 建模块骨架 → 在 entry block 的 InsertionPoint 里激活 session、执行用户 Python 回调 `trace_entry`(期间 `_ops.py` 里的包装器逐个发 `pto.*` MLIR op)→ verify。
- 模块三种布局 `ModuleStyle`(`ptodsl/ptodsl/_tracing/module_builder.py:19-24`):`FLAT_AICORE` / `NESTED` / `BACKEND_PARTITIONED`(默认 NESTED,spec 默认 backend="vpto",module_builder.py:35、39);child module 会带 `pto.target_arch`/`pto.backend`/`pto.kernel_kind` 属性(74-84)——这正是 driver.cpp 混合后端容器所消费的形态。
- 编译为原生库(host 可调用):`ptodsl/ptodsl/_runtime/native_build.py`(docstring 第 8 行:"MLIR → ptoas → bisheng native library build")。`_run_ptoas`(42-70)以子进程调用 ptoas CLI:`ptoas --pto-arch=... [--pto-backend=...][--pto-level=...] [--enable-insert-sync] --enable-tile-op-expand mlir -o kernel_object`,随后 bisheng 编 launch.cpp(`codegen.py:95` `generate_launch_cpp`)并链接(`_kernel_compile_flags` native_build.py:125-148)。缓存于 `~/.cache/ptodsl/`(`ptodsl/README.md:248`)。

### 4.3 TileLang 集成与 PyPTO/CuTile

- **`test/tilelang_st/`** 是 TileLang 系统级(ST, system test)验证:`test/tilelang_st/npu/a5/src/st/smoke/testcase/CMakeLists.txt:9-17` 注释直述流程:"(1) Runs ptoas to compile `<NAME>.pto` → kernel.fatobj.o;(2) Links the fatobj object with launch.cpp → shared library;(3) Builds host executable from main.cpp"。函数 `pto_tilelang_st`(25-95)把上述三步接起来,fatobj 以 `--cce-fatobj-link` 链接(75 行),vec/cube 变体 97-111。用例注册表 116-205(tadd…tmatmul 共 88 个)。
- **`lib/TileOps/`** 是 PTODSL TileLib 的 Python 模板库(`lib/TileOps/a5/tadd.py`、`div_hp.py` 等),由 `lib/PTO/Transforms/TileLibService.cpp` + `include/PTO/Transforms/TileLibService.h` 提供进程内服务;`ExpandTileOp`/`InsertTemplateAttributes` 消费(Passes.td:548-589;ptodsl/README.md:87-106)。安装时复制到 `ptoas/_runtime/share/ptoas/TileOps`(`tools/ptoas/CMakeLists.txt:195-203、264-270`),`_cli.py:34-47` 运行时定位。
- **PyPTO/CuTile**:README.md:12 提及("支持 PyPTO、PTODSL、CuTile 等框架在 Python 端直接构建…");Python 绑定本体在 `lib/Bindings/Python/PTOModule.cpp`(被链进 `PTOASCompiler`,`tools/ptoas/CMakeLists.txt:118`)+ `lib/CAPI/Dialect/PTO.cpp` + `include/pto-c/Dialect/PTO.h`。仓库内没有名为 CuTile 的目录(属外部/上层框架,仅文档引用)。
- 其他前端形态:`.pto` 文本、PTOBC 二进制(`tools/ptobc/`,driver.cpp:131)、`test/dsl/`、`test/dsl-st/`(PTODSL 运行时正确性,ptodsl/README.md:282-284)。

---

## 5. 运行时与验证

### 5.1 生成代码如何调用 pto-isa

- EmitC 输出的 C++ 顶部 `#include "pto/pto-inst.hpp"` + `using namespace pto;`(`lib/PTO/Transforms/PTOToEmitC.cpp:13769-13772`)——即生成的 C++ 通过 **PTO-ISA C++ 头文件库**(`pto-inst.hpp`)调用设备内建。
- pto-isa 是**外部依赖**(不在本仓库):路径由环境变量 `PTO_ISA_PATH`/`PTO_ISA_ROOT` 提供(`tools/ptoas/ObjectEmission.cpp:261-264`),头文件探测 `hasPTOISAHeader` 检查 `include/pto/pto-inst.hpp`(225-227),include 目录组装 `discoverCppIncludeDirs`(256-282,还加 `$ASCEND_HOME_PATH/include` 与 driver 的 `kernel/inc`)。未设置时告警"可能无法 include pto/pto-inst.hpp"(273-275)。
- CI 有 `.github/workflows/update_pto_isa_pin.yml`(pin 更新),印证其外部仓库属性。

### 5.2 test/npu_validation 与 test/vpto

- **`test/npu_validation/`**:把 ptoas 输出的 `.cpp` 自动生成 NPU 验证用例并上板。入口 `test/npu_validation/scripts/generate_testcase.py`(用法见 README.md:346-366):`python3 test/npu_validation/scripts/generate_testcase.py --input test/samples/MatMul/tmatmulk.cpp --run-mode npu --soc-version Ascend910B1|Ascend950`,生成 `tmatmulk_kernel.cpp / main.cpp / golden.py / compare.py / run.sh / CMakeLists.txt`(README.md:364),golden 随机输入、compare.py 对比 bin(365-366)。
- **`test/vpto/`**:VPTO backend 端到端用例集。`test/vpto/cases/**/ptoas.flags` 每个用例一行编译参数,典型 `--pto-arch a5 --pto-backend=vpto`(如 `test/vpto/cases/vmi_new/kernels/dynamic-quant-perchannel-f16-8x256/ptoas.flags:1`)。目录含 `elementwise-1d-2d-equivalence.py`、`kernels/`、`micro-op/`、`onboard-only/`、`vmi_new/`。驱动脚本 `test/vpto/scripts/run_host_vpto_validation.sh`:默认 `PTOAS_BIN=install/bin/ptoas`、`PTOAS_FLAGS="--pto-arch a5 --pto-backend=vpto"`(20-21 行)、`DEVICE=SIM|NPU`、SIM 下自动找 `simulator/dav_3510/lib`(78-95)、bisheng 位于 `$ASCEND_HOME_PATH/bin/bisheng`(98)。
- 其他测试面:`test/lit/`(FileCheck lit 套件,`test/lit.cfg.py`)、`test/compile_cpp/`、`test/kernel-test/`、`test/tilelib-st/`、`test/python/`、`test/samples/`(含 `TInsert/board_validation`)。

### 5.3 上板流程(fatobj / kernel_side_wrapper)

`docs/kernel_side_wrapper_fatobj_link_guide_zh.md` 快速浏览结论:
- 目的(第 3-5 行):验证 **kernel-side C++ wrapper/caller 与 separately compiled 的 vector/cube callee object 通过 `--cce-fatobj-link -r` 合并成 `bundle.o`,再与 host `main.o` 链接运行**——为 pypto kernel-side wrapper 调用 PTOAS-vpto fatobj 的 ABI 设计做预研。
- 核心结论(23-59 行):可行,条件是符号一致。sectioned caller 在 `ASCEND_IS_AIV` 分支调 vector callee、`ASCEND_IS_AIC` 分支调 cube callee(29-35);device linker 需要的符号是 `vec_callee.vector` / `cube_callee.cube`(38-50)。**对 PTOAS-vpto 的含义(52-59 行):vector direct-call callee 必须导出 `foo.vector`,cube 导出 `foo.cube`;如果只导出 `foo`/`foo_mix_aiv`/`foo_mix_aic`/`foo.vector.thread` 则不满足 direct-call ABI**——这正对应 `vptoPublicABISuffix` 在 CANN≥9.0.0-beta.2 时切换到 `.vector/.cube` 的实现(ObjectEmission.cpp:924-934)。
- 完整命令链(227-275 行):`bisheng -dc -xcce --cce-aicore-arch=dav-c310-vec|-cube|-(mixed)` 分别编译三个 TU → `bisheng --cce-fatobj-link -r -o bundle.o caller.o vec_callee.o cube_callee.o`(253-255)→ host `g++ main.o bundle.o -lruntime -lascendcl ...`(270-274)。选项语义:`-dc` 生成可参与设备分离编译的 CCE object(289-293);`--cce-fatobj-link -r` 做 relocatable fatobj link(295-297)。

---

## 6. 版本与发布路径

- **主线 release**:tag `ptoas-vX.Y` → Python 包名 `ptoas`;**VMI release**:tag `vmi-vA.B.C` → 包名 `ptoas-vmi`。约定见 `README.md:183-188`(及英文版 README_en.md:172);分派逻辑在 `.github/workflows/build_wheel.yml:99-148`:release tag 匹配 `vmi-v*` → `PTOAS_PYTHON_PACKAGE_NAME="ptoas-vmi"`、`PTOAS_CLI_VERSION="vmi A.B.C"`(103-108);匹配 `ptoas-v*`/`v*` → 主线 `ptoas`(109-120)。两者**互斥安装**(README.md:177-179:同名顶层 `ptoas` Python 包和 console script,混装互相覆盖)。
- 主线版本来自 `CMakeLists.txt:47` `project(ptoas VERSION 0.61)`,regex 提取(被 vmi patch 删除的正是这段);CLI 版本串由 `PTOAS_RELEASE_VERSION`/`PTOAS_CLI_VERSION_LABEL` 控制(`CMakeLists.txt:56、61`;`ptoas.cpp:53-55、65-67` 打印 `ptoas <version>`)。VMI 线 `ptoas --version` 显示 `ptoas vmi A.B.C`(README.md:175-176)。
- **`packaging/ptoas-vmi/` 做的事**:
  - `prepare_source.py`(docstring 第 10 行:"Prepare the complete source tree used to build the `ptoas-vmi` wheel"):git archive 当前 revision 到 staging 目录 `.work/ptoas-vmi-source`(92-150,排除 `.agents/.claude/.codex`,33-37),应用 metadata patch,**不修改工作区根 pyproject.toml、不产 sdist**(README.md:186-188)。`--print-version` 从 patch 里读唯一静态版本(61-65)。
  - `pyproject.toml.patch`:改包名 `ptoas` → `ptoas-vmi`(第 9 行)、静态 `version = "0.1.6"`(第 10 行)、描述加 "with VMI support"(11)、删掉从 CMakeLists 提取版本的 regex provider(26-30)、加 `sdist.inclusion-mode = "manual"`(19)和 `PTOAS_CLI_VERSION_LABEL = "vmi"`(24)。
  - wheel 构建时 `build_wheel.yml:244-246`:若包名为 ptoas-vmi,先跑 `prepare_source.py` 生成 staging tree 再从那里构建。
- nightly:定时任务(build_wheel.yml:18 cron `10 17 * * *`)发布到 `nightly` release,`tools/install_nightly_wheel.py` 安装(README.md:216-238)。
- 其余 workflow:`ci.yml`、`ci_a5.yml`、`ci_sim.yml`、`build_wheel_mac.yml`、`a3_taskqueue_smoke.yml`、`sync_base_version.yml`、`watchdog.yml`(`.github/workflows/`)。

---

## 附:值得写进文档的补充事实

1. **PTOBC 双格式输入**:ptoas 同时接受文本 `.pto` 与二进制 PTOBC(魔数 `PTOBC\0`,driver.cpp:131-134);ptobc 工具在 `tools/ptobc/`(编解码 + roundtrip 测试数据 `tools/ptobc/testdata/`)。
2. **wrapper vs install wrapper**:`ptoas_wrapper.py` 被 configure 两次(build-tree 绝对路径版与 install 相对路径版,`tools/ptoas/CMakeLists.txt:36-53`),安装名 `bin/ptoas`(231-235)。
3. **VfSimulator 联动**:fusion costmodel 可选链接 `vfsim::ir_planner`(`lib/PTO/Transforms/CMakeLists.txt:19-22、215-222`,`PTO_ENABLE_VFSIM_IR_PLANNER`);3rdparty 子模块 `3rdparty/VfSimulator`、`3rdparty/PTO-Gym`(`.gitmodules`)。
4. **`--vpto-fix-vfsimt-size`**(ptoas.cpp:703-714):auto/verify/off 三态修复 vector 对象里非法的 0xffff VF_SIMT 尺寸,patcher 在 `tools/ptoas/VFSIMTSizePatcher.cpp`。
5. **文档锚点**:`docs/vpto-spec.md`、`docs/release/vpto-spec-v0.3.md`、`docs/PTO_IR_manual.md`、`docs/ptoas-tile-fusion-design.md`、`docs/bufid_sync_a5_design.md`、`docs/no_npu_compile_only_guide_zh.md`、`docs/msprof_op_simulator_usage_zh.md`,PTODSL 手册在 `ptodsl/docs/user_guide/`(VMI 用户指南为 `14-vmi-virtual-instruction-set.md`)。
6. **两处"代码优先"冲突点**(spec §5 要求显式标注):
   - README.md:11 称 EmitC 经 "EmitC / Linalg Dialect",但流水线代码中无任何 linalg pass;
   - README.md:275 称 "level3 会禁用 PlanMemory/InsertSync",代码中 level3 仅跳过 PlanMemory(ptoas.cpp:3699),InsertSync 的关闭来自 flag 默认值与 PTODSL mode="explicit" 组合。
