# 云侧算子迁移端侧（Kirin 9030）指南

> 日期：2026-09-01
> 背景：将云侧（910/910B/950 系，npuarch 如 `DAV_1001`/`DAV_2201`/`DAV_3510`）编写的 PyPTO 算子迁移至端侧 Kirin 9030。
> 案例算子：`python/tests/st/operator/flash_attention_mha/flash_attention_mha_impl.py` 的 `flash_attention_varlen_forward_kernel`。
> 状态：本指南基于访谈共识与仓库内事实（SocInfo 配置、UT 惯例）撰写，**所有代码改动均未经真机/SIM 验证**，待验证项见第 7 节。

## 0. 总原则：复用优先

**能不改的代码一律不改。** 迁移的改动面应收敛到"端侧确实不支持的点"：

- 不影响的代码尽量保留（见下方例外）；
- 签名尽量不改（见第 4.1 节：`cu_seqlens` 保留在签名中，仅替换 kernel 内部三处值读取）；
- 计算过程不动，dtype 只做类型替换（见第 3 节）；
- tile 参数不替用户推导，只给差异事实（见第 5 节）。

**复用原则的一个例外**：云侧的 `if pypto.platform.npuarch == 'DAV_3510': set_pass_options(sg_set_scope=...)` 分支原先依赖 `pypto.loop` 的符号执行保持惰性（端侧求值恒为假、永不生效）；改原生 `range` 后，`pypto.platform.npuarch` 与 Python `if` 都在 trace 期求值——若 `npuarch` 是运行期属性，分支反而会求值出错或语义改变。**这类"惰性平台分支"在循环表达迁移时需一并移除**，这是 `pypto.loop → range` 改动的连带成本。

## 1. 平台差异事实表

来源：`framework/src/platform/parser/simulation_platform/platform_config/Kirin9030.ini`（端侧）与云侧典型配置对比。

| 参数 | Kirin9030（端侧） | 云侧典型（910B/950 级） | 迁移影响 |
|---|---|---|---|
| `ub_size` | **128 KB**（131072 B） | ~256 KB–1 MB | tile 容量大幅缩水，云侧 tile 配置基本不可行 |
| `l1_size` | 512 KB（524288 B） | 数 MB | cube 分块受限 |
| `cube_m/n/k_size` | **16 / 8 / 16** | 128 级 | cube tile 粒度小一个数量级，tile 必须是其整数倍 |
| `vec_calc_size` | 128 | — | vec tile 对齐约束 |
| `ai_core_cnt` | **1** | 数十核 | 多核切分/调度不复存在 |
| `ubblock_size` | 32 | — | UB 对齐约束 |
| dtype（UT 覆盖） | FP16/FP32/INT8/16/32/UINT8，**无 BF16 用例** | BF16/FP32 全覆盖 | BF16 输入与 FP32 中间量需迁移 |
| 批次支持 | **单 batch** | 多 batch | varlen 拼包模式不可用 |

另：Kirin9030 有 SIM 仿真平台配置（`simulation_platform.cpp` 中 `KIRIN_9030` 映射），仓库 UT（`python/tests/ut/kirin/`）以 `runtime_options={"run_mode": pypto.RunMode.SIM}` 运行，无需真机。

## 2. 差异一：静态 shape 约束（影响最深）

### 2.1 约束的精确含义

端侧不仅要求张量 shape 编译期已知，**循环边界与分支条件也必须编译期确定**。云侧 varlen FA 的核心控制流模式——从 `cu_seqlens` tensor 内容读出运行期数值（`seq_len_q = q_end - q_start`）经 `as_variable()` 驱动 `pypto.loop`——在端侧必须彻底消除。

### 2.2 端侧调用契约

- **单 batch**：调用方一次只处理一条序列（或一个 batch 的等长 padding）；
- **S = S_max**：序列维按最大 seqlen 设置静态 shape，调用方自行 padding；
- **`cu_seqlens` 必须为 `[0, S_max]`**：签名保留该参数（见 4.1），但其**值不参与计算**——若真传多 batch 拼包 + 真实累积长度，会**静默出错**（结果错但不报错）。

### 2.3 kernel 内部的改法（FA 案例逐处对照）

| 云侧代码 | 端侧改法 |
|---|---|
| `q: pypto.Tensor([pypto.DYNAMIC, ...], ...)` | 保持 DYNAMIC 策略注解**不改**——静态值由调用时传入的具体 tensor 烘进编译变体（pypto_pro 的 shape policy 测试 `test_shape_policy_codegen.py` 明确验证了 STATIC 值烘入 IR、STATIC 变化触发新变体、DYNAMIC 维不烘入）。端侧调用方传静态 tensor 即得静态 kernel |
| `cu_seqlens_q: pypto.Tensor([pypto.DYNAMIC], DT_INT32)` | 注解改 `[pypto.STATIC]`：其 shape 驱动 batch loop，驱动循环的维度必须 STATIC |
| `batch_size = cu_seqlens_q.shape[0] - 1` | **原样保留**——shape 读取是静态信息，端侧传 `[2]` 张量即得 batch_size=1 常量 |
| `q_start = cu_seqlens_q[b_idx]` 等值读取 | **删除**，替换为静态推导：`q_start = 0`、`q_end = seq_len_q`，其中 `seq_len_q = q.shape[1]`（或等价静态维） |
| `seq_len_q.as_variable()` / `seq_len_k.as_variable()` | **删除**（连同其依赖的值读取） |
| `q_tile_count = (seq_len_q + q_tile - 1) // q_tile` | 保留——`seq_len_q` 已是编译期常量，整式成为常量表达式 |
| `for b_idx in pypto.loop(batch_size, name=...)` 四层循环 | **改为原生 `range`**——静态场景不支持 `pypto.loop`；静态 shape 下 `q.shape[0]`、tile_count 均为普通 Python int（`SymInt = Union[int, SymbolicScalar]`，静态维直接返回 int），`range()` 天然可用 |
| `pypto.is_loop_begin(k_tile_idx)` / `pypto.is_loop_end(k_tile_idx)` | **改为整数比较** `k_tile_idx == 0` / `k_tile_idx == k_tile_count - 1`——`is_loop_begin/end` 要求 `pypto.loop` 产出的 SymbolicScalar 入参（否则 `raise FeError("not loop index")`），原生循环下不可用；整数比较在 trace 期直接求解 |
| `pypto.min(q_tile_start + q_tile, seq_len_q)` 两处 | 改用 Python 内建 `min`（入参均为 int，`pypto.min` 返回 SymbolicScalar 反而破坏纯静态表达） |

### 2.4 padding 语义（静默出错风险）

静态化后若调用方为凑等长而 padding，**pad 位的 K/V 会真实参与 softmax、污染 L/M 与输出**。首版草稿不带 mask，契约是"调用方保证 pad 策略正确"。mask 扩展指引起第 8 节。

## 3. 差异二：数据类型（只换类型，不动计算过程）

### 3.1 替换规则

计算过程与算子结构**完全不变**，仅做类型替换：

| 云侧 | 端侧 | 涉及位置（FA 案例） |
|---|---|---|
| BF16 输入 | **FP16** | `q/k/v` 注解及调用方 |
| FP32 中间量 | **FP16** | `scores`（`out_dtype=DT_FP32`→`DT_FP16`）、`scores_scaled/mij/s_shifted/pij/lij`、L/M 累加器 `oi/li/mi_update` |
| BF16 的 `P` | **FP16** | `pypto.cast(pij, DT_BF16)`→`DT_FP16` |
| FP32 的 `l_output/m_output` | **FP16** | 输出注解及调用方 |

调用方负责把权重/激活提前转成 FP16，kernel 内不做 BF16→FP16 转换（端侧无 BF16 支持证据，UT 无 BF16 用例）。

### 3.2 已知精度风险（不设防、如实标注）

- **FP16 累加 L/M 的误差放大**：online softmax 依赖 M 单调不减修正与 L 分段求和，FP16 的 11-bit 尾数在长序列（S 大、kv tile 多）下误差可能放大。迁移不改计算过程，此风险**接受**并在验收时关注（待验证项 V1）；
- **`exp` 行为**：`pypto.exp` 在 FP16 输入下的实现路径（原生 half exp 还是 FP32 计算后回转）待查证（V2）；
- **除法精度**：`pypto.div(..., precision_type=PrecisionType.INTRINSIC)` 在 FP16 下的行为待查证（V3）。

## 4. 差异三：函数签名（最大程度保留）

### 4.1 `cu_seqlens` 保留在签名中

签名与云侧**一字不差**地保留 `cu_seqlens_q/cu_seqlens_k` 参数，调用方代码云/端两侧一致（端侧照常传 `[0, S_max]` 的 `[2]` 张量）。这样：

- host/model 侧调用代码零改动；
- `batch_size = cu_seqlens_q.shape[0] - 1` 原样保留（shape 读取是静态的）；
- 真正改的只有 kernel 内部三处**值读取**（见 2.3 表）。

### 4.2 端侧入参语义（由调用方体现，不改签名结构）

| 参数 | 端侧语义 |
|---|---|
| `q/k/v` | `[1, S_max, N, D]`（或 `[S_max, N, D]`，取决于原签名维数），静态，FP16 |
| `output` | `[1, S_max, N*D]` |
| `l_output/m_output` | `[1, S_max, N]`，FP16 |
| `cu_seqlens_q/k` | `[2]`，值必须为 `[0, S_max]`，**值不参与计算** |

## 5. 差异四：buffer / tile（用户自调，只给事实）

云侧 kernel 里的 tile 全家桶在 128 KB UB / 单核 / cube 16/8/16 粒度下基本不可行，**需用户自行调整**。本指南不提供推导方法，只列差异事实与触点清单：

**触点盘点（FA 案例共 10+ 处）**

- 模块常量：`Q_TILE=320`、`K_TILE=320`
- 全局设置：`set_cube_tile_shapes([128,128],[128,256],[128,128])`、`set_vec_tile_shapes(64,256)`
- 循环内重设：QK^T 的 `[64,512]/[64,64]/[512,512]`、PV 的 `[128,512]/[256,512]/[64,64]`、`v1_tile=[64,512]`/`v2_tile=[512,64]` 及 6 处应用点
- `pass_options` 的 `cube_l1_reuse_setting/vec_nbuffer_setting/cube_nbuffer_setting = {-1: 8}`——nbuffer=8 在 128 KB UB 上大概率不可行（此项属"必须改"，见 6.2）

**调优建议（非推导）**：保守小 tile 起步（如 Q_TILE=K_TILE=64，vec tile 不超过 64/128），按 `cube_m/n/k_size=16/8/16` 的整数倍与 `ubblock_size=32` 对齐约束迭代；若 tile 已压到很小 UB 仍不足，考虑结构退守（5.1）。

**结构退守项（联动调优）**：双头打包（`h_num = num_heads // 2` + 两套 `oi/li/mi_update` 累加器）在 UB 同时驻留两套 FP32 累加器；dtype 降 FP16 后体积减半，剩余压力若仍装不下，可退守为单 head 循环（`h_num = num_heads`，删 `h_s_idx` 内层与一套累加器），代价是 K/V 每个 head 多搬一次。首版草稿保留双头结构。

### 5.1 工作量评估

| 阶段 | 估计 | 说明 |
|---|---|---|
| 拿到能跑的配置 | 1–2 人日 | 改参数→编译→看 UB 溢出/对齐报错→再改；坑集中在 `ubblock_size=32` 对齐与 16/8/16 粒度整数倍 |
| 性能调优 | 1–2 人周 | 单核小 buffer 下 tile 对性能敏感度高，需扫配置；**强依赖执行/仿真环境反馈** |
| 后续算子边际递减 | 0.5–1 人日/算子 | 触点认知可复用 |

前提：SIM 仿真可用（仓库已有 Kirin9030 仿真平台配置与 `RunMode.SIM` UT 惯例，此假设大概率成立但未实测）。

## 6. 差异五：jit 装饰参数（全删，只留 soc_version）

### 6.1 改法

云侧装饰器：
- 删 `debug_options={"runtime_debug_mode": 0}`
- 删 `runtime_options={"device_sched_mode": 0, "stitch_function_max_num": 1024}`
- 删 `pass_options={...nbuffer 系列...}`
- 只留 `codegen_options={"soc_version": "Kirin9030"}`，按 UT 惯例加 `runtime_options={"run_mode": pypto.RunMode.SIM}`（真机模式待环境就绪后确认）

### 6.2 逐项理由 / 待查证

| 选项 | 处置 | 理由 |
|---|--- |---|
| `device_sched_mode` | 删 | 设备侧调度开关，端侧单核无设备侧调度需求（待查证 litenpu 是否识别该 key，V4） |
| `stitch_function_max_num` | 删 | 多核切分相关，单核不适用（V4） |
| `cube_l1_reuse_setting/vec_nbuffer_setting/cube_nbuffer_setting` | 删 | nbuffer=8 与云侧大 buffer 耦合，128 KB UB 先验不可行；端侧用默认值（V4） |
| `runtime_debug_mode` | 删 | 调试开关，非功能必需 |

## 7. 待验证清单

| ID | 项 | 验证方法（环境就绪后） |
|---|---|---|
| V1 | FP16 累加 L/M 精度是否可接受 | SIM/真机跑 FA，与 FP64 参考（numpy/torch CPU）比对 cosine/相对误差，扫 S_max 与 kv tile 数 |
| V2 | `pypto.exp` FP16 路径 | 查 litenpu codegen 的 exp 实现（`framework/src/codegen/npu/litenpu/`）或 SIM 单测 |
| V3 | `pypto.div` INTRINSIC 精度模式 FP16 行为 | 同上 |
| V4 | jit 选项在 litenpu 的支持矩阵 | 删干净后在 SIM 下编译，观察是否有 unknown key 报错（反向验证"不支持"） |
| V5 | `[pypto.STATIC]` 注解驱动 batch loop 在 pypto 前端的行为 | pypto_pro 的 shape policy 已有 UT 覆盖；pypto 前端用小用例验证 STATIC 烘入 |
| V5b | 原生 `range` 循环 + 整数比较分支的等价性 | SIM 下验证三分支（首/中/末 kv tile）与云侧 `is_loop_begin/end` 版本逻辑一致 |
| V5c | `pypto.platform.npuarch` 在 trace 期的取值行为 | 决定惰性平台分支是"恒假安全"还是"求值出错"，验证移除决策的必要性 |
| V6 | `assemble` 动态偏移替换后的写回正确性 | SIM 下比对输出张量 |
| V7 | SIM 全链路 | 端侧草稿按 UT 惯例接 `RunMode.SIM` 跑通 |

## 8. 附录：mask 扩展指引（可选，非迁移必需）

若需支持 padded 场景，方向如下（参考云侧 `python/tests/st/pypto_pro/frontend/fa/test_fa_with_mask.py` 的既有模式）：

1. 新增静态 mask 入参（如 `[1, S_max, S_max]` 或可广播形状），pad 位为 -inf/极小值；
2. 在 `scores_scaled` 计算后、`mij = amax` 前叠加：`scores_masked = scores_scaled + mask`（或 `where(mask_valid, scores_scaled, -inf)`）；
3. 全程**数据流**实现（加法/select），不引入控制流，不违反静态约束；
4. mask 的 shape 语义与双头打包的交互（mask 不依赖 head 维，广播即可）。

## 9. 端侧代码草稿位置

`python/tests/ut/kirin/flash_attention_fa/flash_attention_fa_edge_draft.py`——单文件 kernel，静态契约、FP16 化、保守 tile 初值，头部标注全部待验证项。与指南的差异对照逐处映射（正文各节"FA 案例逐处对照"表）。
