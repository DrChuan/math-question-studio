from dataclasses import dataclass

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout
from PySide6.QtCore import Qt, Signal
from problem_manager.latex_support import SYMBOL_MAP


@dataclass(frozen=True)
class MathTool:
    label: str
    code: str
    default: str = "x"


ALL_MATH_TOOLS = [
    MathTool("向量", r"\v{__SEL__}", "a"),
    MathTool("分段函数", "\\begin{cases}__SEL__, & x\\geq 0\\\\\n-x, & x<0\\end{cases}"),
    MathTool("粗体", r"\boldsymbol{__SEL__}"),
    MathTool("分式", r"\dfrac{__SEL__}{b}", "a"),
    MathTool("根式", r"\sqrt{__SEL__}"),
    MathTool("上下标", r"__SEL___{n}^{2}", "a"),
    MathTool("自适应括号", r"\left(__SEL__\right)"),
    MathTool("绝对值", r"\left|__SEL__\right|"),
    MathTool("联立方程", "\\begin{cases}__SEL__+y=1\\\\\nx-y=0\\end{cases}"),
    MathTool("求和", r"\sum_{k=1}^{n} __SEL__", "k"),
    MathTool("组合数", r"\mathrm{C}_{n}^{__SEL__}", "m"),
    MathTool("矩阵", "\\begin{pmatrix}__SEL__ & b\\\\\nc & d\\end{pmatrix}", "a"),
]
MATH_TOOLS = [tool for tool in ALL_MATH_TOOLS if tool.label not in {"分式", "根式", "上下标", "组合数", "矩阵"}]


def inside_math(text: str, position: int) -> bool:
    delimiter = None
    index = 0
    while index < position:
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == "$":
            token = "$$" if text.startswith("$$", index) else "$"
            if delimiter is None:
                delimiter = token
            elif delimiter == token:
                delimiter = None
            index += len(token)
            continue
        index += 1
    return delimiter is not None


def insert_math_tool(editor, tool: MathTool) -> None:
    cursor = editor.textCursor()
    start = cursor.selectionStart()
    selected = cursor.selectedText().replace("\u2029", "\n")
    selected = selected[1:-1] if selected.startswith("$") and selected.endswith("$") else selected
    value = selected or tool.default
    code = tool.code.replace("__SEL__", value)
    text = editor.toPlainText()
    # QTextCursor counts UTF-16 code units, while Python slices use code points.
    source_start = len(text.encode("utf-16-le")[:start * 2].decode("utf-16-le", errors="ignore"))
    prefix = "" if inside_math(text, source_start) else "$"
    cursor.beginEditBlock()
    cursor.insertText(prefix + code + prefix)
    if "__SEL__" in tool.code:
        offset = len((prefix + tool.code.split("__SEL__", 1)[0]).encode("utf-16-le")) // 2
        cursor.setPosition(start + offset)
        cursor.setPosition(start + offset + len(value.encode("utf-16-le")) // 2, QTextCursor.KeepAnchor)
    cursor.endEditBlock()
    editor.setTextCursor(cursor)
    editor.setFocus()
    editor.ensureCursorVisible()


def _catalogue():
    groups = {
        "输入与结构": [
            ("行内公式", "$x^2+1$"), ("填空横线", "@"), ("上标、幂", "x^{n+1}"), ("下标", "a_{n+1}"),
            ("分式", r"\dfrac{a}{b}"), ("平方根", r"\sqrt{x}"), ("n 次根", r"\sqrt[n]{x}"),
            ("自适应圆括号", r"\left(\dfrac{a}{b}\right)"), ("方括号", r"\left[a,b\right]"),
            ("花括号", r"\left\{a,b\right\}"), ("绝对值", r"\left|x\right|"), ("范数、向量模", r"\left\|a\right\|"),
            ("分段函数", ALL_MATH_TOOLS[1].code.replace("__SEL__", "x")), ("联立方程", ALL_MATH_TOOLS[8].code.replace("__SEL__", "x")),
            ("矩阵", ALL_MATH_TOOLS[11].code.replace("__SEL__", "a")), ("多行对齐", "\\begin{aligned}a&=b+c\\\\\n&=d\\end{aligned}"),
            ("正体文字", r"\mathrm{AB}"), ("公式内说明文字", r"\text{if }x>0"), ("粗斜体", r"\boldsymbol{a}"),
            ("撇号", "f'(x)"), ("空格", r"\quad"), ("小空格", r"\,"), ("去掉间距", r"\!"),
        ],
        "运算与关系": [
            ("加减", r"\pm"), ("减加", r"\mp"), ("乘号", r"\times"), ("除号", r"\div"), ("点乘", r"\cdot"),
            ("不等于", r"\ne"), ("小于等于", r"\leq"), ("大于等于", r"\geq"), ("约等于", r"\approx"),
            ("恒等于", r"\equiv"), ("正比于", r"\propto"), ("无穷大", r"\infty"), ("阶乘", "n!"),
            ("因为", r"\because"), ("所以", r"\therefore"), ("圆、圆心", r"\odot O"),
            ("横向省略号", r"\cdots"), ("底部省略号", r"\ldots"), ("纵向省略号", r"\vdots"), ("斜向省略号", r"\ddots"),
            ("求和", r"\sum_{k=1}^{n}a_k"), ("连乘", r"\prod_{k=1}^{n}a_k"), ("二项式系数", r"\binom{n}{m}"),
        ],
        "集合与逻辑": [
            ("属于", r"\in"), ("不属于", r"\notin"), ("包含于", r"\subseteq"), ("真包含于", r"\subsetneq"),
            ("包含", r"\supseteq"), ("交集", r"\cap"), ("并集", r"\cup"), ("差集", r"\setminus"),
            ("空集", r"\varnothing"), ("全集补集", r"\complement_U A"), ("自然数集", r"\mathbb{N}"),
            ("正整数集", r"\mathbb{N}^{*}"), ("整数集", r"\mathbb{Z}"), ("有理数集", r"\mathbb{Q}"),
            ("实数集", r"\mathbb{R}"), ("复数集", r"\mathbb{C}"), ("存在", r"\exists"), ("任意、全称", r"\forall"),
            ("推出、充分条件", r"\Rightarrow"), ("被推出、必要条件", r"\Leftarrow"), ("充要条件、等价", r"\Leftrightarrow"),
            ("否定", r"\neg p"), ("且、合取", r"p\land q"), ("或、析取", r"p\lor q"),
            ("集合描述法", r"\{x\mid x>0\}"), ("开区间", "(a,b)"), ("闭区间", "[a,b]"), ("半开区间", "(a,b]"),
        ],
        "函数与三角": [
            ("映射", r"f:A\to B"), ("对应到", r"x\mapsto x^2"), ("对数", r"\log_a x"), ("自然对数", r"\ln x"),
            ("常用对数", r"\lg x"), ("指数", "e^x"), ("正弦", r"\sin x"), ("余弦", r"\cos x"),
            ("正切", r"\tan x"), ("余切", r"\cot x"), ("正弦平方", r"\sin^2 x"), ("角度符号", r"30^{\circ}"),
            ("反正弦", r"\arcsin x"), ("反余弦", r"\arccos x"), ("反正切", r"\arctan x"),
            ("最大值", r"\max\{a,b\}"), ("最小值", r"\min\{a,b\}"), ("复合函数", r"f\circ g"),
        ],
        "向量与几何": [
            ("小写向量简写", r"\v{a_1}"), ("两点向量简写", r"\v{A_1B_2}"), ("箭头向量", r"\overrightarrow{AB}"),
            ("粗体向量", r"\boldsymbol{a}"), ("带撇号向量", r"\v{A'B'}"), ("向量数量积", r"\boldsymbol{a}\cdot\boldsymbol{b}"),
            ("向量夹角", r"\left\langle\boldsymbol{a},\boldsymbol{b}\right\rangle"), ("平行", r"\parallel"), ("垂直", r"\perp"),
            ("不平行", r"\nparallel"), ("角", r"\angle ABC"), ("三角形", r"\triangle ABC"), ("相似", r"\sim"),
            ("全等", r"\cong"), ("线段", r"\overline{AB}"), ("圆弧", r"\widehat{AB}"), ("坐标", "A(x_0,y_0)"),
            ("平面", r"\alpha"), ("直线下标", "l_1"), ("平面夹角", r"\angle(\alpha,\beta)"),
        ],
        "数列与组合": [
            ("数列", r"\{a_n\}"), ("递推", "a_{n+1}=2a_n+1"), ("前 n 项和", r"S_n=\sum_{k=1}^n a_k"),
            ("组合数", r"\mathrm{C}_n^m"), ("排列数", r"\mathrm{A}_n^m"), ("阶乘商", r"\dfrac{n!}{(n-m)!}"),
            ("整除", r"a\mid b"), ("不整除", r"a\nmid b"), ("同余", r"a\equiv b\pmod{m}"),
        ],
        "概率与统计": [
            ("概率", "P(A)"), ("条件概率", r"P(A\mid B)"), ("对立事件", r"\overline{A}"),
            ("事件交", r"P(A\cap B)"), ("事件并", r"P(A\cup B)"), ("数学期望", "E(X)"),
            ("方差", "D(X)"), ("标准差", r"\sigma"), ("样本均值", r"\overline{x}"),
            ("样本方差", r"s^2=\dfrac{1}{n}\sum_{i=1}^{n}(x_i-\overline{x})^2"),
            ("估计值", r"\widehat{p}"), ("相关系数", "r"), ("二项分布", r"X\sim B(n,p)"),
            ("正态分布", r"X\sim N(\mu,\sigma^2)"), ("独立事件", "P(AB)=P(A)P(B)"),
            ("卡方统计量", r"\chi^2"),
        ],
        "导数与拓展": [
            ("一阶导数", "f'(x)"), ("二阶导数", "f''(x)"), ("导数商", r"\dfrac{\mathrm{d}y}{\mathrm{d}x}"),
            ("极限", r"\lim_{x\to 0}f(x)"), ("趋于无穷", r"x\to+\infty"), ("左极限", r"x\to a^-"),
            ("右极限", r"x\to a^+"), ("定积分", r"\int_a^b f(x)\,\mathrm{d}x"), ("不定积分", r"\int f(x)\,\mathrm{d}x"),
            ("复数共轭", r"\overline{z}"), ("复数模", "|z|"), ("取整下界", r"\lfloor x\rfloor"), ("取整上界", r"\lceil x\rceil"),
        ],
    }
    greek = "alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu xi pi varpi rho sigma tau upsilon phi varphi chi psi omega".split()
    names = dict(zip("alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi pi rho sigma tau upsilon phi chi psi omega".split(), "阿尔法 贝塔 伽马 德尔塔 艾普西龙 泽塔 伊塔 西塔 约塔 卡帕 拉姆达 缪 纽 克西 派 罗 西格玛 陶 玉普西龙 斐 卡 普西 欧米伽".split()))
    groups["希腊字母"] = [(name + " " + names.get(name, "变体"), "\\"+name) for name in greek]
    groups["大写希腊字母"] = [(name, "\\"+name) for name in "Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega".split()]
    return [(category, name + (" · " + SYMBOL_MAP[code[1:]] if code.startswith("\\") and code[1:] in SYMBOL_MAP else ""), code) for category, entries in groups.items() for name, code in entries]


LATEX_REFERENCE = _catalogue()


class LatexReferenceDialog(QDialog):
    insert_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("高中数学 LaTeX 参考")
        self.resize(860, 650)
        self.setWindowModality(Qt.ApplicationModal)
        layout = QVBoxLayout(self)
        hint = QLabel("按中文名称、分类或命令搜索；双击或点击“插入选中写法”即可放入当前编辑位置。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        search = QLineEdit()
        search.setPlaceholderText("例如：向量、分段、条件概率、theta、\\subseteq")
        search.setClearButtonEnabled(True)
        layout.addWidget(search)
        self.table = QTreeWidget()
        self.table.setHeaderLabels(["分类", "符号 / 用途", "LaTeX 写法"])
        self.table.setRootIsDecorated(False)
        self.table.setAlternatingRowColors(True)
        for category, name, code in LATEX_REFERENCE:
            item = QTreeWidgetItem([category, name, code])
            item.setToolTip(2, code)
            self.table.addTopLevelItem(item)
        self.table.setColumnWidth(0, 125)
        self.table.setColumnWidth(1, 165)
        layout.addWidget(self.table, 1)
        self.count = QLabel()
        layout.addWidget(self.count)
        row = QHBoxLayout()
        copy = QPushButton("复制代码")
        insert = QPushButton("插入选中写法")
        insert.setObjectName("primaryButton")
        close = QPushButton("关闭")
        row.addWidget(copy)
        row.addStretch()
        row.addWidget(insert)
        row.addWidget(close)
        layout.addLayout(row)
        search.textChanged.connect(self.filter_items)
        insert.clicked.connect(self.insert_current)
        self.table.itemDoubleClicked.connect(lambda *_: self.insert_current())
        copy.clicked.connect(self.copy_current)
        close.clicked.connect(self.accept)
        self.filter_items("")

    def filter_items(self, query):
        query = query.strip().casefold()
        visible = []
        for row in range(self.table.topLevelItemCount()):
            item = self.table.topLevelItem(row)
            match = query in " ".join(item.text(column) for column in range(3)).casefold()
            item.setHidden(not match)
            if match:
                visible.append(item)
        self.count.setText(f"显示 {len(visible)} 项，共 {len(LATEX_REFERENCE)} 项")
        self.table.setCurrentItem(visible[0] if visible else None)

    def insert_current(self):
        item = self.table.currentItem()
        if item and not item.isHidden():
            self.insert_requested.emit(item.text(2))
            self.accept()

    def copy_current(self):
        from PySide6.QtWidgets import QApplication
        item = self.table.currentItem()
        if item and not item.isHidden():
            QApplication.clipboard().setText(item.text(2))
