# PTOAS 编译流水线深度分析文档 — 设计

- 日期:2026-08-26
- 状态:已获用户批准
- 产出物类型:分析文档(无代码改动)

## 1. 背景与目标

PTOAS 是基于 LLVM/MLIR(LLVM19 `vpto-dev/llvm-project:feature-vpto` 分支)构建的
PTO Bytecode 编译器工具链。CLI 层面存在两个后端
(`enum class PTOBackend { EmitC, VPTO }`,tools/ptoas/ptoas.h:44-47;
`--pto-backend` 仅接受 `emitc|vpto`,tools/ptoas/ptoas.cpp:656-659),支持单后端与
mix-backend 混合编译两种模式;VMI 是 VPTO 后端内部的 IR 层(VMI→VPTO 语义
pipeline 始终启用,README.md:278),同时也是一个独立的 wheel 发布形态
(`ptoas-vmi`),不是 CLI 可选路径。前端入口有多个:PTODSL、TileLang、PyPTO、
`.pto` 文本。当前仓库缺少一份把所有路径端到端串起来的分析文档,新接手的开发者
难以快速建立全貌心智模型。

目标:撰写一份面向新开发者的中文深度分析文档,以数据流为主线,把
"前端入口 → 解析/验证 → Pass 管线 → Lowering → 后端发射 → 运行时/上板"
的完整路径讲清楚,并对比各后端路径的差异与适用场景。

## 2. 产出物

- 单一文档:`docs/ptoas_compilation_pipeline_analysis_zh.md`
  (命名跟随 `docs/` 现有 snake_case + `_zh` 后缀惯例,
  如 `kernel_side_wrapper_fatobj_link_guide_zh.md`)
- 语言:中文;代码标识符/Pass 名/选项保留英文
- 预计规模:1500~2500 行
- 图:至少 3 张 ASCII 图
  1. 软件栈位置图(上游框架 → PTOAS → pto-isa → NPU)
  2. 编译流水线主干图(公共阶段)
  3. 后端分叉图:两个后端(EmitC / VPTO)× 单后端与 mix-backend 组合模式;
     VMI 标注为 VPTO 后端内部的 IR 层(VMI→VPTO 语义 pipeline),
     不画成与两个后端并列的第三条 CLI 路径

## 3. 组织方式(已选定方案 A:按数据流组织)

备选方案:

- **A. 按数据流组织(选定)**:主干按编译数据流阶段走,后端分叉处展开各路径,
  末尾附路径对比总表。符合新开发者认知顺序,重复最少。
- B. 按路径独立成章:每条路径一章端到端描述,查阅方便但公共阶段重复多,
  维护成本高。
- C. 分层架构文档:按 Dialect/Transform/Lowering/Emission 分层,架构视角强
  但"路径感"弱,与目标偏离。

选定 A 的理由:文档主题是"路径分析",数据流主线 + 分叉点对比最贴合;
对比总表(第 6 章)弥补 VPTO 细节被打散到多章的问题。

## 4. 章节结构

| # | 章节 | 内容要点 |
|---|------|---------|
| 1 | 总览 | PTOAS 定位、软件栈位置、两后端 × 单/混合模式一图流 |
| 2 | 入口与驱动 | `tools/ptoas/` CLI 驱动、选项分派(`--pto-arch`/`--pto-backend`/`--pto-level`、`--emit-vpto`、`--vpto-scheduler`) |
| 3 | 前端入口 | PTODSL、TileLang、PyPTO、`.pto` 文本/字节码各自的进入方式 |
| 4 | 公共阶段 | 解析/验证、Pass 管线编排、level 分档对 Pass 的影响 |
| 5 | 后端分叉 | 两个后端各自的完整路径:EmitC 路径(PTO→EmitC/Linalg→C++)与 VPTO 路径(VMI→VPTO 语义 pipeline、`--emit-vpto`、fatobj 发射、host stub);mix-backend 混合编译模式;各含发射产物形态 |
| 6 | 路径对比总表 | arch(A2/A3/A5)× backend(emitc/vpto)× level 组合矩阵;每个单元格标注 可用/有条件(注明条件,如 `--enable-op-fusion`、`--vpto-scheduler`、`--enable-bufid_sync` 要求 `--pto-arch=a5`,tools/ptoas/ptoas.cpp:3395-3417)/不支持,并附证据引用 |
| 7 | 运行时与上板 | pto-isa 调用、npu_validation、kernel-side wrapper 链接 |
| 8 | 发布形态 | ptoas wheel / ptoas-vmi wheel / compiler-only tarball 差异 |
| 9 | 新开发者上手建议 | 从哪条路径切入、先读哪些文件 |
| A | 附录 | Pass 速查表、术语表 |

每个编译阶段的叙述采用统一节奏:**输入 IR 形态 → 处理(Pass/转换)→ 输出 IR 形态**,
实例取自仓库真实测试文件(`test/lit/`、`test/samples/` 下的 `.pto`/`.cpp`),不虚构。

## 5. 证据规范

- **代码优先**:README/docs 与代码冲突时以代码为准,冲突处显式标注。
- **引用格式**:统一 `path/file.cpp:123`,行号基于撰写时的 HEAD。
- **不确定即标注**:无法从代码确证的论断标 `> 待确认:` 块,而不是编造。
- 所有 Pass、入口函数、选项分派点必须给出 file:line 定位。

## 6. 工作流程

1. 代码侦察(后台 explore subagent)产出:Pass 清单、入口调用链、后端分派点、
   前端入口、运行时与发布形态的素材(带 file:line 引用)。
2. 对侦察结果中的关键入口做抽样复核,尤其:
   - `--pto-backend` 的取值与分派逻辑
   - `--emit-vpto` 的流水线分支
   - `--pto-level=level3` 禁用 PlanMemory/InsertSync 的位置
   - fatobj 发射(ObjectEmission/VPTOFatobjEmission)的调用关系
3. 按章节起草,每章写完自查引用有效性。
4. 交付前全文校验:所有 `file:line` 引用逐一核对。

## 7. 验收标准

- [ ] 两个后端路径(EmitC / VPTO)各自的端到端流程可从文档独立走通
      (入口命令 → 中间产物 → 最终产物);VMI 作为 VPTO 路径内部的 IR 层讲清
      其进入方式与 VMI→VPTO 语义 pipeline 的位置;mix-backend 作为组合模式
      单独说明其与单后端模式的差异
- [ ] 对比总表覆盖 arch(A2/A3/A5)× backend(emitc/vpto)× level 组合,
      无效/有条件组合显式标注而非省略
- [ ] 每个被提及的 Pass 有 file:line 定位
- [ ] 所有引用经核对有效
- [ ] 第 9 章(上手建议)满足可检验的读者产出:读者不经帮助能为每条路径
      定位 (a) 入口命令 (b) 后端分叉点 (c) 最终产物形态

## 8. 范围外(明确不做)与交叉引用策略

- 不修改任何编译器代码
- 不新增测试
- 不深入单个 Pass 的算法细节(只讲职责、输入输出与在管线中的位置)
- 不覆盖 `docker/`、CI workflow 细节(仅在发布形态章节提及 nightly wheel)

交叉引用策略:本文是"路径总览",IR 语义与规格细节一律引用已有文档而不复述——
- PTO IR 语义 → `docs/PTO_IR_manual.md`
- VPTO 规格 → `docs/vpto-spec.md`(及 `docs/release/vpto-spec-v*.md` 版本线)
- VMI 概念 → `docs/designs/vmi-introduction.md`、`vmi-implementation-manual.md`
- mix-backend 流程 → `docs/designs/mix-kernel-mix-backend-compile-flow.md`
- 单个 Pass/机制的设计 → `docs/designs/` 对应专项文档
重叠处以"本文讲它在流水线中的位置,细节见上述文档"的分工原则处理,避免重复
或口径漂移。
