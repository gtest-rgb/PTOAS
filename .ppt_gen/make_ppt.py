#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate review PPT for cloud-to-edge migration guide (Kirin 9030)."""
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR

# Colors - clean business white theme
INK = RGBColor(0x1F, 0x29, 0x37)       # near-black text
GRAY = RGBColor(0x6B, 0x72, 0x80)      # secondary text
BLUE = RGBColor(0x1D, 0x4E, 0xD8)      # accent blue
LIGHT = RGBColor(0xEF, 0xF3, 0xFF)     # light blue fill
LINE = RGBColor(0xD7, 0xDC, 0xE3)      # table border
WARN = RGBColor(0xB4, 0x53, 0x00)      # warn orange-red
BG = RGBColor(0xFF, 0xFF, 0xFF)

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
BLANK = prs.slide_layouts[6]

SW = prs.slide_width
SH = prs.slide_height
MARGIN = Inches(0.55)


def add_slide():
    return prs.slides.add_slide(BLANK)


def rect(slide, x, y, w, h, fill, line_color=None):
    from pptx.enum.shapes import MSO_SHAPE
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
         align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, spacing=1.0, wrap=True):
    """runs: str or list of paragraphs; each paragraph is str or list of (txt,opts)."""
    tb = slide.shapes.add_textbox(x, y, w, h)
    tf = tb.text_frame
    tf.word_wrap = wrap
    tf.vertical_anchor = anchor
    if isinstance(runs, str):
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
    # top accent bar
    rect(slide, 0, 0, SW, Inches(0.06), BLUE)
    text(slide, MARGIN, Inches(0.28), Inches(11.0), Inches(0.6), title,
         size=26, bold=True, color=INK)
    if kicker:
        text(slide, MARGIN, Inches(0.88), Inches(11.5), Inches(0.35), kicker,
             size=13, color=GRAY)
    return Inches(1.35)  # content top


def table(slide, x, y, w, rows, col_widths=None, header_fill=BLUE,
          font=12, header_font=12, row_h=Inches(0.34)):
    nrows = len(rows)
    ncols = len(rows[0])
    gt = slide.shapes.add_table(nrows, ncols, x, y, w, row_h * nrows).table
    if col_widths:
        total = sum(col_widths)
        for i, cw in enumerate(col_widths):
            gt.columns[i].width = Emu(int(w * cw / total))
    for ri, row in enumerate(rows):
        gt.rows[ri].height = row_h
        for ci, val in enumerate(row):
            cell = gt.cell(ri, ci)
            cell.margin_left = Inches(0.07)
            cell.margin_right = Inches(0.05)
            cell.margin_top = Inches(0.02)
            cell.margin_bottom = Inches(0.02)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            tf = cell.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            r = p.add_run()
            r.text = str(val)
            r.font.name = "PingFang SC"
            if ri == 0:
                cell.fill.solid()
                cell.fill.fore_color.rgb = header_fill
                r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
                r.font.bold = True
                r.font.size = Pt(header_font)
            else:
                cell.fill.solid()
                cell.fill.fore_color.rgb = BG if ri % 2 else LIGHT
                r.font.color.rgb = INK
                r.font.size = Pt(font)
    return gt


def bullets(slide, x, y, w, h, items, size=15, spacing=1.15, gap=6):
    paras = []
    for it in items:
        if isinstance(it, tuple):
            head, body = it
            paras.append([("▪ ", {"color": BLUE, "bold": True, "size": size}),
                          (head, {"bold": True, "size": size}),
                          (body, {"size": size, "color": INK})])
        else:
            paras.append([("▪ ", {"color": BLUE, "bold": True, "size": size}),
                          (it, {"size": size})])
        paras.append([(" ", {"size": gap})])
    return text(slide, x, y, w, h, paras[:-1], spacing=spacing)


# ============================================================ 1 cover
s = add_slide()
rect(s, 0, 0, SW, SH, BG)
rect(s, 0, Inches(4.05), SW, Inches(0.045), BLUE)
rect(s, Inches(0.55), Inches(2.30), Inches(0.75), Inches(0.09), BLUE)
text(s, Inches(0.55), Inches(2.55), Inches(11.5), Inches(1.1),
     "云侧算子迁移端侧(Kirin 9030)评审汇报",
     size=38, bold=True)
text(s, Inches(0.55), Inches(3.45), Inches(11.5), Inches(0.5),
     "迁移指南解读与框架侧免改方案(2.5 透明降级 / 2.6 值烘焙)立项建议",
     size=17, color=GRAY)
text(s, Inches(0.55), Inches(4.35), Inches(11.5), Inches(0.4),
     [[("汇报人:____________        日期:2026-09-03", {"size": 14, "color": GRAY})]])
text(s, Inches(0.55), Inches(6.6), Inches(12), Inches(0.4),
     "依据:《云侧算子迁移端侧(Kirin 9030)指南》(2026-09-01 / 09-02 / 09-03 增补)",
     size=11, color=GRAY)

# ============================================================ 2 background
s = add_slide()
y = header(s, "背景与问题", "为什么要做这次迁移")
bullets(s, MARGIN, y, Inches(12.2), Inches(3.4), [
    ("迁移动因:", "将云侧(910/910B/950 系,npuarch 如 DAV_1001/DAV_2201/DAV_3510)"
     "编写的 PyPTO 算子迁移至端侧 Kirin 9030"),
    ("案例算子:", "flash_attention varlen forward kernel"
     "(python/tests/st/operator/flash_attention_mha/flash_attention_mha_impl.py)"),
    ("核心矛盾:", "云侧为多核、大 buffer、动态 varlen 设计;端侧单核、128KB UB、"
     "静态 shape 强约束——云侧 tile 配置与控制流模式基本不可行"),
    ("本轮产出:", "迁移指南(差异事实 + 改法对照)+ 端侧代码草稿"
     "(python/tests/ut/kirin/flash_attention_fa/flash_attention_fa_edge_draft.py)"),
], size=15)
rect(s, MARGIN, Inches(5.35), Inches(12.2), Inches(1.0), LIGHT)
text(s, Inches(0.85), Inches(5.52), Inches(11.6), Inches(0.7),
     [[("现状声明:", {"bold": True, "color": WARN}),
       ("指南基于访谈共识与仓库内事实(SocInfo 配置、UT 惯例)撰写,"
        "所有代码改动均未经真机/SIM 验证,待验证项见第 13 页 V1–V10。",
        {"color": INK})]], size=14)

# ============================================================ 3 overview
s = add_slide()
y = header(s, "总览:五大差异 + 一个总原则", "兼作目录;后续各页逐项展开")
text(s, MARGIN, y, Inches(12.2), Inches(0.4),
     [[("总原则:复用优先——能不改的代码一律不改,", {"bold": True, "size": 16}),
       ("改动面收敛到端侧确实不支持的点", {"size": 16, "color": GRAY})]])
items = [
    ("差异一  静态 shape 约束", "影响最深:循环边界、分支条件必须编译期确定", "P5"),
    ("差异二  数据类型", "BF16→FP16、FP32 中间量→FP16,计算过程不动", "P6"),
    ("差异三  函数签名", "最大程度保留:cu_seqlens 留在签名,调用方零改动", "P6"),
    ("差异四  buffer / tile", "128KB UB 下云侧 tile 全家桶不可行,用户自调", "P7"),
    ("差异五  jit 装饰参数", "全删,只留 soc_version", "P7"),
    ("框架方案 2.5 + 2.6", "透明降级 + 值烘焙,互补缺一不可(本次评审重点)", "P8–12"),
]
ty = Inches(2.15)
for i, (t1, t2, pg) in enumerate(items):
    ry = ty + Inches(0.78) * i
    rect(s, MARGIN, ry, Inches(12.2), Inches(0.66), LIGHT if i % 2 == 0 else BG,
         LINE)
    text(s, Inches(0.8), ry + Inches(0.14), Inches(3.6), Inches(0.4), t1,
         size=15, bold=True, color=BLUE if i == 5 else INK)
    text(s, Inches(4.5), ry + Inches(0.16), Inches(7.2), Inches(0.4), t2,
         size=13, color=GRAY)
    text(s, Inches(11.8), ry + Inches(0.16), Inches(0.8), Inches(0.4), pg,
         size=13, color=BLUE, align=PP_ALIGN.RIGHT)
rect(s, MARGIN, Inches(6.95), Inches(12.2), Inches(0.42), WARN)
text(s, Inches(0.8), Inches(7.01), Inches(11.8), Inches(0.3),
     [[("复用原则的例外:", {"bold": True, "color": RGBColor(0xFF, 0xFF, 0xFF)}),
       ("云侧依赖 pypto.loop 符号执行的惰性平台分支(npuarch == DAV_3510)"
        "在循环表达迁移时需一并移除", {"color": RGBColor(0xFF, 0xFF, 0xFF)})]],
     size=12)

# ============================================================ 4 platform facts
s = add_slide()
y = header(s, "平台差异事实表", "来源:Kirin9030.ini 与云侧典型配置对比")
rows = [
    ["参数", "Kirin9030(端侧)", "云侧典型(910B/950)", "迁移影响"],
    ["ub_size", "128 KB", "~256KB–1MB", "tile 容量大幅缩水,云侧配置基本不可行"],
    ["cube_m/n/k_size", "16 / 8 / 16", "128 级", "tile 粒度小一个数量级,须为整数倍"],
    ["ai_core_cnt", "1 核", "数十核", "多核切分/调度不复存在"],
    ["dtype 覆盖", "无 BF16 用例", "BF16/FP32 全覆盖", "BF16 输入与 FP32 中间量需迁移"],
    ["批次支持", "单 batch", "多 batch", "varlen 拼包模式不可用"],
    ["l1_size / vec_calc / ubblock", "512KB / 128 / 32", "数MB / — / —", "cube 分块与对齐约束"],
]
table(s, MARGIN, y, Inches(12.2), rows, col_widths=[2.3, 2.4, 2.4, 5.1],
      font=12, header_font=12, row_h=Inches(0.52))
rect(s, MARGIN, Inches(5.45), Inches(12.2), Inches(0.95), LIGHT)
text(s, Inches(0.85), Inches(5.62), Inches(11.6), Inches(0.65),
     [[("利好:", {"bold": True, "color": BLUE}),
       ("Kirin9030 有 SIM 仿真平台配置(simulation_platform.cpp 的 KIRIN_9030 映射),"
        "仓库 UT(python/tests/ut/kirin/)以 run_mode=RunMode.SIM 运行,无需真机即可验证。",
        {"size": 13})]])

# ============================================================ 5 diff 1 static shape
s = add_slide()
y = header(s, "差异一:静态 shape 约束(影响最深)",
           "不仅 shape,循环边界与分支条件也必须编译期确定")
bullets(s, MARGIN, y, Inches(6.0), Inches(3.6), [
    ("端侧调用契约:", "单 batch;S = S_max(padding 由调用方负责);"
     "cu_seqlens 必须为 [0, S_max] 且值不参与计算"),
    ("核心改造:", "删除 cu_seqlens 的运行期值读取,改为静态推导 "
     "seq_len_q = q.shape[1];四层 pypto.loop 改原生 range"),
], size=14)
bullets(s, Inches(6.9), y, Inches(5.9), Inches(3.6), [
    ("常见误解:", "值恰好是常量也不行——cu_seqlens[b] 构造的是运行期符号表达式,"
     "\u201c值是常量\u201d≠\u201c编译期常量\u201d,只有 shape 读取与 host 侧 int 传入是静态的"),
    ("签名策略:", "cu_seqlens 保留在签名中一字不差,仅改 kernel 内部三处值读取,"
     "host/model 侧调用代码零改动"),
], size=14)
rect(s, MARGIN, Inches(5.05), Inches(12.2), Inches(0.9), WARN)
text(s, Inches(0.85), Inches(5.18), Inches(11.7), Inches(0.65),
     [[("静默出错风险:", {"bold": True, "color": RGBColor(0xFF, 0xFF, 0xFF)}),
       ("若真传多 batch 拼包+真实累积长度,或 padding 后 pad 位 K/V 参与 softmax,"
        "均会结果错但不报错。首版不带 mask,契约=调用方保证 pad 策略(mask 扩展见第 16 页)。",
        {"color": RGBColor(0xFF, 0xFF, 0xFF), "size": 13})]], size=13)
text(s, MARGIN, Inches(6.25), Inches(12.2), Inches(0.5),
     [[("机械改写 vs 语义静态化:", {"bold": True}),
       ("loop→range、is_loop_begin/end→整数比较、pypto.min→min 三项属机械改写,"
        "可由 2.5 方案免手工修改;值读取与 as_variable 是语义约束,必须用户改造。",
        {"color": GRAY})]], size=13)

# ============================================================ 6 diff 2&3
s = add_slide()
y = header(s, "差异二~三:数据类型与函数签名",
           "只换类型,不动计算过程;签名最大程度保留")
rows = [
    ["云侧", "端侧", "涉及位置(FA 案例)"],
    ["BF16 输入(q/k/v)", "FP16", "q/k/v 注解及调用方"],
    ["FP32 中间量", "FP16", "scores、scores_scaled、mij/s_shifted/pij/lij、L/M 累加器"],
    ["BF16 的 P", "FP16", "pypto.cast(pij, DT_BF16) → DT_FP16"],
    ["FP32 输出(l/m)", "FP16", "输出注解及调用方"],
]
table(s, MARGIN, y, Inches(6.4), rows, col_widths=[2.2, 1.2, 3.0],
      font=11, header_font=11, row_h=Inches(0.42))
rows2 = [
    ["参数", "端侧语义"],
    ["q/k/v", "[1, S_max, N, D] 静态,FP16"],
    ["output", "[1, S_max, N*D]"],
    ["l/m_output", "[1, S_max, N],FP16"],
    ["cu_seqlens_q/k", "[2],值必须 [0, S_max],值不参与计算"],
]
table(s, Inches(7.3), y, Inches(5.45), rows2, col_widths=[1.6, 3.4],
      font=11, header_font=11, row_h=Inches(0.42))
text(s, MARGIN, Inches(4.0), Inches(6.4), Inches(0.3),
     [[("替换规则(签名与结构不变)", {"bold": True, "size": 13})]])
text(s, Inches(7.3), Inches(4.0), Inches(5.45), Inches(0.3),
     [[("端侧入参语义(不改签名结构)", {"bold": True, "size": 13})]])
rect(s, MARGIN, Inches(4.45), Inches(12.2), Inches(1.5), LIGHT)
text(s, Inches(0.85), Inches(4.6), Inches(11.7), Inches(1.25), [
    [("已知精度风险(如实标注,不改计算过程):", {"bold": True, "size": 13})],
    [("▪ FP16 累加 L/M 误差放大:11-bit 尾数在长序列下可能放大误差,验收时关注 (V1)",
      {"size": 12.5})],
    [("▪ pypto.exp 在 FP16 输入下的实现路径待查证 (V2)", {"size": 12.5})],
    [("▪ pypto.div INTRINSIC 精度模式在 FP16 下的行为待查证 (V3)",
      {"size": 12.5})],
], spacing=1.15)
text(s, MARGIN, Inches(6.2), Inches(12.2), Inches(0.5),
     "调用方负责把权重/激活提前转成 FP16;kernel 内不做 BF16→FP16 转换(端侧无 BF16 支持证据)。",
     size=13, color=GRAY)

# ============================================================ 7 diff 4&5
s = add_slide()
y = header(s, "差异四~五:buffer/tile 与 jit 参数", "tile 用户自调,只给事实;jit 参数全删")
bullets(s, MARGIN, y, Inches(6.2), Inches(2.9), [
    ("tile 触点盘点: ", "FA 案例共 10+ 处——模块常量 Q_TILE/K_TILE=320、"
     "全局 set_cube/vec_tile_shapes、循环内 QK^T 与 PV 的 6 处重设、"
     "nbuffer=8 的 pass_options(128KB UB 下大概率不可行,必须改)"),
    ("调优建议(非推导): ", "保守小 tile 起步(Q_TILE=K_TILE=64,vec tile ≤ 64/128),"
     "按 16/8/16 整数倍与 ubblock=32 对齐迭代;仍不足则结构退守单 head 循环"),
], size=13.5)
rows = [
    ["阶段", "估计", "说明"],
    ["拿到能跑的配置", "1–2 人日", "改参数→编译→看 UB 溢出/对齐报错→再改"],
    ["性能调优", "1–2 人周", "单核小 buffer 对 tile 敏感,需扫配置;强依赖仿真反馈"],
    ["后续算子", "0.5–1 人日/算子", "触点认知可复用,边际递减"],
]
table(s, MARGIN, Inches(4.0), Inches(6.2), rows, col_widths=[2.0, 1.6, 2.6],
      font=11, header_font=11, row_h=Inches(0.44))
text(s, MARGIN, Inches(3.7), Inches(6.2), Inches(0.3),
     [[("工作量评估(前提:SIM 仿真可用)", {"bold": True, "size": 13})]])
rows2 = [
    ["jit 选项", "处置", "理由"],
    ["device_sched_mode", "删", "设备侧调度,端侧单核无需求 (V4)"],
    ["stitch_function_max_num", "删", "多核切分相关,单核不适用 (V4)"],
    ["nbuffer 系列(=8)", "删", "与云侧大 buffer 耦合,端侧用默认值 (V4)"],
    ["runtime_debug_mode", "删", "调试开关,非功能必需"],
]
table(s, Inches(7.0), y, Inches(5.75), rows2, col_widths=[2.2, 0.7, 2.85],
      font=10.5, header_font=10.5, row_h=Inches(0.44))
text(s, Inches(7.0), Inches(4.05), Inches(5.75), Inches(0.3),
     [[("jit 装饰参数:只留 codegen_options.soc_version=Kirin9030,",
        {"bold": True, "size": 12.5})],
      [("按 UT 惯例加 runtime_options.run_mode=RunMode.SIM", {"size": 12.5})]])

# ============================================================ 8 section page
s = add_slide()
rect(s, 0, 0, SW, SH, LIGHT)
rect(s, 0, Inches(3.2), SW, Inches(0.045), BLUE)
text(s, MARGIN, Inches(2.2), Inches(12.2), Inches(0.5),
     "框架侧免改方案(规划中,未实现)", size=20, color=BLUE, bold=True)
text(s, MARGIN, Inches(3.45), Inches(12.2), Inches(0.8),
     "2.5 循环透明降级  +  2.6 编译期值烘焙", size=34, bold=True)
text(s, MARGIN, Inches(4.5), Inches(12.2), Inches(0.5),
     "两者互补,缺一不可——目标是 varlen 云侧 kernel 端侧零修改", size=16, color=GRAY)
text(s, MARGIN, Inches(5.4), Inches(12.2), Inches(0.8),
     [[("2.5 解决\u201c循环写法\u201d免改:", {"bold": True, "size": 14}),
       ("loop / is_loop_begin / min 三类机械改写自动映射    ", {"size": 14})],
      [("2.6 解决\u201c值读取\u201d免改:", {"bold": True, "size": 14}),
       ("cu_seqlens 值读取在编译期折叠为立即数", {"size": 14})]], spacing=1.3)

# ============================================================ 9 2.5 motivation
s = add_slide()
y = header(s, "2.5 透明降级:动机与可行性",
           "定位:让\u201c已静态化的代码 + 云侧循环写法\u201d云/端共用同一份源码")
bullets(s, MARGIN, y, Inches(6.1), Inches(3.2), [
    ("能做到: ", "三行机械改写由框架自动映射——四层 pypto.loop→range、"
     "is_loop_begin/end→整数比较、pypto.min→Python min"),
    ("做不到: ", "语义静态化(删值读取、as_variable)无法替代;"
     "前端无法不读真实数据做常量折叠"),
    ("边界策略: ", "遇动态边界(SymbolicScalar)时报错而非静默降级——"
     "替用户提前发现\u201c该段代码尚未静态化\u201d"),
], size=13.5)
bullets(s, Inches(6.95), y, Inches(5.8), Inches(3.2), [
    ("先例一: ", "pil/ops.py 的 loop_impl 已内置静态/动态双路径——"
     "LoopRange 走动态 _dyn_for,原生可迭代走 _static_for(trace 期逐迭代展开)。"
     "静态循环机制已存在,缺的只是端侧场景的路由"),
    ("先例二: ", "同文件 max_impl 已有\u201c入参全 int 则退回 Python 内建\u201d"
     "的透明降级先例,pypto.min 可照此实现"),
], size=13.5)
rect(s, MARGIN, Inches(5.35), Inches(12.2), Inches(0.85), LIGHT)
text(s, Inches(0.85), Inches(5.5), Inches(11.7), Inches(0.6),
     [[("结论:", {"bold": True, "color": BLUE}),
       ("方案不是新造机制,而是把已有静态路径在端侧场景接通——"
        "实现风险低、不碰 C++,估计 0.5–1 人日(2.5.7 落地顺序共 4 步)。",
        {"size": 13})]])

# ============================================================ 10 2.5 mechanism
s = add_slide()
y = header(s, "2.5 透明降级:_LoopInt 属性桥机制",
           "Python int 不可挂属性 → 用允许动态属性的 int 子类桥接")
steps = [
    ("1. 路由", "loop() 静态分支:开关开启且循环边界全为 int 时,"
     "降级为 range 逐迭代 yield _LoopInt"),
    ("2. 桥接", "_LoopInt(int) 子类默认带 __dict__,可 setattr 携带 "
     "_loop_begin/_loop_end/_loop_step,通过现有 hasattr 检查"),
    ("3. 判定", "is_loop_begin/end 增加 int 分支:直接读值上属性返回 "
     "Python bool,现有校验逻辑原样复用;begin 随值携带,正确处理 start≠0"),
    ("4. 降级", "pypto.min/max 照 max_impl 模式:入参全 int 退回 Python 内建"),
]
for i, (t1, t2) in enumerate(steps):
    ry = y + Inches(0.85) * i
    rect(s, MARGIN, ry, Inches(6.1), Inches(0.72), LIGHT if i % 2 == 0 else BG, LINE)
    text(s, Inches(0.75), ry + Inches(0.08), Inches(1.0), Inches(0.3), t1,
         size=13, bold=True, color=BLUE)
    text(s, Inches(1.75), ry + Inches(0.05), Inches(4.75), Inches(0.62), t2,
         size=11, color=INK)
rows = [
    ["代码", "透明降级能否覆盖"],
    ["四层 pypto.loop → range", "能"],
    ["is_loop_begin/end → 整数比较", "能"],
    ["pypto.min → min", "能"],
    ["cu_seqlens 值读取 / .as_variable()", "不能(触发报错,符合预期;需 2.6 配合)"],
    ["循环体内 set_pass_options 等", "不报错,但被逐迭代 trace"],
    ["jit 云侧专属选项", "仍按第 6 节处理(过滤/告警)"],
]
table(s, Inches(6.95), y, Inches(5.8), rows, col_widths=[3.0, 2.8],
      font=10.5, header_font=10.5, row_h=Inches(0.4))
rect(s, MARGIN, Inches(5.05), Inches(6.1), Inches(1.1), WARN)
text(s, Inches(0.8), Inches(5.15), Inches(5.7), Inches(0.95),
     [[("开关设计:", {"bold": True, "color": RGBColor(0xFF, 0xFF, 0xFF), "size": 12.5})],
      [("显式开关(jit frontend_options 或 target 派生),不建议纯靠 npuarch 嗅探"
        "——该属性 trace 期是否就绪是待验证项 (V5c)。",
        {"color": RGBColor(0xFF, 0xFF, 0xFF), "size": 11.5})]], spacing=1.1)
text(s, MARGIN, Inches(6.3), Inches(6.1), Inches(0.5),
     [[("代价:", {"bold": True, "size": 12}),
       ("trace 期全展开,IR 规模 = 迭代次数×循环体;FA 的 32 个 kv tile 无问题,"
        "大 trip count 需上限告警 (V9)", {"size": 11.5, "color": GRAY})]], spacing=1.1)

# ============================================================ 11 2.6 bake
s = add_slide()
y = header(s, "2.6 值烘焙:机制与前置条件",
           "与 2.5 同族的静态前端选项,解决 varlen 值读取免改")
text(s, MARGIN, y, Inches(12.2), Inches(0.6),
     [[("机制设想:", {"bold": True, "size": 14}),
       ("trace 期对显式标记的 tensor 从 host 侧数据读取元素值,把 GetTensorData "
        "符号表达式直接替换为常量——后续 q_end-q_start、q_tile_count 等表达式"
        "自然折叠为编译期常量,as_variable() 在此模式改 no-op 或报错。", {"size": 13})],
      [("开关:@pypto.frontend.jit(frontend_options={\"bake_host_values\": "
        "[\"cu_seqlens_q\", \"cu_seqlens_k\"]})", {"size": 12, "color": BLUE})]],
     spacing=1.2)
rows = [
    ["前置条件", "说明", "现状依据"],
    ["host 可得性", "只对 host 侧数据生效;device tensor 烘不了",
     "云侧 ready_on_host_tensors 机制可复用"],
    ["变体缓存", "值变化必须触发重编译,否则换 S_max 静默算错",
     "类比 shape policy 变体机制,烘焙值纳入变体 key"],
    ["类型限制", "GetTensorData 仅支持 DT_INT32",
     "恰好覆盖 cu_seqlens 场景,其它 dtype 需扩展"],
]
table(s, MARGIN, Inches(3.3), Inches(12.2), rows, col_widths=[1.6, 4.5, 4.5],
      font=11.5, header_font=11.5, row_h=Inches(0.5))
rect(s, MARGIN, Inches(5.55), Inches(12.2), Inches(1.0), LIGHT)
text(s, Inches(0.85), Inches(5.68), Inches(11.7), Inches(0.75),
     [[("风险:", {"bold": True, "color": WARN})],
      [("▪ 语义偏移:每个值一份编译产物,S_max 种类多时编译次数线性增长"
        "(端侧单 batch 可接受)    ▪ 与 padding 契约交互:静默出错风险不变"
        "    ▪ 名单写错 tensor 名需编译期报错 (V10)", {"size": 12.5})]],
     spacing=1.15)

# ============================================================ 12 benefit
s = add_slide()
y = header(s, "方案收益:演进路线", "2.5 与 2.6 互补推进,逐步收敛用户改动面")
stages = [
    ("现状(无方案)", "用户手改全部:DTYPE + TILE + JIT\n+ 值读取 + as_variable +\n循环表达(机械改写)", BG, INK),
    ("2.5 落地后", "循环表达自动映射:\n云/端共用同一份源码\n(可换回 pypto.loop 写法,\n时机以 V9 为准)", LIGHT, INK),
    ("2.5 + 2.6 齐备", "varlen 端侧零修改:\n仅剩 dtype / tile / jit\n三类改动(非控制流)", BLUE, RGBColor(0xFF, 0xFF, 0xFF)),
]
bw = Inches(3.7)
for i, (t1, body, fill, fg) in enumerate(stages):
    bx = MARGIN + Inches(4.05) * i
    rect(s, bx, y, bw, Inches(2.5), fill, LINE if i < 2 else None)
    text(s, bx + Inches(0.2), y + Inches(0.2), bw - Inches(0.4), Inches(0.4),
         t1, size=17, bold=True, color=fg)
    text(s, bx + Inches(0.2), y + Inches(0.75), bw - Inches(0.4), Inches(1.6),
         body, size=12.5, color=fg, spacing=1.25)
    if i < 2:
        text(s, bx + bw, y + Inches(1.0), Inches(0.35), Inches(0.5), "→",
             size=22, bold=True, color=BLUE, align=PP_ALIGN.CENTER)
rows = [
    ["方案", "落地成本", "性质"],
    ["2.5 循环透明降级", "约 0.5–1 人日,不碰 C++(_controller.py + pil/ops.py + 开关接线 + UT)",
     "仓库已有先例,风险低"],
    ["2.6 编译期值烘焙", "暂无估计,待评估(前置条件已具备仓库依据)",
     "需变体缓存配合,防静默算错"],
]
table(s, MARGIN, Inches(4.5), Inches(12.2), rows, col_widths=[2.2, 6.0, 3.0],
      font=11.5, header_font=11.5, row_h=Inches(0.55))
text(s, MARGIN, Inches(6.2), Inches(12.2), Inches(0.6),
     [[("注:", {"bold": True, "color": GRAY}),
       ("等价性与展开上限需按 V9/V10 验证;行为等价性 UT 按 V5b 方法——"
        "三分支(首/中/末 tile)下降级版与手写 range 版产物比对。",
        {"size": 12, "color": GRAY})]])

# ============================================================ 13 validation
s = add_slide()
y = header(s, "待验证清单(V1–V10)", "环境就绪后执行;★ 标记项阻塞方案决策")
rows = [
    ["ID", "验证项", "方法/说明"],
    ["V1", "FP16 累加 L/M 精度", "SIM 跑 FA 与 FP64 参考比对,扫 S_max 与 kv tile 数"],
    ["V2/V3", "exp / div INTRINSIC 的 FP16 路径", "查 litenpu codegen 实现或 SIM 单测"],
    ["V4", "jit 选项在 litenpu 的支持矩阵", "删干净后 SIM 编译,观察 unknown key 报错"],
    ["V5/V5b", "STATIC 烘入;range+整数比较等价性", "pypto 小用例;三分支(首/中/末 tile)比对"],
    ["★V5c", "npuarch 在 trace 期的取值行为", "决定惰性平台分支移除必要性;决定 2.5 开关能否 arch 派生"],
    ["V6/V7/V8", "assemble 写回;SIM 全链路;combine_axis", "SIM 下比对输出/编译观察"],
    ["★V9", "2.5 降级等价性与展开上限", "三分支产物比对(V5b 基准);IR 规模上限告警阈值"],
    ["★V10", "2.6 值烘焙可行性与变体缓存", "GetTensorData 折叠;改 S_max 触发新变体重编译"],
]
table(s, MARGIN, y, Inches(12.2), rows, col_widths=[1.1, 3.9, 7.2],
      font=11, header_font=11, row_h=Inches(0.5))

# ============================================================ 14 risks
s = add_slide()
y = header(s, "风险汇总", "四类主要风险及现有对策")
risks = [
    ("padding 静默出错", "高", "pad 位 K/V 真实参与 softmax,结果错但不报错;"
     "契约=调用方保证 pad 策略,mask 扩展为可选项"),
    ("FP16 精度误差放大", "中", "online softmax 的 L/M 在长序列下误差可能放大;"
     "不改计算过程,验收时按 V1 关注"),
    ("SIM 环境依赖", "中", "调优强依赖仿真反馈;Kirin9030 SIM 配置已存在但未实测"),
    ("trace 全展开 IR 膨胀", "中", "2.5 静态映射等价循环全展开,大 trip count 会爆;"
     "需上限告警(FA 32 个 kv tile 已验证无问题)"),
]
for i, (t1, lvl, body) in enumerate(risks):
    ry = y + Inches(1.05) * i
    rect(s, MARGIN, ry, Inches(12.2), Inches(0.9), LIGHT if i % 2 == 0 else BG, LINE)
    text(s, Inches(0.8), ry + Inches(0.28), Inches(2.6), Inches(0.4), t1,
         size=15, bold=True)
    lc = WARN if lvl == "高" else BLUE
    text(s, Inches(3.5), ry + Inches(0.3), Inches(0.7), Inches(0.35), lvl,
         size=13, bold=True, color=lc)
    text(s, Inches(4.4), ry + Inches(0.14), Inches(8.1), Inches(0.65), body,
         size=12, color=INK)

# ============================================================ 15 review topics
s = add_slide()
y = header(s, "评审议题", "请评审方决策的三件事")
topics = [
    ("议题一", "2.5 / 2.6 是否立项?",
     "2.5 成本 0.5–1 人日、有仓库先例、不碰 C++;2.6 需变体缓存配合。"
     "立项后 varlen 端侧零修改可期。"),
    ("议题二", "mask 扩展是否纳入首版?",
     "当前契约\u201c调用方保证 pad 策略\u201d存在静默出错面;"
     "mask 扩展走数据流实现不违反静态约束,可后置(第 8 节指引)。"),
    ("议题三", "真机/SIM 验证环境何时就绪?",
     "V1–V10 全部依赖环境;V5c/V9/V10 阻塞方案决策,建议优先排期。"),
]
for i, (tag, t1, body) in enumerate(topics):
    ry = y + Inches(1.5) * i
    rect(s, MARGIN, ry, Inches(1.3), Inches(1.2), BLUE)
    text(s, MARGIN, ry + Inches(0.45), Inches(1.3), Inches(0.4), tag,
         size=16, bold=True, color=RGBColor(0xFF, 0xFF, 0xFF), align=PP_ALIGN.CENTER)
    rect(s, Inches(2.0), ry, Inches(10.75), Inches(1.2), LIGHT, LINE)
    text(s, Inches(2.3), ry + Inches(0.15), Inches(10.2), Inches(0.4), t1,
         size=16, bold=True)
    text(s, Inches(2.3), ry + Inches(0.6), Inches(10.2), Inches(0.55), body,
         size=12.5, color=GRAY)

# ============================================================ 16 appendix
s = add_slide()
y = header(s, "附录:产物位置与扩展指引")
bullets(s, MARGIN, y, Inches(12.2), Inches(2.2), [
    ("迁移指南: ", "docs_cloud_to_edge_migration_guide.md"
     "(正文各节含 FA 案例\u201c逐处对照\u201d表,与草稿逐处映射)"),
    ("端侧代码草稿: ", "python/tests/ut/kirin/flash_attention_fa/"
     "flash_attention_fa_edge_draft.py——单文件 kernel,静态契约、FP16 化、"
     "保守 tile 初值,头部标注全部待验证项"),
], size=14)
rect(s, MARGIN, Inches(3.6), Inches(12.2), Inches(2.4), LIGHT)
text(s, Inches(0.85), Inches(3.78), Inches(11.7), Inches(2.1), [
    [("mask 扩展指引(可选,非迁移必需;参考云侧 test_fa_with_mask.py 既有模式):",
      {"bold": True, "size": 14})],
    [("1. 新增静态 mask 入参(如 [1, S_max, S_max] 可广播形状),pad 位 -inf/极小值",
      {"size": 13})],
    [("2. 在 scores_scaled 之后、mij=amax 之前叠加:scores_masked = scores_scaled + mask",
      {"size": 13})],
    [("3. 全程数据流实现(加法/select),不引入控制流,不违反静态约束", {"size": 13})],
    [("4. mask 不依赖 head 维,与双头打包直接广播兼容", {"size": 13})],
], spacing=1.3)
text(s, MARGIN, Inches(6.3), Inches(12.2), Inches(0.5),
     [[("换回时机:", {"bold": True, "color": GRAY}),
       ("2.5 落地后草稿中 range+整数比较可换回 pypto.loop 写法(V9 验证结论为准);"
        "2.5+2.6 齐备后 varlen 云侧 kernel 端侧零修改。",
        {"size": 12.5, "color": GRAY})]])

# ============================================================ 17 end
s = add_slide()
rect(s, 0, 0, SW, SH, BG)
rect(s, 0, Inches(3.6), SW, Inches(0.045), BLUE)
text(s, 0, Inches(3.9), SW, Inches(0.9), "谢谢,请评审指正",
     size=34, bold=True, align=PP_ALIGN.CENTER)
text(s, 0, Inches(4.9), SW, Inches(0.5),
     "云侧算子迁移端侧(Kirin 9030)评审汇报 · 2026-09-03",
     size=14, color=GRAY, align=PP_ALIGN.CENTER)

out = "docs_cloud_to_edge_migration_guide.pptx"
prs.save(out)
print("saved:", out, "slides:", len(prs.slides.__iter__.__self__._sldIdLst))
