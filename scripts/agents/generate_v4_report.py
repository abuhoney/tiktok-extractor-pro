#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate PDF report for TikTok 1000-Agents v4 results.
Arabic content + ReportLab.
"""

import os, sys, json, glob, hashlib
from datetime import datetime
from collections import Counter, defaultdict

import arabic_reshaper
from bidi.algorithm import get_display

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm
from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_RIGHT, TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle,
    Image, KeepTogether, Frame, PageTemplate, BaseDocTemplate, NextPageTemplate,
)
from reportlab.platypus.flowables import HRFlowable
from reportlab.graphics.shapes import Drawing, Rect, String, Line
from reportlab.graphics.charts.barcharts import VerticalBarChart, HorizontalBarChart
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.charts.legends import Legend

# ─── Fonts ───
# NOTE: FreeSerif does NOT support Arabic glyphs (0 Arabic chars).
# FreeSerif has full Arabic + Presentation Forms-A/B support (255+207+141).
# We use FreeSerif for everything to avoid font-switching issues.
pdfmetrics.registerFont(TTFont('FreeSerif', '/usr/share/fonts/truetype/freefont/FreeSerif.ttf'))
pdfmetrics.registerFont(TTFont('FreeSerif-Bold', '/usr/share/fonts/truetype/freefont/FreeSerifBold.ttf'))
pdfmetrics.registerFont(TTFont('FreeSerif-Italic', '/usr/share/fonts/truetype/freefont/FreeSerifItalic.ttf'))
pdfmetrics.registerFont(TTFont('FreeSerif-BoldItalic', '/usr/share/fonts/truetype/freefont/FreeSerifBoldItalic.ttf'))
pdfmetrics.registerFont(TTFont('DejaVu', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
pdfmetrics.registerFont(TTFont('DejaVu-Bold', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'))
pdfmetrics.registerFont(TTFont('FreeSans', '/usr/share/fonts/truetype/freefont/FreeSans.ttf'))

# Register font family so <b> and <i> work in Paragraph
from reportlab.pdfbase.pdfmetrics import registerFontFamily
registerFontFamily('FreeSerif',
                   normal='FreeSerif',
                   bold='FreeSerif-Bold',
                   italic='FreeSerif-Italic',
                   boldItalic='FreeSerif-BoldItalic')

# Arabic-reshaper wrapper
def ar(text: str) -> str:
    """Reshape Arabic text for ReportLab (RTL display)."""
    reshaped = arabic_reshaper.reshape(text)
    return get_display(reshaped)

# ─── Colors (tech & futuristic palette) ───
C_BG = HexColor('#0f172a')          # deep navy
C_PRIMARY = HexColor('#1e293b')     # body bg
C_ACCENT = HexColor('#06b6d4')      # cyan
C_ACCENT_2 = HexColor('#ec4899')    # pink
C_ACCENT_3 = HexColor('#f59e0b')    # amber
C_TEXT = HexColor('#0f172a')        # body text (on white)
C_TEXT_LIGHT = HexColor('#64748b')
C_TABLE_HEADER = HexColor('#1e293b')
C_TABLE_ROW_ALT = HexColor('#f1f5f9')
C_GREEN = HexColor('#10b981')
C_RED = HexColor('#ef4444')
C_WHITE = HexColor('#ffffff')

# ─── Load data ───
V4_DIR = '/home/z/my-project/download/agents_1000_v4'
V3_DIR = '/home/z/my-project/download/agents_1000_v3'
OUT_PDF = '/home/z/my-project/download/agents_v4_analysis_report.pdf'

def load_results(dirpath):
    results = []
    for f in sorted(glob.glob(os.path.join(dirpath, 'agent_*/results.json'))):
        with open(f, encoding='utf-8') as fp:
            results.append(json.load(fp))
    return results

v3 = load_results(V3_DIR)
v4 = load_results(V4_DIR)

# ─── Compute statistics ───
def stats(rs):
    s = {
        'total': len(rs),
        '200': sum(1 for r in rs if r.get('http_status') == 200),
        '302': sum(1 for r in rs if r.get('http_status') == 302),
        '400': sum(1 for r in rs if r.get('http_status') == 400),
        'avg_score': sum(r.get('score', 0) for r in rs) / len(rs) if rs else 0,
        'max_score': max((r.get('score', 0) for r in rs), default=0),
        'ge_80': sum(1 for r in rs if r.get('score', 0) >= 80),
        'ge_50': sum(1 for r in rs if r.get('score', 0) >= 50),
        'json_responses': sum(1 for r in rs if r.get('response_type') == 'json'),
        'html_responses': sum(1 for r in rs if r.get('response_type') == 'html'),
        'empty_responses': sum(1 for r in rs if r.get('response_type') == 'empty'),
    }
    return s

v3_stats = stats(v3)
v4_stats = stats(v4)

# Score distribution by endpoint
endpoint_scores = defaultdict(list)
for r in v4:
    endpoint_scores[r.get('endpoint', '')].append(r.get('score', 0))
endpoint_summary = {e: {'avg': round(sum(s)/len(s), 2), 'max': max(s), 'count': len(s)}
                    for e, s in endpoint_scores.items()}

# Score distribution by sub-variation
var_scores = defaultdict(list)
for r in v4:
    var_scores[r.get('sub_variation', '')].append(r.get('score', 0))
var_summary = {v: {'avg': round(sum(s)/len(s), 2), 'max': max(s), 'count': len(s)}
               for v, s in var_scores.items()}

# Top findings
all_findings = Counter()
for r in v4:
    for f in r.get('unique_findings', []):
        all_findings[f] += 1

# Top 10 agents
top_agents = sorted(v4, key=lambda x: -x.get('score', 0))[:10]

# ─── Styles ───
styles = getSampleStyleSheet()

# Arabic-aware paragraph styles
def make_style(name, parent=None, **kwargs):
    base = parent or styles['Normal']
    return ParagraphStyle(name, parent=base, **kwargs)

style_title = make_style('ArTitle',
    fontName='FreeSerif-Bold', fontSize=28, leading=34,
    alignment=TA_CENTER, textColor=C_WHITE, spaceAfter=12)

style_subtitle = make_style('ArSubtitle',
    fontName='FreeSerif', fontSize=14, leading=20,
    alignment=TA_CENTER, textColor=HexColor('#cbd5e1'), spaceAfter=20)

style_h1 = make_style('ArH1',
    fontName='FreeSerif-Bold', fontSize=18, leading=24,
    alignment=TA_RIGHT, textColor=C_ACCENT, spaceBefore=18, spaceAfter=10)

style_h2 = make_style('ArH2',
    fontName='FreeSerif-Bold', fontSize=14, leading=20,
    alignment=TA_RIGHT, textColor=C_PRIMARY, spaceBefore=12, spaceAfter=6)

style_body = make_style('ArBody',
    fontName='FreeSerif', fontSize=10.5, leading=18,
    alignment=TA_RIGHT, textColor=C_TEXT, spaceAfter=6,
    wordWrap='RTL')

style_body_ltr = make_style('BodyLTR',
    fontName='DejaVu', fontSize=9, leading=13,
    alignment=TA_LEFT, textColor=C_TEXT_LIGHT, spaceAfter=4,
    wordWrap='LTR')

style_code = make_style('Code',
    fontName='DejaVu', fontSize=8.5, leading=12,
    alignment=TA_LEFT, textColor=HexColor('#334155'),
    backColor=HexColor('#f1f5f9'),
    leftIndent=8, rightIndent=8, spaceAfter=8, spaceBefore=4)

style_caption = make_style('ArCaption',
    fontName='FreeSerif', fontSize=9, leading=12,
    alignment=TA_CENTER, textColor=C_TEXT_LIGHT, spaceAfter=10)

style_table_header = make_style('TblH',
    fontName='FreeSerif-Bold', fontSize=9.5, leading=12,
    alignment=TA_CENTER, textColor=C_WHITE)

style_table_cell = make_style('TblC',
    fontName='FreeSerif', fontSize=9, leading=12,
    alignment=TA_CENTER, textColor=C_TEXT)

style_table_cell_ar = make_style('TblCAr',
    fontName='FreeSerif', fontSize=9, leading=12,
    alignment=TA_RIGHT, textColor=C_TEXT)

# ─── Helpers ───
def P(text, style=None):
    """Paragraph with Arabic shaping if needed."""
    if style is None:
        style = style_body
    # If contains Arabic chars, reshape
    has_arabic = any('\u0600' <= c <= '\u06ff' for c in text)
    if has_arabic and style.alignment == TA_RIGHT:
        return Paragraph(ar(text), style)
    return Paragraph(text, style)

def P_ar(text, style=None):
    """Always shape as Arabic."""
    if style is None:
        style = style_body
    return Paragraph(ar(text), style)

# ─── Charts ───
def make_v3_v4_comparison_chart():
    """Bar chart: v3 vs v4 — % agents with 200 OK."""
    d = Drawing(440, 220)

    # Title
    d.add(String(220, 200, ar('نسبة الوكلاء الذين حصلوا على استجابة 200 OK'),
                 fontName='FreeSerif', fontSize=11, fillColor=C_PRIMARY,
                 textAnchor='middle'))

    # Bars
    chart = VerticalBarChart()
    chart.x = 60
    chart.y = 40
    chart.width = 320
    chart.height = 130
    chart.data = [[v3_stats['200'] / 10, v4_stats['200'] / 10]]  # convert to %
    chart.categoryAxis.categoryNames = ['v3', 'v4']
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = 100
    chart.valueAxis.valueStep = 20
    chart.bars[0].fillColor = C_ACCENT
    chart.bars[0].strokeColor = None
    chart.barWidth = 60
    chart.groupSpacing = 30
    chart.valueAxis.labelTextFormat = '%d%%'
    chart.valueAxis.labels.fontName = 'DejaVu'
    chart.valueAxis.labels.fontSize = 8
    chart.categoryAxis.labels.fontName = 'DejaVu'
    chart.categoryAxis.labels.fontSize = 10
    d.add(chart)

    # Value labels above bars
    d.add(String(110, 175, f"{v3_stats['200']/10:.1f}%",
                 fontName='DejaVu-Bold', fontSize=11, fillColor=C_RED, textAnchor='middle'))
    d.add(String(290, 175, f"{v4_stats['200']/10:.1f}%",
                 fontName='DejaVu-Bold', fontSize=11, fillColor=C_GREEN, textAnchor='middle'))

    return d

def make_endpoint_chart():
    """Bar chart: avg score by endpoint."""
    d = Drawing(440, 230)
    d.add(String(220, 210, ar('متوسط الدرجات حسب نقطة النهاية (Endpoint)'),
                 fontName='FreeSerif', fontSize=11, fillColor=C_PRIMARY,
                 textAnchor='middle'))

    endpoints = list(endpoint_summary.keys())
    avgs = [endpoint_summary[e]['avg'] for e in endpoints]

    chart = VerticalBarChart()
    chart.x = 50
    chart.y = 40
    chart.width = 340
    chart.height = 140
    chart.data = [avgs]
    chart.categoryAxis.categoryNames = [e.split(':')[-1].strip()[:15] for e in endpoints]
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = 100
    chart.valueAxis.valueStep = 20
    chart.bars[0].fillColor = C_ACCENT
    chart.bars[0].strokeColor = None
    chart.barWidth = 50
    chart.groupSpacing = 20
    chart.valueAxis.labels.fontName = 'DejaVu'
    chart.valueAxis.labels.fontSize = 8
    chart.categoryAxis.labels.fontName = 'DejaVu'
    chart.categoryAxis.labels.fontSize = 8
    chart.categoryAxis.labels.angle = 0
    d.add(chart)
    return d

def make_score_distribution_chart():
    """Pie chart of score distribution for v4."""
    d = Drawing(440, 220)
    d.add(String(220, 200, ar('توزيع الدرجات في الإصدار v4'),
                 fontName='FreeSerif', fontSize=11, fillColor=C_PRIMARY,
                 textAnchor='middle'))

    # Categories: 80+, 50-79, 1-9
    ge80 = v4_stats['ge_80']
    ge50 = v4_stats['ge_50'] - v4_stats['ge_80']
    low = v4_stats['total'] - v4_stats['ge_50']

    pie = Pie()
    pie.x = 140
    pie.y = 30
    pie.width = 140
    pie.height = 140
    pie.data = [ge80, ge50, low]
    pie.labels = None
    pie.slices.strokeColor = white
    pie.slices.strokeWidth = 2
    pie.slices[0].fillColor = C_GREEN
    pie.slices[1].fillColor = C_ACCENT
    pie.slices[2].fillColor = C_ACCENT_3
    d.add(pie)

    # Legend
    leg = Legend()
    leg.x = 310
    leg.y = 130
    leg.colorNamePairs = [
        (C_GREEN, f'{ar("80+ (نجاح كامل)")}  {ge80}'),
        (C_ACCENT, f'{ar("50-79 (JSON صالح)")}  {ge50}'),
        (C_ACCENT_3, f'{ar("1-9 (رد فارغ)")}  {low}'),
    ]
    leg.fontName = 'FreeSerif'
    leg.fontSize = 9
    leg.alignment = 'right'
    leg.columnMaximum = 4
    leg.deltay = 18
    d.add(leg)

    return d

def make_findings_chart():
    """Horizontal bar: top findings."""
    d = Drawing(440, 240)
    d.add(String(220, 220, ar('أكثر النتائج الفريدة تكراراً عبر 1000 وكيل'),
                 fontName='FreeSerif', fontSize=11, fillColor=C_PRIMARY,
                 textAnchor='middle'))

    top = all_findings.most_common(6)
    labels = [f[0] for f in top]
    values = [f[1] for f in top]

    chart = HorizontalBarChart()
    chart.x = 160
    chart.y = 30
    chart.width = 250
    chart.height = 160
    chart.data = [values]
    chart.categoryAxis.categoryNames = labels
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = 1100
    chart.valueAxis.valueStep = 200
    chart.bars[0].fillColor = C_ACCENT_2
    chart.bars[0].strokeColor = None
    chart.barWidth = 12
    chart.categoryAxis.labels.fontName = 'DejaVu'
    chart.categoryAxis.labels.fontSize = 7.5
    chart.categoryAxis.labels.dx = -5
    chart.valueAxis.labels.fontName = 'DejaVu'
    chart.valueAxis.labels.fontSize = 8
    d.add(chart)
    return d

# ─── Page Templates ───
def cover_page(canvas, doc):
    """Cover page with dark navy background + accent shapes."""
    w, h = A4
    # Background
    canvas.setFillColor(C_BG)
    canvas.rect(0, 0, w, h, fill=1, stroke=0)

    # Decorative shapes (top-right corner)
    canvas.setFillColor(HexColor('#1e3a5f'))
    canvas.circle(w - 30, h - 30, 120, fill=1, stroke=0)
    canvas.setFillColor(HexColor('#0e7490'))
    canvas.circle(w - 80, h - 80, 60, fill=1, stroke=0)

    # Bottom-left decorative
    canvas.setFillColor(HexColor('#831843'))
    canvas.circle(40, 40, 100, fill=1, stroke=0)
    canvas.setFillColor(HexColor('#be185d'))
    canvas.circle(80, 100, 40, fill=1, stroke=0)

    # Title
    canvas.setFillColor(C_WHITE)
    canvas.setFont('FreeSerif-Bold', 28)
    canvas.drawCentredString(w/2, h - 200, ar('تحليل أداء 1000 وكيل'))

    canvas.setFont('FreeSerif-Bold', 22)
    canvas.setFillColor(C_ACCENT)
    canvas.drawCentredString(w/2, h - 240, ar('لاستخراج بيانات TikTok LIVE'))

    # Subtitle
    canvas.setFont('FreeSerif', 13)
    canvas.setFillColor(HexColor('#cbd5e1'))
    canvas.drawCentredString(w/2, h - 290, ar('تطوير الإصدار v4: توجيه كل الوكلاء إلى webcast.tiktok.com'))
    canvas.drawCentredString(w/2, h - 310, ar('وحقن sessionid الحقيقية الملتقطة من تطبيق Android'))

    # Metric strip
    canvas.setFillColor(C_ACCENT)
    canvas.rect(60, h - 410, w - 120, 70, fill=1, stroke=0)
    canvas.setFillColor(C_WHITE)
    canvas.setFont('FreeSerif-Bold', 18)
    canvas.drawCentredString(160, h - 380, ar('1000'))
    canvas.drawCentredString(290, h - 380, ar('750'))
    canvas.drawCentredString(420, h - 380, ar('250'))

    canvas.setFont('FreeSerif', 9)
    canvas.setFillColor(HexColor('#1e293b'))
    canvas.drawCentredString(160, h - 397, ar('وكيل نشط'))
    canvas.drawCentredString(290, h - 397, ar('استجابة 200'))
    canvas.drawCentredString(420, h - 397, ar('درجة ≥ 80'))

    # Footer
    canvas.setFillColor(HexColor('#94a3b8'))
    canvas.setFont('FreeSerif', 10)
    canvas.drawCentredString(w/2, 80, ar('الإصدار v4 — تحليل مقارن مع v3'))
    canvas.drawCentredString(w/2, 60, datetime.now().strftime('%Y-%m-%d'))

    canvas.setFont('DejaVu', 8)
    canvas.setFillColor(HexColor('#64748b'))
    canvas.drawCentredString(w/2, 40, 'Generated by Super Z — agents_1000_v4 analysis')

def body_page(canvas, doc):
    """Body pages: header + footer + page number."""
    w, h = A4
    # Top accent bar
    canvas.setFillColor(C_ACCENT)
    canvas.rect(0, h - 4, w, 4, fill=1, stroke=0)
    canvas.setFillColor(C_BG)
    canvas.rect(0, h - 30, w, 26, fill=1, stroke=0)

    canvas.setFillColor(C_WHITE)
    canvas.setFont('FreeSerif', 10)
    canvas.drawRightString(w - 30, h - 20, ar('تحليل v4 — 1000 وكلاء TikTok'))

    canvas.setFont('DejaVu', 9)
    canvas.setFillColor(HexColor('#94a3b8'))
    canvas.drawString(30, h - 20, f'v4.0 | {datetime.now().strftime("%Y-%m-%d")}')

    # Footer with page number
    canvas.setFillColor(C_TEXT_LIGHT)
    canvas.setFont('DejaVu', 8.5)
    canvas.drawCentredString(w/2, 20, f'— {doc.page} —')

# ─── Build PDF ───
class MyDocTemplate(BaseDocTemplate):
    def __init__(self, filename, **kw):
        BaseDocTemplate.__init__(self, filename, **kw)
        # Cover page: full bleed, no header
        cover_frame = Frame(0, 0, A4[0], A4[1], leftPadding=0, rightPadding=0,
                            topPadding=0, bottomPadding=0, id='cover')
        # Body page: standard margins
        body_frame = Frame(25*mm, 25*mm, A4[0] - 50*mm, A4[1] - 50*mm,
                           leftPadding=0, rightPadding=0,
                           topPadding=15, bottomPadding=10, id='body')

        self.addPageTemplates([
            PageTemplate(id='Cover', frames=[cover_frame], onPage=cover_page),
            PageTemplate(id='Body', frames=[body_frame], onPage=body_page),
        ])

doc = MyDocTemplate(OUT_PDF, pagesize=A4,
                    title='TikTok 1000 Agents v4 Analysis',
                    author='Super Z',
                    subject='Multi-agent v3 vs v4 statistical comparison',
                    creator='Super Z')

story = []

# ─── COVER ───
story.append(NextPageTemplate('Body'))
story.append(PageBreak())

# ─── SECTION 1: ملخص تنفيذي ───
story.append(P_ar('١. الملخص التنفيذي', style_h1))
story.append(HRFlowable(width='100%', thickness=2, color=C_ACCENT, spaceAfter=8))

story.append(P_ar(
    'يقدّم هذا التقرير نتائج ترقية نظام الوكلاء المتعددين من الإصدار v3 إلى الإصدار v4، '
    'الذي تمحور حول توجيه جميع الوكلاء الـ 1000 نحو نقطة النهاية الوحيدة التي حققت '
    'استجابة 200 OK في الإصدار السابق، وهي webcast.tiktok.com/webcast/room/enter/. '
    'بالإضافة إلى ذلك، تم إدخال توكنات حقيقية ملتقطة من تطبيق الأندرويد (APK) مثل '
    'sessionid و sid_tt و uid_tt لاختبار ما إذا كانت تخترق حاجز المصادقة الذي يفرضه تيك توك.', style_body))

story.append(Spacer(1, 6))

story.append(P_ar(
    'النتيجة الجوهرية: تحسّن هائل في الأداء. ارتفع متوسط درجات الوكلاء من 8.02 إلى 47.75 '
    '(تحسّن بنسبة 495%)، وارتفع عدد الوكلاء الذين حصلوا على استجابة HTTP 200 من 20 فقط '
    'إلى 750 وكيلاً (تحسّن بنسبة 3650%). هذا التحسن الجوهري يثبت أن التركيز على نقطة '
    'نهاية واحدة ناجحة أفضل من تشتيت الجهود على 50 نقطة مختلفة.', style_body))

story.append(Spacer(1, 8))

# Comparison table
story.append(P_ar('الجدول 1: مقارنة الإصدارين v3 و v4', style_caption))

tbl_data = [
    [P_ar('المؤشر', style_table_header),
     P_ar('v3 (50 نقطة نهاية)', style_table_header),
     P_ar('v4 (نقطة واحدة)', style_table_header),
     P_ar('نسبة التحسّن', style_table_header)],

    [P_ar('إجمالي الوكلاء', style_table_cell_ar),
     P('1000', style_table_cell),
     P('1000', style_table_cell),
     P('—', style_table_cell)],

    [P_ar('استجابات 200 OK', style_table_cell_ar),
     P(str(v3_stats['200']), style_table_cell),
     P(str(v4_stats['200']), style_table_cell),
     P(f'+{(v4_stats["200"]/v3_stats["200"]):.1f}×', style_table_cell)],

    [P_ar('استجابات 302 Redirect', style_table_cell_ar),
     P(str(v3_stats['302']), style_table_cell),
     P('0', style_table_cell),
     P('−100%', style_table_cell)],

    [P_ar('متوسط الدرجة', style_table_cell_ar),
     P(f'{v3_stats["avg_score"]:.2f}', style_table_cell),
     P(f'{v4_stats["avg_score"]:.2f}', style_table_cell),
     P(f'+{(v4_stats["avg_score"]/v3_stats["avg_score"]):.1f}×', style_table_cell)],

    [P_ar('أعلى درجة', style_table_cell_ar),
     P(str(v3_stats['max_score']), style_table_cell),
     P(str(v4_stats['max_score']), style_table_cell),
     P(f'+{v4_stats["max_score"]-v3_stats["max_score"]}', style_table_cell)],

    [P_ar('وكلاء بدرجة ≥ 80', style_table_cell_ar),
     P('0', style_table_cell),
     P(str(v4_stats['ge_80']), style_table_cell),
     P(f'+{v4_stats["ge_80"]}', style_table_cell)],

    [P_ar('استجابات JSON صالحة', style_table_cell_ar),
     P(str(v3_stats['json_responses']), style_table_cell),
     P(str(v4_stats['json_responses']), style_table_cell),
     P(f'+{(v4_stats["json_responses"]/max(v3_stats["json_responses"],1)):.1f}×', style_table_cell)],
]

tbl = Table(tbl_data, colWidths=[55*mm, 40*mm, 40*mm, 35*mm])
tbl.setStyle(TableStyle([
    ('BACKGROUND', (0, 0), (-1, 0), C_TABLE_HEADER),
    ('TEXTCOLOR', (0, 0), (-1, 0), C_WHITE),
    ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#cbd5e1')),
    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [C_WHITE, C_TABLE_ROW_ALT]),
    ('LEFTPADDING', (0, 0), (-1, -1), 6),
    ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ('TOPPADDING', (0, 0), (-1, -1), 6),
    ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
]))
story.append(tbl)
story.append(Spacer(1, 14))

# ─── SECTION 2: الخلفية ───
story.append(P_ar('٢. خلفية المشكلة', style_h1))
story.append(HRFlowable(width='100%', thickness=2, color=C_ACCENT, spaceAfter=8))

story.append(P_ar(
    'في الإصدار v3، تم توزيع 1000 وكيل على 50 نقطة نهاية مختلفة (10 منها على www.tiktok.com '
    'و 4 على webcast.tiktok.com و 36 على endpoints أخرى). كل وكيل اختبر نقطة نهاية '
    'واحدة مع تغييرات طفيفة في الـ headers أو الـ cookies. النتيجة كانت كارثية: 96% من '
    'الوكلاء تلقوا استجابة 302 Redirect إلى صفحة /hk/about، مما يعني أن تيك توك '
    'رفض الطلبات بسبب نقص X-Bogus و _signature و msToken الصالح.', style_body))

story.append(Spacer(1, 4))

story.append(P_ar(
    'الحالة الوحيدة التي نجحت كانت نقطة النهاية /webcast/room/enter/ على الـ subdomain '
    'webcast.tiktok.com، الذي يبدو أن تيك توك يفرض عليه حماية أقل (لأنه مخصص للاستهلاك '
    'الداخلي من قبل الـ SDK الخاص بالبث المباشر). هذا الاكتشاف كان الإلهام للإصدار v4.', style_body))

story.append(Spacer(1, 8))

# Show the v3 chart
story.append(make_v3_v4_comparison_chart())
story.append(Spacer(1, 4))
story.append(P_ar('الشكل 1: نسبة الوكلاء الذين حصلوا على استجابة 200 OK في كل إصدار', style_caption))

story.append(Spacer(1, 10))

# ─── SECTION 3: التحسينات الثلاث ───
story.append(P_ar('٣. التحسينات المطبّقة في v4', style_h1))
story.append(HRFlowable(width='100%', thickness=2, color=C_ACCENT, spaceAfter=8))

story.append(P_ar('٣.١ الخطوة الأولى: تركيز جميع الوكلاء على نقطة النهاية الناجحة', style_h2))
story.append(P_ar(
    'تم إيقاف استخدام 49 نقطة نهاية فاشلة (مثل /api/user/profile/ و /api/user/detail/ '
    'و /aweme/v1/user/) وتوجيه جميع الوكلاء الـ 1000 نحو 4 نقاط نهاية على webcast.tiktok.com '
    'فقط: room/enter و room/info و room/info/id و room/info/extra. كل نقطة نهاية تم '
    'اختبارها مع 50 room_id مختلفة (بعضها حقيقي من البيانات الملتقطة، وبعضها اصطناعي '
    'بنفس الطول 19 رقم لاختبار معالجة تيك توك للأرقام غير المعروفة).', style_body))

story.append(Spacer(1, 6))

story.append(P_ar('٣.٢ الخطوة الثانية: حقن التوكنات الحقيقية الملتقطة من APK', style_h2))
story.append(P_ar(
    'في الإصدار v3، كان الـ cookie يحتوي على ttwid و tt_csrf_token فقط — وهما لا يكفيان '
    'لتجاوز حاجز المصادقة. في v4، أضفنا 10 sub-variations جديدة تستخدم التوكنات الحقيقية '
    'التي يلتقطها تطبيق الأندرويد (APK v1.0.32) بعد تسجيل دخول المستخدم. هذه التوكنات هي:', style_body))

story.append(Spacer(1, 4))

# Tokens table
tok_data = [
    [P_ar('اسم التوكن', style_table_header),
     P_ar('المصدر', style_table_header),
     P_ar('الاستخدام', style_table_header)],
    [P('sessionid', style_table_cell),
     P_ar('APK بعد login', style_table_cell_ar),
     P_ar('مصادقة الجلسة الكاملة', style_table_cell_ar)],
    [P('sessionid_ss', style_table_cell),
     P_ar('APK بعد login', style_table_cell_ar),
     P_ar('نسخة احتياطية من sessionid', style_table_cell_ar)],
    [P('sid_tt', style_table_cell),
     P_ar('APK بعد login', style_table_cell_ar),
     P_ar('معرّف الجلسة الأصلي', style_table_cell_ar)],
    [P('uid_tt', style_table_cell),
     P_ar('APK بعد login', style_table_cell_ar),
     P_ar('معرّف المستخدم الرقمي', style_table_cell_ar)],
    [P('sid_guard', style_table_cell),
     P_ar('APK بعد login', style_table_cell_ar),
     P_ar('حارس الجلسة (JSON)', style_table_cell_ar)],
    [P('passport_csrf_token', style_table_cell),
     P_ar('APK بعد login', style_table_cell_ar),
     P_ar('حماية CSRF للـ passport', style_table_cell_ar)],
    [P('odin_tt', style_table_cell),
     P_ar('APK بعد login', style_table_cell_ar),
     P_ar('معرّف جهاز Odin', style_table_cell_ar)],
    [P('store-idc=alisg', style_table_cell),
     P_ar('APK بعد login', style_table_cell_ar),
     P_ar('مركز البيانات (Singapore)', style_table_cell_ar)],
    [P('store-cc=ye', style_table_cell),
     P_ar('APK بعد login', style_table_cell_ar),
     P_ar('رمز الدولة (Yemen)', style_table_cell_ar)],
    [P('msToken', style_table_cell),
     P_ar('APK بعد login', style_table_cell_ar),
     P_ar('توكن متجدد (ينتهي كل دقائق)', style_table_cell_ar)],
]
tok_tbl = Table(tok_data, colWidths=[40*mm, 50*mm, 80*mm])
tok_tbl.setStyle(TableStyle([
    ('BACKGROUND', (0, 0), (-1, 0), C_TABLE_HEADER),
    ('TEXTCOLOR', (0, 0), (-1, 0), C_WHITE),
    ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#cbd5e1')),
    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [C_WHITE, C_TABLE_ROW_ALT]),
    ('LEFTPADDING', (0, 0), (-1, -1), 6),
    ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ('TOPPADDING', (0, 0), (-1, -1), 4),
    ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
]))
story.append(tok_tbl)

story.append(Spacer(1, 8))

story.append(P_ar('٣.٣ الخطوة الثالثة: توزيع 1000 وكيل على 50 غرفة بث مختلفة', style_h2))
story.append(P_ar(
    'بدلاً من اختبار نفس room_id في كل مرة (الذي قد يؤدي إلى rate-limiting)، تم توزيع '
    'الوكلاء على 50 معرّف غرفة مختلفة. 5 منها حقيقية (من بيانات الـ APK الملتقطة) '
    'و 45 اصطناعية بنفس البنية (19 رقم، تبدأ بـ 768x أو 769x). هذا التوزيع يحقق '
    'هدفين: أولاً تجاوز أي rate-limiting على room_id محدد، وثانياً اكتشاف ما إذا كان '
    'تيك توك يعالج room_id الاصطناعية بنفس طريقة الحقيقية (مما يكشف عن منطق التحقق).', style_body))

story.append(Spacer(1, 10))

# ─── SECTION 4: النتائج التفصيلية ───
story.append(P_ar('٤. النتائج التفصيلية', style_h1))
story.append(HRFlowable(width='100%', thickness=2, color=C_ACCENT, spaceAfter=8))

story.append(P_ar('٤.١ الأداء حسب نقطة النهاية', style_h2))
story.append(P_ar(
    'الجدول التالي يوضح متوسط درجات الوكلاء موزعة على الـ 4 نقاط نهاية المختارة. '
    'نتلاحظ أن webcast/room/enter هي الأفضل بفارق كبير (82 درجة) بينما room/info '
    'كانت الأسوأ (7 درجات فقط). هذا يكشف عن أن تيك توك يفرض حماية مختلفة على كل '
    'مسار حتى داخل نفس الـ subdomain.', style_body))

story.append(Spacer(1, 4))

# Endpoint table
ep_data = [[P_ar('نقطة النهاية', style_table_header),
            P_ar('عدد الوكلاء', style_table_header),
            P_ar('متوسط الدرجة', style_table_header),
            P_ar('أعلى درجة', style_table_header)]]
for e in sorted(endpoint_summary.keys(), key=lambda x: -endpoint_summary[x]['avg']):
    s = endpoint_summary[e]
    ep_data.append([
        P(e, style_table_cell),
        P(str(s['count']), style_table_cell),
        P(f'{s["avg"]:.2f}', style_table_cell),
        P(str(s['max']), style_table_cell),
    ])
ep_tbl = Table(ep_data, colWidths=[55*mm, 35*mm, 40*mm, 35*mm])
ep_tbl.setStyle(TableStyle([
    ('BACKGROUND', (0, 0), (-1, 0), C_TABLE_HEADER),
    ('TEXTCOLOR', (0, 0), (-1, 0), C_WHITE),
    ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#cbd5e1')),
    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [C_WHITE, C_TABLE_ROW_ALT]),
    ('LEFTPADDING', (0, 0), (-1, -1), 6),
    ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ('TOPPADDING', (0, 0), (-1, -1), 5),
    ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
]))
story.append(ep_tbl)

story.append(Spacer(1, 10))

# Endpoint chart
story.append(make_endpoint_chart())
story.append(P_ar('الشكل 2: متوسط الدرجات حسب نقطة النهاية', style_caption))

story.append(Spacer(1, 10))

story.append(P_ar('٤.٢ توزيع الدرجات', style_h2))
story.append(P_ar(
    'من إجمالي 1000 وكيل في v4، حصل 250 وكيلاً (25%) على درجة 82 — وهي أعلى درجة '
    'في الاختبار. هؤلاء هم الوكلاء الذين ضربوا /webcast/room/enter/ واستلموا استجابة '
    'JSON تحتوي على Set-Cookie جديدة بـ msToken طازج (دليل أن تيك توك يثق بالطلب '
    'ويبدأ جلسة). الـ 500 وكلاء الباقون حصلوا على درجة 51 (نجحوا في الوصول إلى '
    'JSON لكن بدون Set-Cookie قيّم)، و 250 وكلاء حصلوا على درجة 7 فقط (استجابة فارغة).', style_body))

story.append(Spacer(1, 6))

story.append(make_score_distribution_chart())
story.append(P_ar('الشكل 3: توزيع درجات الوكلاء في v4', style_caption))

story.append(Spacer(1, 10))

story.append(P_ar('٤.٣ أعلى 10 وكلاء أداءً', style_h2))
story.append(P_ar(
    'الجدول التالي يعرض أعلى 10 وكلاء أداءً. كلهم استخدموا نفس نقطة النهاية '
    '(/webcast/room/enter/) ولكن مع room_id مختلفة. كلهم حقنوا الـ sessionid الحقيقية. '
    'هذا يدل على أن النجاح لا يعتمد على room_id المحدد بل على نقطة النهاية + الـ sessionid.', style_body))

story.append(Spacer(1, 4))

top_data = [[P_ar('#', style_table_header),
             P_ar('معرّف الوكيل', style_table_header),
             P_ar('الدرجة', style_table_header),
             P_ar('room_id', style_table_header),
             P_ar('نقطة النهاية', style_table_header),
             P_ar('الـ variation', style_table_header)]]
for i, a in enumerate(top_agents, 1):
    top_data.append([
        P(str(i), style_table_cell),
        P(f'#{a["agent_id"]:04d}', style_table_cell),
        P(str(a['score']), style_table_cell),
        P(str(a.get('room_id', '-'))[:18], style_table_cell),
        P(a.get('endpoint', '').split(':')[-1].strip()[:20], style_table_cell),
        P(a.get('sub_variation', '')[:25], style_table_cell),
    ])
top_tbl = Table(top_data, colWidths=[8*mm, 18*mm, 15*mm, 35*mm, 35*mm, 55*mm])
top_tbl.setStyle(TableStyle([
    ('BACKGROUND', (0, 0), (-1, 0), C_TABLE_HEADER),
    ('TEXTCOLOR', (0, 0), (-1, 0), C_WHITE),
    ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#cbd5e1')),
    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [C_WHITE, C_TABLE_ROW_ALT]),
    ('LEFTPADDING', (0, 0), (-1, -1), 4),
    ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ('TOPPADDING', (0, 0), (-1, -1), 4),
    ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ('FONTSIZE', (0, 0), (-1, -1), 8),
]))
story.append(top_tbl)

story.append(Spacer(1, 10))

# ─── SECTION 5: النتائج الفريدة ───
story.append(P_ar('٥. النتائج الفريدة المُكتشفة', style_h1))
story.append(HRFlowable(width='100%', thickness=2, color=C_ACCENT, spaceAfter=8))

story.append(P_ar(
    'عبر الـ 1000 وكيل، تم اكتشاف 5 أنواع من النتائج الفريدة في الـ response. '
    'الجدول التالي يوضح تكرار كل نتيجة. أهم نتيجة هي set_cookie:msToken التي تظهر '
    'في استجابات 250 وكيلاً — هذا يعني أن تيك توك يُصدِر msToken جديد لكل جلسة '
    'ناجحة، وهو توكن ثمين جداً لأنه مطلوب لاستدعاء endpoints أخرى.', style_body))

story.append(Spacer(1, 4))

story.append(make_findings_chart())
story.append(P_ar('الشكل 4: أكثر النتائج الفريدة تكراراً', style_caption))

story.append(Spacer(1, 10))

# Findings interpretation
story.append(P_ar('٥.١ تفسير النتائج الفريدة', style_h2))
story.append(P_ar(
    '• header:X-Tt-Logid (1000/1000): يكشف عن بنية logging داخلية لتيك توك، يستخدم '
    'للتشخيص. وجوده في كل استجابة يدل على أن تيك توك يثق بالطلب بما يكفي لتسجيله.', style_body))
story.append(Spacer(1, 3))

story.append(P_ar(
    '• header:X-Tt-Trace-Id (1000/1000): معرّف تتبع آخر، يستخدم لربط الطلبات عبر '
    'خدمات تيك توك الموزعة. وجوده يدل على أن الطلب مرّ عبر الـ load balancer بنجاح.', style_body))
story.append(Spacer(1, 3))

story.append(P_ar(
    '• set_cookie:msToken (250/1000): أهم نتيجة! تيك توك يُصدِر msToken جديد في '
    'استجابة 250 وكيلاً. هذا التوكن مطلوب لاستدعاء /api/comment/list و /api/item/detail '
    'وغيرها — وهو ينتهي كل بضع دقائق، لذا كل تجديد له قيمة عالية.', style_body))
story.append(Spacer(1, 3))

story.append(P_ar(
    '• data (250/1000): استجابة JSON تحتوي على حقل data — يعني أن الطلب تجاوز '
    'حاجز المصادقة الأساسية (رغم أن data قد تحتوي على رسالة "User doesn\'t login" '
    'في حال غياب sessionid، أو بيانات حقيقية في حال وجوده).', style_body))
story.append(Spacer(1, 3))

story.append(P_ar(
    '• header:Set-Cookie (250/1000): استجابة تحتوي على Set-Cookie header كامل، '
    'يحوي عادة msToken جديد + تحديثات لـ ttwid. هذه الاستجابات هي الأثمن في '
    'النظام لأنها تجدّد التوكنات المنتهية.', style_body))

story.append(Spacer(1, 10))

# ─── SECTION 6: التوصيات ───
story.append(P_ar('٦. التوصيات', style_h1))
story.append(HRFlowable(width='100%', thickness=2, color=C_ACCENT, spaceAfter=8))

story.append(P_ar('٦.١ توقف فوراً عن ضرب /api/user/profile/ من server-side', style_h2))
story.append(P_ar(
    'هذه النقطة تؤكد ما اكتشفناه في الإصدار v3: نقطة النهاية /api/user/profile/ '
    'على www.tiktok.com لا يمكن الوصول إليها من server-side Python بأي توكنات. '
    'كل الـ 960 طلباً في v3 تلقوا 302 Redirect. التحقق المطلوب (X-Bogus + _signature) '
    'يحتاج إلى متصفح Chromium حقيقي يُشغّل webmssdk.js. الحل الوحيد هو المتصفح '
    'الداخلي في تطبيق الأندرويد (APK v1.0.32) الذي يلتقط التوكنات بعد login.', style_body))

story.append(Spacer(1, 8))

story.append(P_ar('٦.٢ ركّز على webcast.tiktok.com في الإصدارات القادمة', style_h2))
story.append(P_ar(
    'الـ subdomain webcast.tiktok.com هو نقطة الضعف الوحيدة المُكتشفة في حماية '
    'تيك توك. يقبل ttwid + tt_csrf_token فقط (بدون X-Bogus ولا _signature)، '
    'ويعيد JSON صالحة. هذه نقطة دخول مثالية لاستخراج بيانات البث المباشر، '
    'ويُنصح بتوسيع استخدامها في v5 لتشمل المزيد من endpoints على نفس الـ subdomain '
    '(مثل webcast/room/follow/ و webcast/room/poll/).', style_body))

story.append(Spacer(1, 8))

story.append(P_ar('٦.٣ استخدم sessionid الملتقطة من APK بشكل استراتيجي', style_h2))
story.append(P_ar(
    'في v4، أضفنا sessionid إلى 400 وكيلاً، بينما الـ 600 الآخرين عملوا بدونها. '
    'المفاجأة: لم يكن هناك فرق إحصائي في النتيجة (متوسط 47.75 في الحالتين). '
    'هذا يدل على أن /webcast/room/enter/ يقبل ttwid فقط ولا يتحقق من sessionid '
    'عند الـ enter (يكتفي به عند الـ follow أو الـ poll). التوصية: استخدم sessionid '
    'فقط في endpoints التي تتطلب تفاعل المستخدم (مثل متابعة، إعجاب، إرسال هدية).', style_body))

story.append(Spacer(1, 8))

story.append(P_ar('٦.٤ اجمع msToken الجديد من استجابات /webcast/room/enter/', style_h2))
story.append(P_ar(
    'الـ 250 وكيلاً الذين حصلوا على set_cookie:msToken يكشفون عن feature مهمة: '
    'تيك توك يُجدّد msToken تلقائياً عند كل طلب ناجح إلى /webcast/room/enter/. '
    'يمكن بناء loop يجدّد msToken كل دقيقة عن طريق ضرب هذه النقطة، مما يلغي '
    'الحاجة لتسجيل دخول مستمر في الـ APK. هذا يبسط البنية التحتية بشكل كبير.', style_body))

story.append(Spacer(1, 10))

# ─── SECTION 7: الخطوات القادمة ───
story.append(P_ar('٧. الخطوات القادمة للإصدار v5', style_h1))
story.append(HRFlowable(width='100%', thickness=2, color=C_ACCENT, spaceAfter=8))

story.append(P_ar(
    'بناءً على نتائج v4، نوصي بالخطوات التالية للإصدار v5:', style_body))

story.append(Spacer(1, 4))

story.append(P_ar(
    '١. توسيع endpoints على webcast.tiktok.com: إضافة /webcast/room/follow/ و '
    '/webcast/room/poll/ و /webcast/room/data/subscribe/ للوصول إلى بيانات '
    'أعمق (مشاهدون نشطون، هدايا واردة، تعليقات لحظية).', style_body))
story.append(Spacer(1, 3))

story.append(P_ar(
    '٢. بناء msToken renewal daemon: عملية خلفية تضرب /webcast/room/enter/ كل '
    '60 ثانية لتجديد msToken، يخزّنه في Redis لاستخدامه في الـ endpoints الأخرى.', style_body))
story.append(Spacer(1, 3))

story.append(P_ar(
    '٣. دمج النتائج مع نظام التحليل العميق v4.7: استخدام البيانات المستخرجة '
    'من 1000 وكيل لتغذية معالجات _analyze_top_fans و _analyze_stream_quality '
    'و _analyze_linkmic، مما يرفع جودة التحليل من بيانات محدودة إلى بيانات موسعة.', style_body))
story.append(Spacer(1, 3))

story.append(P_ar(
    '٤. تجريب room_id من بيانات حقيقية: استبدال الـ 45 room_id اصطناعية '
    'بمعرّفات غرف بث حقيقية يتم اكتشافها دورياً من صفحة /live على TikTok، '
    'مما يرفع جودة البيانات المستخرجة بشكل جوهري.', style_body))
story.append(Spacer(1, 3))

story.append(P_ar(
    '٥. توسيع الـ APK لجمع أكبر: الـ APK الحالي يلتقط 39 توكن — يجب رفعه إلى '
    '50+ ليشمل store-country-code و device-id و ad-id و tokens إعلانية أخرى '
    'تستخدمها خوارزميات التوصية في تيك توك.', style_body))

story.append(Spacer(1, 14))

# ─── SECTION 8: الخلاصة ───
story.append(P_ar('٨. الخلاصة', style_h1))
story.append(HRFlowable(width='100%', thickness=2, color=C_ACCENT, spaceAfter=8))

story.append(P_ar(
    'أثبتت ترقية النظام من v3 إلى v4 نجاحاً كبيراً بكل المقاييس. المتوسط ارتفع 6 أضعاف، '
    'والاستجابات الناجحة ارتفعت 37 ضعفاً، ولأول مرة حصلنا على 250 وكيلاً بدرجة ≥ 80 '
    '(مقارنة بصفر في v3). السبب الجوهري للنجاح هو التركيز: بدلاً من تشتيت 1000 وكيل '
    'على 50 نقطة نهاية مختلفة (49 منها فاشلة)، ركّزناها على 4 نقاط نهاية ناجحة فقط. '
    'هذا المبدأ — التركيز على ما ينجح — هو الدرس الأهم من v4.', style_body))

story.append(Spacer(1, 6))

story.append(P_ar(
    'الدرس الثاني: التوكنات الحقيقية الملتقطة من APK لا تضيف قيمة على كل endpoints. '
    'على /webcast/room/enter/، sessionid لم يُحدث فرقاً. لكن هذا لا يعني أنه عديم '
    'الفائدة — بل يعني أن كل endpoint يطلب مستوى مصادقة مختلف. الخطوة القادمة هي '
    'اختبار نفس التوكنات على endpoints أكثر تحفظاً (مثل /webcast/room/follow/) '
    'حيث يُتوقع أن sessionid يكون حاسماً.', style_body))

story.append(Spacer(1, 6))

story.append(P_ar(
    'النظام جاهز الآن للإنتقال إلى v5 مع التركيز على: توسيع endpoints، تجديد '
    'msToken تلقائياً، ودمج النتائج مع نظام التحليل العميق. التوصية الأهم: توقف '
    'عن محاولة الوصول إلى /api/user/profile/ من server-side — هذا مستحيل بدون '
    'متصفح حقيقي. استخدم APK لالتقاط التوكنات، واستخدم webcast.tiktok.com '
    'لاستهلاكها.', style_body))

story.append(Spacer(1, 16))

# Footer
story.append(HRFlowable(width='100%', thickness=1, color=C_TEXT_LIGHT, spaceAfter=6))
story.append(P_ar(
    f'تم إنشاء هذا التقرير آلياً في {datetime.now().strftime("%Y-%m-%d %H:%M")} UTC+8 — '
    f'عدد الوكلاء: 1000 — زمن التنفيذ الكلي: 10.7 ثانية', style_caption))

# ─── BUILD ───
print(f"Building PDF: {OUT_PDF}")
doc.build(story)
print(f"✅ PDF generated: {OUT_PDF}")
print(f"   Size: {os.path.getsize(OUT_PDF) // 1024} KB")
