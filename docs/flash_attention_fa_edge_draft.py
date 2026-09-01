#!/usr/bin/env python3
# coding: utf-8
# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# This program is free software, you can redistribute it and/or modify it under the terms and conditions of
# CANN Open Software License Agreement Version 2.0 (the "License").
# Please refer to the License for details. You may not use this file except in compliance with the License.
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
# INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY OR FITNESS FOR A PARTICULAR PURPOSE.
# See LICENSE in the root of the software repository for the full text of the License.
# -----------------------------------------------------------------------------------------------------------
"""
Flash Attention Forward - 云侧 varlen kernel 的端侧（Kirin9030）迁移草稿

!!! 未验证草稿 !!! 本文件由云侧实现
python/tests/st/operator/flash_attention_mha/flash_attention_mha_impl.py 按迁移指南
docs_cloud_to_edge_migration_guide.md 修改而来，未经真机/SIM 验证。

迁移改动清单（与指南章节对应，标注 [TODO(edge)] 的项需用户自行调整/验证）：
  1. jit 装饰参数：删云侧调度/nbuffer 选项，只留 soc_version（指南 §6）；SIM 模式沿用
     python/tests/ut/kirin UT 惯例 [TODO(edge)] 真机就绪后确认 run_mode（V7）
  2. dtype：只换类型不动计算过程 —— BF16→FP16、FP32→FP16（指南 §3）；
     FP16 累加 L/M 的精度风险已知并接受（V1）
  3. 静态 shape：cu_seqlens 保留在签名中、shape 读取保留，仅删除三处值读取与
     as_variable()，改静态推导（指南 §2/§4）；端侧单 batch、S=S_max、调用方自行
     padding，cu_seqlens 值必须为 [0, S_max] 且不参与计算（值参与计算会静默出错）
  4. 循环表达：静态场景不支持 pypto.loop，四层循环全部改为原生 range；
     is_loop_begin/is_loop_end 改为整数比较（k_tile_idx == 0 / == k_tile_count-1），
     分支在 trace 期求解；pypto.min 两处改用 Python 内建 min（入参均为 int）
  5. tile 参数：全部替换为保守初值，[TODO(edge)] 用户自调（指南 §5）；
     约束：cube 16/8/16 整数倍、ubblock 32 对齐、UB 128KB
  6. 保留不改：双头打包结构、combine_axis 实验选项（V8）、online softmax 计算流程；
     DAV_3510 平台分支随 pypto.loop → range 迁移一并移除（其 set_pass_options 调用
     位于循环体内，在原生 Python 控制流下会被无条件 trace，不再惰性）
"""

import pypto

# [TODO(edge)] 云侧为 320；端侧 128KB UB 下的保守初值，用户自调（指南 §5）
Q_TILE = 64
K_TILE = 64


@pypto.frontend.jit(
    codegen_options={"soc_version": "Kirin9030"},
    runtime_options={"run_mode": pypto.RunMode.SIM},
)
def flash_attention_varlen_forward_kernel(
    # Q侧输入: shape=[S_max, N, D], 端侧单 batch, dim0 静态烘入驱动循环
    q: pypto.Tensor([pypto.STATIC, ...], pypto.DT_FP16),
    # KV侧输入: shape=[S_max, N, D]
    k: pypto.Tensor([pypto.STATIC, ...], pypto.DT_FP16),
    v: pypto.Tensor([pypto.STATIC, ...], pypto.DT_FP16),
    # Q侧输出: shape=[S_max, hidden_dim] (二维)
    output: pypto.Tensor([pypto.STATIC, pypto.STATIC], pypto.DT_FP16),
    # Q侧: softmax中间量L, shape=[S_max, n] (二维)
    l_output: pypto.Tensor([pypto.STATIC, pypto.STATIC], pypto.DT_FP16),
    # Q侧: softmax中间量M, shape=[S_max, n] (二维)
    m_output: pypto.Tensor([pypto.STATIC, pypto.STATIC], pypto.DT_FP16),
    # 累积序列长度: shape=[2], 端侧必须为 [0, S_max]; 值不参与计算（签名兼容保留）
    cu_seqlens_q: pypto.Tensor([pypto.STATIC], pypto.DT_INT32),
    cu_seqlens_k: pypto.Tensor([pypto.STATIC], pypto.DT_INT32),
):
    """
    Flash Attention Forward - 4 loops (batch + head + q_tile + kv_tile).

    端侧语义（与云侧的差异）:
      - 单 batch: batch_size 由 cu_seqlens_q.shape[0]-1 静态推导（=1），
        cu_seqlens 的值不读取；q_start/k_start 恒为 0，seq_len 即 shape 静态值
      - dtype: 全链路 FP16（云侧为 BF16 输入 + FP32 中间量）
      - padding: 调用方保证 pad 策略（pad 位 K/V 会真实参与 softmax，指南 §8）

    计算流程 (per batch, per head, per q_tile, per kv_tile) 与云侧一致:
        S_tile = Q_tile @ K_tile^T * scale         [sq, sk]       FP16
        M_tile = max(S_tile, dim=-1)               [sq, 1]        FP16
        P_tile = exp(S_tile - M_tile)              [sq, sk]       FP16
        L_tile = sum(P_tile, dim=-1)               [sq, 1]        FP16
        P_norm = P_tile / L_tile                   [sq, sk]       FP16
        O_tile = P_norm @ V_tile                   [sq, D]        FP16
      O/L/M 在 kv_tile 循环中累加 (online softmax), 最终写回。
    """
    # ---- 从三维输入获取 N(num_heads) 和 D(head_dim), 然后 reshape 为二维 ----
    num_heads = q.shape[1]
    head_dim = q.shape[2]
    hidden_dim = num_heads * head_dim
    total_q = q.shape[0]
    total_kv = k.shape[0]
    scale = 1.0 / (head_dim**0.5)

    # reshape inplace: q/k/v [S_max, N, D] → [S_max, N*D]
    q_2d = pypto.reshape(q, [total_q, hidden_dim], inplace=True)
    k_2d = pypto.reshape(k, [total_kv, hidden_dim], inplace=True)
    v_2d = pypto.reshape(v, [total_kv, hidden_dim], inplace=True)
    # output/l/m 保持二维，无需 reshape

    # [TODO(edge)] 云侧 v1_tile=[64,512]/v2_tile=[512,64]；端侧保守初值，用户自调
    v1_tile = [64, 128]
    v2_tile = [128, 64]

    q_tile = Q_TILE
    k_tile = K_TILE

    pypto.experimental.set_operation_options(combine_axis=True)  # V8: litenpu 支持性待验证
    # [TODO(edge)] 云侧 [128,128]/[128,256]/[128,128]；端侧按 cube 16/8/16 粒度取保守值
    pypto.set_cube_tile_shapes([16, 16], [16, 16], [16, 16])
    pypto.set_vec_tile_shapes(64, 128)

    # 累计Q序列长度 batch_size + 1（shape 读取保留：端侧单 batch 传入 [2] 张量 → 1）
    batch_size = cu_seqlens_q.shape[0] - 1
    # 端侧: 循环边界全部编译期已知，pypto.loop 改为原生 range（静态场景不支持 pypto.loop）；
    # is_loop_begin/is_loop_end 相应改为整数比较，分支在 trace 期求解
    for b_idx in range(batch_size):
        # 端侧: 删除 cu_seqlens 值读取与 as_variable()，改静态推导（b_idx 恒为 0）
        q_start = 0
        seq_len_q = total_q

        k_start = 0
        seq_len_k = total_kv

        q_tile_count = (seq_len_q + q_tile - 1) // q_tile
        k_tile_count = (seq_len_k + k_tile - 1) // k_tile

        h_num = num_heads // 2
        for h_idx in range(h_num):
            for q_tile_idx in range(q_tile_count):
                # 创建2个head独立的累加器（双头打包保留；UB 不足时退守单 head，指南 §5）
                oi_update_0 = pypto.tensor([q_tile, head_dim], pypto.DT_FP16, "oi_update")
                li_update_0 = pypto.tensor([q_tile, 1], pypto.DT_FP16, "li_update")
                mi_update_0 = pypto.tensor([q_tile, 1], pypto.DT_FP16, "mi_update")
                oi_update_1 = pypto.tensor([q_tile, head_dim], pypto.DT_FP16, "oi_update")
                li_update_1 = pypto.tensor([q_tile, 1], pypto.DT_FP16, "li_update")
                mi_update_1 = pypto.tensor([q_tile, 1], pypto.DT_FP16, "mi_update")

                q_tile_start = q_tile_idx * q_tile
                q_tile_end = min(q_tile_start + q_tile, seq_len_q)
                q_tile_len = q_tile_end - q_tile_start

                for k_tile_idx in range(k_tile_count):
                    k_tile_start = k_tile_idx * k_tile
                    k_tile_end = min(k_tile_start + k_tile, seq_len_k)
                    k_tile_len = k_tile_end - k_tile_start

                    for h_s_idx in range(2):
                        h_act_idx = h_idx * 2 + h_s_idx
                        h_offset = h_act_idx * head_dim

                        if h_s_idx == 0:
                            li_update = li_update_0
                            mi_update = mi_update_0
                            oi_update = oi_update_0
                        else:
                            li_update = li_update_1
                            mi_update = mi_update_1
                            oi_update = oi_update_1

                        q_tile_view = pypto.view(
                            q_2d,
                            [q_tile, head_dim],
                            [q_start + q_tile_start, h_offset],
                            valid_shape=[q_tile_len, head_dim],
                        )

                        k_tile_view = pypto.view(
                            k_2d,
                            [k_tile, head_dim],
                            [k_start + k_tile_start, h_offset],
                            valid_shape=[k_tile_len, head_dim],
                        )
                        v_tile_view = pypto.view(
                            v_2d,
                            [k_tile, head_dim],
                            [k_start + k_tile_start, h_offset],
                            valid_shape=[k_tile_len, head_dim],
                        )

                        # [TODO(edge)] 云侧 [64,512]/[64,64]/[512,512]；端侧保守值
                        pypto.set_cube_tile_shapes([16, 16], [16, 16], [16, 16])

                        pypto.set_vec_tile_shapes(v1_tile[0], v1_tile[1])

                        scores = pypto.matmul(q_tile_view, k_tile_view, out_dtype=pypto.DT_FP16, b_trans=True)

                        scores_scaled = pypto.mul(scores, scale)
                        mij = pypto.amax(scores_scaled, dim=-1, keepdim=True)
                        s_shifted = pypto.sub(scores_scaled, mij)
                        pij = pypto.exp(s_shifted)
                        lij = pypto.sum(pij, dim=-1, keepdim=True)

                        # [TODO(edge)] 云侧 [128,512]/[256,512]/[64,64]；端侧保守值
                        pypto.set_cube_tile_shapes([16, 16], [16, 16], [16, 16])

                        # 原生 range 循环下 k_tile_idx 为 Python int，is_loop_begin/end 改整数比较
                        if k_tile_idx == 0:
                            if k_tile_idx == k_tile_count - 1:
                                pypto.set_vec_tile_shapes(v1_tile[0], v1_tile[1])
                                pij_div = pypto.div(pij, lij, precision_type=pypto.PrecisionType.INTRINSIC)
                                pij_fp16 = pypto.cast(pij_div, pypto.DT_FP16)

                                oij = pypto.matmul(pij_fp16, v_tile_view, out_dtype=pypto.DT_FP16)

                                pypto.assemble(lij, [q_start + q_tile_start, h_act_idx], l_output)
                                pypto.assemble(mij, [q_start + q_tile_start, h_act_idx], m_output)
                                pypto.assemble(oij, [q_start + q_tile_start, h_offset], output)

                            else:
                                pypto.set_vec_tile_shapes(v1_tile[0], v1_tile[1])
                                pij_fp16 = pypto.cast(pij, pypto.DT_FP16)

                                oij = pypto.matmul(pij_fp16, v_tile_view, out_dtype=pypto.DT_FP16)

                                oi_update[:] = oij
                                li_update[:] = lij
                                mi_update[:] = mij
                        else:
                            pypto.set_vec_tile_shapes(v1_tile[0], v1_tile[1])
                            pij_fp16 = pypto.cast(pij, pypto.DT_FP16)

                            oij = pypto.matmul(pij_fp16, v_tile_view, out_dtype=pypto.DT_FP16)

                            pypto.set_vec_tile_shapes(v2_tile[0], v2_tile[1])

                            li = pypto.view(li_update, [q_tile, 1], [0, 0], valid_shape=[q_tile_len, 1])
                            mi = pypto.view(mi_update, [q_tile, 1], [0, 0], valid_shape=[q_tile_len, 1])
                            oi = pypto.view(oi_update, [q_tile, head_dim], [0, 0], valid_shape=[q_tile_len, head_dim])

                            mi_new = pypto.maximum(mi, mij)
                            t1 = pypto.sub(mi, mi_new)
                            t2 = pypto.exp(t1)
                            t3 = pypto.sub(mij, mi_new)
                            t4 = pypto.exp(t3)

                            li_new = pypto.add(pypto.mul(t2, li), pypto.mul(t4, lij))
                            oi_tmp = pypto.add(pypto.mul(oi, t2), pypto.mul(oij, t4))

                            if k_tile_idx == k_tile_count - 1:
                                out_fp16 = pypto.div(oi_tmp, li_new, precision_type=pypto.PrecisionType.INTRINSIC)
                                out_res = pypto.cast(out_fp16, pypto.DT_FP16)

                                pypto.assemble(li_new, [q_start + q_tile_start, h_act_idx], l_output)
                                pypto.assemble(mi_new, [q_start + q_tile_start, h_act_idx], m_output)
                                pypto.assemble(out_res, [q_start + q_tile_start, h_offset], output)
                            else:
                                oi_update[:] = oi_tmp
                                li_update[:] = li_new
                                mi_update[:] = mi_new
