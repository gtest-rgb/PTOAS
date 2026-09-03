#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cloud vs edge comparison PPT: writing style, constraints, migration paths,
user-awareness tiers. Based on docs_cloud_to_edge_migration_guide.md."""
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE

INK = RGBColor(0x1F, 0x29, 0x37)
GRAY = RGBColor(0x6B, 0x72, 0x80)
BLUE = RGBColor(0x1D, 0x4E, 0xD8)
LIGHT = RGBColor(0xEF, 0xF3, 0xFF)
LINE = RGBColor(0xD7, 0xDC, 0xE3)
BG = RGBColor(0xFF, 0xFF, 0xFF)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
CLOUD = RGBColor(0x1D, 0x4E, 0xD8)          # cloud-side band
EDGE = RGBColor(0x47, 0x50, 0x5C)           # edge-side band
GREEN = RGBColor(0x1A, 0x7F, 0x37)          # zero awareness
AMBER = RGBColor(0xB0, 0x6A, 0x00)          # semi awareness
RED = RGBColor(0xC0, 0x2E, 0x2E)            # explicit change
GREEN_BG = RGBColor(0xE4, 0xF3, 0xE8)
AMBER_BG = RGBColor(0xFB, 0xF0, 0xDD)
RED_BG = RGBColor(0xFB, 0xE8, 0xE8)

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
BLANK = prs.slide_layouts[6]
SW, SH = prs.slide_width, prs.slide_height
MARGIN = Inches(0.55)
CW = Inches(12.23)  # content width


def add_slide():
    return prs.slides.add_slide(BLANK)


def rect(slide, x, y, w, h, fill, line_color=None):
    sp = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, y, w, h)
    sp.fill.solid()
    sp.fill.fore_color.rgb = fill
    if line_color is None:
        sp.line.fill.background()
    else:
        sp.line.color.rgb = line_color
        sp.line.width = Pt(0.75)
    sp.shadow.inherit = False
    return sp


def text(slide, x, y, w, h, runs, size=16, color=INK, bold=False,
         align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, spacing=1.0):
    tb = slide.shapes.add_textbox(x, y, w, h)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    if isinstance(runs, str):
        runs = [[(runs, {})]]
    elif runs and isinstance(runs[0], tuple):
        runs = [runs]
    first = True
    for para in runs:
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        p.alignment = align
        p.line_spacing = spacing
        if isinstance(para, str):
            para = [(para, {})]
        for txt, opts in para:
            r = p.add_run()
            r.text = txt
            r.font.size = Pt(opts.get("size", size))
            r.font.color.rgb = opts.get("color", color)
            r.font.bold = opts.get("bold", bold)
            r.font.name = "PingFang SC"
    return tb


def header(slide, title, kicker=None):
    rect(slide, 0, 0, SW, Inches(0.06), BLUE)
    text(slide, MARGIN, Inches(0.28), Inches(12.0), Inches(0.6), title,
         size=25, bold=True)
    if kicker:
        text(slide, MARGIN, Inches(0.88), Inches(12.2), Inches(0.35), kicker,
             size=13, color=GRAY)
    return Inches(1.32)


def table(slide, x, y, w, rows, col_widths=None, font=11, header_font=11,
          row_h=Inches(0.4), cell_colors=None):
    """cell_colors: dict {(ri,ci): color} to color specific cell text."""
    nrows, ncols = len(rows), len(rows[0])
    gt = slide.shapes.add_table(nrows, ncols, x, y, w, row_h * nrows).table
    if col_widths:
        total = sum(col_widths)
        for i, cw in enumerate(col_widths):
            gt.columns[i].width = Emu(int(w * cw / total))
    for ri, row in enumerate(rows):
        gt.rows[ri].height = row_h
        for ci, val in enumerate(row):
            cell = gt.cell(ri, ci)
            cell.margin_left = Inches(0.06)
            cell.margin_right = Inches(0.04)
            cell.margin_top = Inches(0.02)
            cell.margin_bottom = Inches(0.02)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            tf = cell.text_frame
            tf.word_wrap = True
            for pi, line in enumerate(str(val).split("\n")):
                p = tf.paragraphs[0] if pi == 0 else tf.add_paragraph()
                p.line_spacing = 1.0
                r = p.add_run()
                r.text = line
                r.font.name = "PingFang SC"
                if ri == 0:
                    cell.fill.solid()
                    cell.fill.fore_color.rgb = BLUE
                    r.font.color.rgb = WHITE
                    r.font.bold = True
                    r.font.size = Pt(header_font)
                else:
                    cell.fill.solid()
                    cell.fill.fore_color.rgb = BG if ri % 2 else LIGHT
                    r.font.color.rgb = (cell_colors or {}).get((ri, ci), INK)
                    r.font.size = Pt(font)
    return gt


def tier_chip(slide, x, y, label, tier):
    """Small colored awareness chip. tier: zero|semi|explicit"""
    m = {"zero": (GREEN, GREEN_BG), "semi": (AMBER, AMBER_BG),
         "explicit": (RED, RED_BG)}
    fg, bgc = m[tier]
    rect(slide, x, y, Inches(1.35), Inches(0.34), bgc, fg)
    text(slide, x, y + Inches(0.045), Inches(1.35), Inches(0.26), label,
         size=11.5, bold=True, color=fg, align=PP_ALIGN.CENTER)


# ================================================================ P1 cover
s = add_slide()
rect(s, 0, 0, SW, SH, BG)
rect(s, 0, Inches(4.0), SW, Inches(0.045), BLUE)
rect(s, MARGIN, Inches(2.25), Inches(0.75), Inches(0.09), BLUE)
text(s, MARGIN, Inches(2.5), Inches(12.2), Inches(0.9),
     "云侧 vs 端侧:写法、约束与免改路径", size=36, bold=True)
text(s, MARGIN, Inches(3.4), Inches(12.2), Inches(0.5),
     "每个维度:云侧写法与目的 → 端侧约束与期望结果 → 达成路径 → 用户感知档位",
     size=16, color=GRAY)
text(s, MARGIN, Inches(4.3), Inches(12.2), Inches(0.4),
     "汇报人:____________        日期:2026-09-03", size=14, color=GRAY)
# legend preview on cover bottom
lx = MARGIN
for label, tier, desc in [("零感知", "zero", "不改代码、不开选项,照跑"),
                          ("半感知", "semi", "不改代码,但需开选项/遵守契约"),
                          ("显式改造", "explicit", "必须改代码或调用行为")]:
    tier_chip(s, lx, Inches(5.3), label, tier)
    text(s, lx + Inches(1.5), Inches(5.34), Inches(2.6), Inches(0.3), desc,
         size=12, color=GRAY)
    lx += Inches(4.1)

# ================================================================ P2 matrix
s = add_slide()
y = header(s, "全景对比矩阵", "六维度一览;感知三档图例见下,全程沿用")
lx = MARGIN
for label, tier, desc in [("零感知", "zero", "不改代码、不开选项"),
                          ("半感知", "semi", "不改代码,需开选项/遵守契约"),
                          ("显式改造", "explicit", "必须改代码或调用行为")]:
    tier_chip(s, lx, y, label, tier)
    text(s, lx + Inches(1.45), y + Inches(0.04), Inches(2.5), Inches(0.3),
         desc, size=11, color=GRAY)
    lx += Inches(4.1)
rows = [
    ["维度", "云侧写法与目的", "端侧约束", "期望结果", "达成路径", "感知档位"],
    ["循环控制流\npypto.loop",
     "动态边界驱动,一份 kernel 跑任意序列长度(varlen 多 batch 必需)",
     "循环边界/分支条件必须编译期确定",
     "循环静态展开",
     "2.5 透明降级 / 手改 range+整数比较",
     "半感知 / 显式"],
    ["值读取\ncu_seqlens",
     "tensor 值→符号表达式,运行期求值,host 免传参",
     "值不参与计算,须为 [0, S_max]",
     "静态推导 q.shape[1]",
     "2.6 值烘焙 / 手改+注解 [STATIC]",
     "半感知 / 显式"],
    ["dtype",
     "BF16 输入 + FP32 中间量,求精度",
     "无 BF16 支持证据(UT 无用例)",
     "计算不动,仅类型替换 FP16",
     "用户替换(V1–V3 精度风险)",
     "显式"],
    ["签名语义",
     "varlen 拼包,cu_seqlens 驱动计算",
     "单 batch,S=S_max,padding 调用方负责",
     "签名一字不差,契约遵守",
     "遵守调用契约 / mask 扩展(可后置)",
     "半感知 / 显式"],
    ["tile / buffer",
     "大 UB/多核的 tile 全家桶榨性能",
     "128KB UB、单核、cube 16/8/16",
     "保守小 tile+对齐约束",
     "用户自调(1–2 人日跑通,1–2 人周调优)",
     "显式"],
    ["jit 参数",
     "设备调度/多核切分/调试选项",
     "单核无设备侧调度需求",
     "只留 soc_version",
     "删选项(SIM 惯例 +RunMode.SIM)(V4)",
     "显式"],
]
tier_col = {("半感知 / 显式", "semi"): None}  # marker only
colors = {}
for ri in range(1, 7):
    colors[(ri, 5)] = AMBER if "半感知" in rows[ri][5] and "显式" not in rows[ri][5] \
        else (RED if "显式" == rows[ri][5] else INK)
table(s, MARGIN, Inches(1.95), CW, rows,
      col_widths=[1.15, 2.6, 2.1, 1.7, 2.6, 1.0], font=10, header_font=10.5,
      row_h=Inches(0.68), cell_colors=colors)
text(s, MARGIN, Inches(6.9), CW, Inches(0.35),
     "注:感知档位首项为框架方案落地后的最优档;未落地时均为显式改造。",
     size=11, color=GRAY)

# ====================================================== dimension page helper
def dim_page(no, title, kicker, cloud_pts, edge_cons, edge_expect, paths,
             tiers, footnote=None):
    """Fixed four-band layout per dimension page."""
    s = add_slide()
    y = header(s, f"{no}  {title}", kicker)
    # band 1: cloud (left) vs edge constraint (right)
    ch = Inches(1.95)
    rect(s, MARGIN, y, Inches(6.0), ch, LIGHT, LINE)
    rect(s, MARGIN, y, Inches(6.0), Inches(0.36), CLOUD)
    text(s, MARGIN + Inches(0.15), y + Inches(0.045), Inches(5.6),
         Inches(0.28), "云侧写法与目的", size=13, bold=True, color=WHITE)
    paras = [[("▪ ", {"color": BLUE, "bold": True, "size": 12}),
              (t, {"size": 12})] for t in cloud_pts]
    # interleave small gaps
    full = []
    for i, p in enumerate(paras):
        full.append(p)
        if i < len(paras) - 1:
            full.append([(" ", {"size": 4})])
    text(s, MARGIN + Inches(0.15), y + Inches(0.48), Inches(5.7),
         ch - Inches(0.55), full, spacing=1.05)

    rect(s, Inches(6.78), y, Inches(6.0), ch, BG, LINE)
    rect(s, Inches(6.78), y, Inches(6.0), Inches(0.36), EDGE)
    text(s, Inches(6.93), y + Inches(0.045), Inches(5.6), Inches(0.28),
         "端侧约束", size=13, bold=True, color=WHITE)
    paras = [[("▪ ", {"color": EDGE, "bold": True, "size": 12}),
              (t, {"size": 12})] for t in edge_cons]
    full = []
    for i, p in enumerate(paras):
        full.append(p)
        if i < len(paras) - 1:
            full.append([(" ", {"size": 4})])
    text(s, Inches(6.93), y + Inches(0.48), Inches(5.7), ch - Inches(0.55),
         full, spacing=1.05)

    # band 2: expected result strip
    ry = y + ch + Inches(0.12)
    rect(s, MARGIN, ry, CW, Inches(0.52), LIGHT, BLUE)
    text(s, MARGIN + Inches(0.15), ry + Inches(0.09), Inches(1.6), Inches(0.34),
         "期望结果", size=13, bold=True, color=BLUE)
    text(s, MARGIN + Inches(1.75), ry + Inches(0.09), Inches(10.3),
         Inches(0.34), edge_expect, size=12.5)

    # band 3: paths (left wide)
    py = ry + Inches(0.64)
    ph = Inches(2.2)
    rect(s, MARGIN, py, Inches(8.6), ph, BG, LINE)
    text(s, MARGIN + Inches(0.15), py + Inches(0.1), Inches(8.2),
         Inches(0.3), "达成路径", size=13, bold=True, color=BLUE)
    full = []
    for i, (label, desc) in enumerate(paths):
        full.append([(f"{label}", {"bold": True, "size": 12, "color": INK}),
                     (desc, {"size": 11.5, "color": GRAY})])
        if i < len(paths) - 1:
            full.append([(" ", {"size": 5})])
    text(s, MARGIN + Inches(0.15), py + Inches(0.45), Inches(8.3),
         ph - Inches(0.5), full, spacing=1.08)

    # band 4: awareness (right)
    rect(s, Inches(9.4), py, Inches(3.38), ph, BG, LINE)
    text(s, Inches(9.55), py + Inches(0.1), Inches(3.0), Inches(0.3),
         "用户感知档位", size=13, bold=True, color=BLUE)
    ty = py + Inches(0.5)
    for label, desc, tier in tiers:
        tier_chip(s, Inches(9.55), ty, label, tier)
        text(s, Inches(9.55), ty + Inches(0.4), Inches(3.05), Inches(0.62),
             desc, size=10.5, color=GRAY, spacing=1.02)
        ty += Inches(0.83) if len(tiers) > 2 else Inches(1.0)
    if footnote:
        text(s, MARGIN, Inches(7.05), CW, Inches(0.35), footnote, size=11,
             color=GRAY)
    return s


# ================================================================ P3 loop
dim_page(
    "维度一", "循环控制流:pypto.loop → 原生 range",
    "影响最深的差异;也是 2.5 框架方案的发力点",
    ["四层 pypto.loop 以运行期符号量为边界(batch/seq/tile 循环)",
     "is_loop_begin/end 识别循环首末 tile;pypto.min 处理边界截断",
     "目的:一份编译产物跑任意序列长度,云侧多 batch varlen 的设计特性"],
    ["循环边界与分支条件必须编译期确定",
     "静态 shape 下 q.shape[0]、tile_count 均为普通 Python int",
     "is_loop_end 要求 pypto.loop 产出的 SymbolicScalar,原生循环下不可用"],
    "循环静态展开:range + 整数比较(k_tile_idx == 0 / == count-1)替代符号判定",
    [("框架方案 2.5 透明降级:", " _LoopInt 属性桥自动映射 loop / is_loop_begin/end / "
      "min 三类机械改写;云/端共用同一份源码;动态边界 fail fast 报错"),
     ("用户手改:", " 四层循环改原生 range,is_loop_begin/end 改整数比较,"
      "pypto.min 改 Python min")],
    [("半感知", "2.5 落地后:不改代码,但需显式开 static_loop 选项(不建议 npuarch 嗅探,V5c)", "semi"),
     ("显式改造", "方案未落地时:手改三类循环表达", "explicit")],
    "连带成本:云侧惰性平台分支(npuarch == DAV_3510)依赖 pypto.loop 符号执行的惰性,循环表达迁移时需一并移除(源文档第 0 节例外)。")

# ================================================================ P4 value read
dim_page(
    "维度二", "值读取:cu_seqlens / as_variable → 静态推导",
    "语义静态化的核心;2.6 值烘焙的发力点",
    ["q_start = cu_seqlens_q[b] 走 __getitem__ 标量分支,构造 "
     "RUNTIME_GetTensorDataInt32Dim1 符号调用表达式",
     "as_variable() 显式标记运行期变量;trace 期不读 tensor 数据",
     "目的:kernel 侧自含控制流信息,host 无需传参,varlen 拼包一份产物"],
    ["值不参与计算:cu_seqlens 必须为 [0, S_max] 的 [2] 张量",
     "常见误解——值恰好是常量也不行:\u201c值是常量\u201d≠\u201c编译期常量\u201d,"
     "表达式仍是运行期符号",
     "真正静态的只有 shape 读取与 host 侧 int 传入"],
    "删除值读取,静态推导 seq_len_q = q.shape[1];签名保留 cu_seqlens,调用方零改动",
    [("框架方案 2.6 值烘焙:", " bake_host_values 显式名单,trace 期把 GetTensorData "
      "折叠为常量,后续表达式自然折叠;三个前置条件(host 可得性/变体缓存/DT_INT32)"),
     ("用户手改:", " 删值读取与 as_variable,改静态推导;驱动 batch loop 的维度"
      "注解改 [pypto.STATIC]")],
    [("半感知", "2.6 落地后:不改代码,需开 bake_host_values 且值变触发重编译(V10)", "semi"),
     ("显式改造", "方案未落地时:手改三处值读取 + 注解", "explicit")],
    "2.5 无法覆盖此项:值读取产生的 SymbolicScalar 边界会触发 2.5 降级路径报错(符合预期,替用户发现未静态化代码)。")

# ================================================================ P5 dtype
dim_page(
    "维度三", "dtype:BF16/FP32 → FP16(只换类型,不动计算)",
    "计算过程与算子结构完全不变",
    ["q/k/v 输入 BF16;scores、mij/pij/lij 等中间量 FP32;L/M 累加器 FP32",
     "P 矩阵 cast 为 BF16;l_output/m_output 输出 FP32",
     "目的:大模型训练/推理的数值精度需求,云侧 BF16/FP32 全覆盖"],
    ["端侧无 BF16 支持证据:UT dtype 覆盖 FP16/FP32/INT8-32/UINT8,无 BF16 用例",
     "调用方负责提前转 FP16;kernel 内不做 BF16→FP16 转换"],
    "全部替换为 FP16:输入/中间量/P/输出;结构与计算过程零改动",
    [("用户替换(无框架方案):", " 注解与调用方 dtype 替换——"
      "out_dtype=DT_FP32→DT_FP16、cast(pij, DT_BF16)→DT_FP16 等"),
     ("精度风险(如实标注):", " FP16 11-bit 尾数在长序列下 L/M 累加误差可能放大(V1);"
      "exp 的 FP16 路径待查(V2);div INTRINSIC 待查(V3)")],
    [("显式改造", "dtype 替换必须用户完成,框架不介入", "explicit")],
    None)

# ================================================================ P6 tile + jit
dim_page(
    "维度四", "tile/buffer 与 jit 参数:性能配置整体重置",
    "云侧性能优化项在端侧先验不可行,需全部重调",
    ["tile 全家桶:Q_TILE/K_TILE=320、全局+循环内 6 处 cube/vec tile 重设",
     "pass_options:nbuffer=8 等 buffer 复用策略",
     "jit:device_sched_mode、stitch_function_max_num、runtime_debug_mode 等",
     "目的:云侧大 UB(256KB–1MB)、数十核下榨吞吐与复用"],
    ["128KB UB、单核、cube 16/8/16 粒度、ubblock=32 对齐",
     "nbuffer=8 与云侧大 buffer 耦合,端侧大概率不可行",
     "单核无设备侧调度/多核切分需求(V4)"],
    "保守小 tile 起步(Q_TILE=K_TILE=64)按 16/8/16 整数倍+32 对齐迭代;jit 只留 soc_version(+RunMode.SIM)",
    [("用户自调(无推导公式,只给事实):", " 触点 10+ 处;1–2 人日跑通,"
      "1–2 人周调优,后续算子 0.5–1 人日/个(边际递减);仍不足可退守单 head 循环"),
     ("jit 选项全删:", " device_sched_mode / stitch_function_max_num / "
      "nbuffer 系列 / runtime_debug_mode——处置理由见源文档 6.2")],
    [("显式改造", "tile 与 jit 均需用户重配,框架不介入", "explicit")],
    "前提:SIM 仿真可用(Kirin9030 仿真配置已存在,RunMode.SIM UT 惯例,未实测)。")

# ================================================================ P7 signature
dim_page(
    "维度五", "签名语义:varlen 拼包 → 单 batch padding 契约",
    "签名一字不差保留,变的是语义契约",
    ["签名含 cu_seqlens_q/k,云侧传真实累积长度驱动 varlen 计算",
     "多 batch 拼包:一条 kernel 处理整 batch 变长序列",
     "目的:host/model 侧调度灵活,一份产物服务任意 batch"],
    ["单 batch:一次只处理一条序列(或等长 padding)",
     "S = S_max:静态 shape,调用方自行 padding",
     "cu_seqlens 值不参与计算——传真实累积长度会静默出错(结果错但不报错)"],
    "签名不改、调用代码云/端一致:batch_size = cu_seqlens_q.shape[0]-1 原样保留(shape 读取是静态的)",
    [("遵守契约(半感知):", " 不改代码,但调用方必须知道并遵守 [0, S_max] 契约——"
      "这是知识性约束,不是代码约束"),
     ("mask 扩展(显式,可选):", " 新增静态 mask 入参,scores 计算后数据流叠加"
      "(加法/select,不引控制流);非迁移必需,可后置")],
    [("半感知", "契约遵守:不改代码但必须知道,违反则静默出错", "semi"),
     ("显式改造", "padded 场景需 mask 扩展(可选)", "explicit")],
    "padding 静默出错风险:pad 位 K/V 真实参与 softmax、污染 L/M 与输出——首版不带 mask,靠调用方保证 pad 策略。")

# ================================================================ P8 evolution
s = add_slide()
y = header(s, "感知演进矩阵:框架方案如何压缩用户感知面", "核心结论页")
rows = [
    ["维度", "现状(无方案)", "2.5 落地后", "2.5 + 2.6 齐备"],
    ["循环控制流", "显式(手改 range 等)", "半感知(开 static_loop)", "半感知(开 static_loop)"],
    ["值读取", "显式(手改静态推导)", "显式(2.5 不覆盖,触发报错)", "半感知(开 bake_host_values)"],
    ["dtype", "显式", "显式", "显式"],
    ["签名语义", "半感知(遵守契约)", "半感知(遵守契约)", "半感知(遵守契约)"],
    ["tile / jit", "显式", "显式", "显式"],
]
ev_colors = {}
tier_color = {"显式": RED, "半感知": AMBER}
for ri in range(1, 6):
    for ci in range(1, 4):
        v = rows[ri][ci]
        base = v.split("(")[0]
        ev_colors[(ri, ci)] = tier_color.get(base, INK)
table(s, MARGIN, y, CW, rows, col_widths=[1.8, 3.2, 3.4, 3.6], font=12,
      header_font=12, row_h=Inches(0.52), cell_colors=ev_colors)
rect(s, MARGIN, Inches(4.75), Inches(8.6), Inches(1.55), LIGHT)
text(s, Inches(0.8), Inches(4.9), Inches(8.2), Inches(1.3), [
    [("方案成本:", {"bold": True, "size": 13}),
     ("2.5 约 0.5–1 人日、不碰 C++(_controller.py + pil/ops.py + 开关接线 + UT),"
      "仓库已有 loop_impl 静态双路径与 max_impl 降级先例;", {"size": 12})],
    [("            ", {"size": 6})],
    [("            ", {"size": 6})],
    [("          2.6 暂无估计(前置条件具备仓库依据:ready_on_host_tensors / "
      "shape policy 变体机制 / DT_INT32)。", {"size": 12})],
], spacing=1.1)
text(s, MARGIN, Inches(6.45), CW, Inches(0.7), [
    [("注:", {"bold": True, "size": 12, "color": GRAY}),
     ("2.5/2.6 均需显式选项,无一达到零感知;等价性与展开上限待 V9/V10 验证;"
      "终态下用户仅剩 dtype / tile / jit 三类显式改动(非控制流)。",
      {"size": 12, "color": GRAY})],
], spacing=1.1)

# ================================================================ P9 end
s = add_slide()
rect(s, 0, 0, SW, SH, BG)
rect(s, 0, Inches(3.4), SW, Inches(0.045), BLUE)
text(s, 0, Inches(3.7), SW, Inches(0.8), "谢谢,请评审指正",
     size=34, bold=True, align=PP_ALIGN.CENTER)
text(s, 0, Inches(4.7), SW, Inches(0.5),
     "云侧 vs 端侧对比专题 · 2026-09-03", size=14, color=GRAY,
     align=PP_ALIGN.CENTER)
text(s, 0, Inches(5.4), SW, Inches(0.5),
     "决策点:立项 2.5 + 2.6,即可将用户感知面压缩至 dtype / tile / jit 三类",
     size=15, color=BLUE, align=PP_ALIGN.CENTER)

out = "docs_cloud_vs_edge_comparison.pptx"
prs.save(out)
print("saved:", out, "slides:", len(prs.slides._sldIdLst))
