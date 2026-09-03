from __future__ import annotations

from problem_manager.models import Question, QuestionType


def build_sample_questions() -> list[Question]:
    return [
        Question(
            question_type=QuestionType.SINGLE.value,
            stem=(
                "已知函数 $f(x)=x^2-2x-3$，则方程 $f(x)=0$ 的两个实数根之和为 $x_1+x_2=?$"
            ),
            options=[
                "$-3$",
                "$2$",
                "$3$",
                "$-2$",
                "",
                "",
            ],
            answer="B",
            analysis="由韦达定理可知 $x_1+x_2=2$。",
            note="测试单选题与行间公式预览。",
            knowledge_tags=["必修第一册 / 一元二次函数、方程和不等式 / 一元二次方程"],
            method_tags=["函数与方程", "整体思想"],
        ),
        Question(
            question_type=QuestionType.MULTIPLE.value,
            stem="已知集合 $A=\\{x\\mid -1<x\\le 2\\}$，$B=\\{x\\mid x>0\\}$，下列结论正确的是",
            options=[
                "$A\\cap B=(0,2]$",
                "$A\\cup B=(-1,+\\infty)$",
                "$A\\setminus B=(-1,0]$",
                "$B\\setminus A=(2,+\\infty)$",
                "",
                "",
            ],
            answer="A、B、D",
            analysis=(
                "逐项判断：\n"
                "1. $A\\cap B=(0,2]$ 正确；\n"
                "2. $A\\cup B=(-1,+\\infty)$ 正确；\n"
                "3. $A\\setminus B=(-1,0]$ 错误，实际为 $(-1,0]$ 中需注意 $0\\notin B$，故该项可根据定义再核对；\n"
                "4. $B\\setminus A=(2,+\\infty)$ 正确。"
            ),
            note="测试多选题和集合符号。",
            knowledge_tags=["必修第一册 / 集合与常用逻辑用语 / 基本关系与运算"],
            method_tags=["分类讨论", "数形结合"],
        ),
        Question(
            question_type=QuestionType.FILL_BLANK.value,
            stem="若 $\\sin\\theta=\\frac{3}{5}$，且 $\\theta$ 为锐角，则 $\\cos\\theta=\\underline{\\quad\\quad}$。",
            answer="$\\frac{4}{5}$",
            analysis="由平方关系 $\\sin^2\\theta+\\cos^2\\theta=1$ 可得 $\\cos\\theta=\\frac{4}{5}$。",
            note="测试填空题与分式。",
            knowledge_tags=["必修第一册 / 三角函数 / 同角三角函数关系"],
            method_tags=["整体思想"],
        ),
        Question(
            question_type=QuestionType.SOLUTION.value,
            stem=(
                "已知数列 $\\{a_n\\}$ 满足 $a_1=1$，$a_{n+1}=2a_n+1$。\n"
                "1. 求 $a_2,a_3$；\n"
                "2. 猜想并证明 $a_n=2^n-1$。"
            ),
            answer=(
                "1. $a_2=3,a_3=7$。\n"
                "2. 猜想 $a_n=2^n-1$，用数学归纳法可证：\n"
                "当 $n=1$ 时成立；若 $n=k$ 时成立，即 $a_k=2^k-1$，\n"
                "则 $a_{k+1}=2a_k+1=2(2^k-1)+1=2^{k+1}-1$，故结论成立。"
            ),
            analysis="解答题测试，覆盖上下标和递推公式。",
            note="测试解答题。",
            knowledge_tags=["选择性必修第二册 / 数列 / 数列概念"],
            method_tags=["数学归纳法", "构造法"],
        ),
        Question(
            question_type=QuestionType.SINGLE.value,
            difficulty=2,
            source="题库测试",
            stem="若 $\\log_2(x-1)=3$，则 $x=$",
            options=["$4$", "$8$", "$9$", "$10$", "", ""],
            answer="C",
            analysis="由 $\\log_2(x-1)=3$ 得 $x-1=2^3=8$，故 $x=9$。",
            note="测试对数函数与选择题筛选。",
            knowledge_tags=["必修第一册 / 指数函数与对数函数 / 对数函数"],
            method_tags=["转化与化归"],
        ),
        Question(
            question_type=QuestionType.FILL_BLANK.value,
            difficulty=2,
            source="题库测试",
            stem="已知向量 $\\vec a=(1,2)$，$\\vec b=(3,-1)$，则 $\\vec a\\cdot\\vec b=$ \\underline{\\quad\\quad}。",
            answer="$1$",
            analysis="$\\vec a\\cdot\\vec b=1\\times3+2\\times(-1)=1$。",
            note="测试向量数量积。",
            knowledge_tags=["必修第二册 / 平面向量及其应用 / 数量积与应用"],
            method_tags=["整体思想"],
        ),
        Question(
            question_type=QuestionType.MULTIPLE.value,
            difficulty=3,
            source="题库测试",
            stem="从数字 $1,2,3,4$ 中任取两个不同的数字，则下列结论正确的是",
            options=[
                "基本事件总数为 $6$",
                "取到的两个数字之和为偶数的概率为 $\\dfrac{1}{3}$",
                "取到的两个数字之积为偶数的概率为 $\\dfrac{5}{6}$",
                "取到的两个数字之和大于 $5$ 的基本事件有 $2$ 个",
                "",
                "",
            ],
            answer="A、B、C",
            analysis=(
                "从 $4$ 个数字中任取 $2$ 个，共有 $C_4^2=6$ 种。\n"
                "和为偶数时同奇或同偶，共 $2$ 种，概率为 $\\dfrac{1}{3}$。\n"
                "积为奇数仅在取到 $1,3$ 时发生，故积为偶数的概率为 $\\dfrac{5}{6}$。\n"
                "和大于 $5$ 的只有 $\\{2,4\\},\\{3,4\\}$，故 D 错误。"
            ),
            note="测试多选、组合数和分式行距。",
            knowledge_tags=["必修第二册 / 概率 / 古典概型"],
            method_tags=["分类讨论", "整体思想"],
        ),
        Question(
            question_type=QuestionType.SOLUTION.value,
            difficulty=3,
            source="题库测试",
            stem="已知圆 $C$ 的圆心为 $(2,-1)$，且过点 $A(5,3)$。\n（1）求圆 $C$ 的标准方程；\n（2）判断点 $B(6,2)$ 与圆 $C$ 的位置关系。",
            answer=(
                "（1）半径 $r=|CA|=\\sqrt{(5-2)^2+[3-(-1)]^2}=5$，故圆 $C$ 的方程为 $(x-2)^2+(y+1)^2=25$。\n"
                "（2）$|CB|=\\sqrt{(6-2)^2+[2-(-1)]^2}=5$，所以点 $B$ 在圆 $C$ 上。"
            ),
            analysis="先由圆心和圆上一点求半径，再比较点到圆心的距离与半径。",
            note="测试解答题、根式和留白功能。",
            knowledge_tags=["选择性必修第一册 / 直线和圆的方程 / 圆的方程"],
            method_tags=["数形结合"],
        ),
        Question(
            question_type=QuestionType.FILL_BLANK.value,
            difficulty=4,
            source="题库测试",
            stem="在 $(1+x)^5$ 的展开式中，$x^3$ 项的系数为 \\underline{\\quad\\quad}。",
            answer="$10$",
            analysis="由二项式定理，$x^3$ 项为 $C_5^3x^3=10x^3$，系数为 $10$。",
            note="测试组合数与二项式定理。",
            knowledge_tags=["选择性必修第二册 / 计数原理 / 二项式定理"],
            method_tags=["整体思想"],
        ),
        Question(
            question_type=QuestionType.SOLUTION.value,
            difficulty=5,
            source="题库测试",
            stem="设函数 $f(x)=x^3-3x+2$。\n（1）求 $f(x)$ 的单调区间；\n（2）求 $f(x)$ 在区间 $[-2,2]$ 上的最大值和最小值。",
            answer=(
                "（1）$f'(x)=3x^2-3=3(x-1)(x+1)$。\n"
                "当 $x<-1$ 或 $x>1$ 时，$f'(x)>0$；当 $-1<x<1$ 时，$f'(x)<0$。\n"
                "故 $f(x)$ 在 $(-\\infty,-1)$、$(1,+\\infty)$ 上单调递增，在 $(-1,1)$ 上单调递减。\n"
                "（2）计算 $f(-2)=0$，$f(-1)=4$，$f(1)=0$，$f(2)=4$，故最大值为 $4$，最小值为 $0$。"
            ),
            analysis="利用导数符号判断单调性；求闭区间最值时需比较端点和驻点处的函数值。",
            note="测试导数解答题与分段答案。",
            knowledge_tags=["选择性必修第二册 / 导数及其应用 / 导数与极值最值"],
            method_tags=["导数法", "分类讨论"],
        ),
    ]
