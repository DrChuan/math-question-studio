from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import subprocess

from problem_manager.latex_support import split_math_segments
from problem_manager.math_normalizer import normalize_math_expression
from problem_manager.models import CHOICE_TYPES, OPTION_LABELS, Question
from problem_manager.image_markup import split_image_content, image_marker, image_code_settings


@dataclass
class LatexExportOptions:
    title: str = "高中数学题目"
    include_analysis: bool = False
    include_note: bool = False
    leave_solution_space: bool = False
    compile_pdf: bool = True
    question_spacing_mm: dict[str, float] = field(default_factory=dict)


@dataclass
class LatexExportResult:
    tex_path: Path
    pdf_path: Path | None


class LatexExportError(RuntimeError):
    pass


def export_questions_to_latex_bundle(
    questions: list[Question],
    tex_path: str | Path,
    options: LatexExportOptions | None = None,
) -> LatexExportResult:
    options = options or LatexExportOptions()
    target = Path(tex_path)
    if target.suffix.lower() != ".tex":
        target = target.with_suffix(".tex")
    target.parent.mkdir(parents=True, exist_ok=True)

    source = build_latex_document(questions, options)
    target.write_text(source, encoding="utf-8")

    pdf_path = target.with_suffix(".pdf") if options.compile_pdf else None
    if options.compile_pdf:
        _compile_latex(target)
        if not pdf_path.exists():
            raise LatexExportError("XeLaTeX 编译结束，但未找到生成的 PDF 文件。")

    return LatexExportResult(tex_path=target, pdf_path=pdf_path)


def build_latex_document(questions: list[Question], options: LatexExportOptions) -> str:
    body_parts = [_render_title(options.title)]
    for index, question in enumerate(questions, start=1):
        body_parts.append(_render_question(index, question, options))
        if index < len(questions):
            gap = max(0.0, min(250.0, options.question_spacing_mm.get(question.id, 0.0)))
            if gap:
                body_parts.append(f"\\par\\vspace{{{gap:.2f}mm}}")

    body = "\n\n".join(part for part in body_parts if part.strip())
    return (
        _render_preamble()
        + "\n\\begin{document}\n"
        + body
        + "\n\\end{document}\n"
    )


def _render_preamble() -> str:
    return r"""
\documentclass[12pt,fontset=none]{ctexart}
\usepackage[a4paper,left=3.17cm,right=3.17cm,top=2.54cm,bottom=2.54cm]{geometry}
\usepackage{fontspec}
\usepackage{xeCJK}
\usepackage{amsmath,amssymb,mathtools}
\usepackage{newtxmath}
\usepackage{array}
\usepackage{ragged2e}
\usepackage{xcolor}
\usepackage{calc}
\usepackage{graphicx}
\usepackage{needspace}
\usepackage[hidelinks]{hyperref}
\usepackage{bookmark}
\setlength{\parindent}{0pt}
\setlength{\parskip}{0.25em}
\linespread{1.38}
\setlength{\lineskip}{6pt}
\setlength{\lineskiplimit}{5pt}
\pagestyle{empty}
\IfFontExistsTF{Times New Roman}{\setmainfont{Times New Roman}}{\setmainfont{TeX Gyre Termes}}
\IfFontExistsTF{SimSun}{\setCJKmainfont{SimSun}}{\IfFontExistsTF{Songti SC}{\setCJKmainfont{Songti SC}}{\setCJKmainfont{FandolSong-Regular}}}
\IfFontExistsTF{SimHei}{\newCJKfontfamily\PMTitleCJK{SimHei}}{\IfFontExistsTF{Heiti SC}{\newCJKfontfamily\PMTitleCJK{Heiti SC}}{\newCJKfontfamily\PMTitleCJK{FandolHei-Regular}}}
\IfFontExistsTF{Times New Roman}{\newfontfamily\PMMathDigitFont{Times New Roman}}{\newfontfamily\PMMathDigitFont{TeX Gyre Termes}}
% Keep the established newtx math letters and symbols, but use Times-style glyphs for digits only.
\begingroup\lccode`~=`0\lowercase{\endgroup\gdef~{\text{\PMMathDigitFont 0}}}\mathcode`0="8000
\begingroup\lccode`~=`1\lowercase{\endgroup\gdef~{\text{\PMMathDigitFont 1}}}\mathcode`1="8000
\begingroup\lccode`~=`2\lowercase{\endgroup\gdef~{\text{\PMMathDigitFont 2}}}\mathcode`2="8000
\begingroup\lccode`~=`3\lowercase{\endgroup\gdef~{\text{\PMMathDigitFont 3}}}\mathcode`3="8000
\begingroup\lccode`~=`4\lowercase{\endgroup\gdef~{\text{\PMMathDigitFont 4}}}\mathcode`4="8000
\begingroup\lccode`~=`5\lowercase{\endgroup\gdef~{\text{\PMMathDigitFont 5}}}\mathcode`5="8000
\begingroup\lccode`~=`6\lowercase{\endgroup\gdef~{\text{\PMMathDigitFont 6}}}\mathcode`6="8000
\begingroup\lccode`~=`7\lowercase{\endgroup\gdef~{\text{\PMMathDigitFont 7}}}\mathcode`7="8000
\begingroup\lccode`~=`8\lowercase{\endgroup\gdef~{\text{\PMMathDigitFont 8}}}\mathcode`8="8000
\begingroup\lccode`~=`9\lowercase{\endgroup\gdef~{\text{\PMMathDigitFont 9}}}\mathcode`9="8000
\color{black}
\newlength{\PMQuestionIndent}
\setlength{\PMQuestionIndent}{1.5em}
\newlength{\PMOptionTextIndent}
\setlength{\PMOptionTextIndent}{1.5em}
\newcommand{\PMOptionLabelWidth}{1.6em}
\newcommand{\PMLabelIndent}{4.6em}
\newcommand{\PMOptionCell}[2]{%
  \parbox[t]{\linewidth}{%
    \hangindent=\PMOptionLabelWidth\hangafter=1\noindent\makebox[\PMOptionLabelWidth][l]{#1.}#2%
  }%
}
\newcommand{\PMOptionBlock}[1]{%
  \par\noindent\hspace*{\PMOptionTextIndent}\begin{minipage}{\dimexpr\linewidth-\PMOptionTextIndent\relax}#1\end{minipage}\par%
}
\newcommand{\PMQuestionLine}[3]{%
  \par\settowidth{\PMQuestionIndent}{#1．}\setlength{\PMOptionTextIndent}{\PMQuestionIndent}%
  \noindent\hangindent=\PMQuestionIndent\hangafter=1%
  \hypertarget{pm-question-#1}{}\bookmark[dest=pm-question-#1,level=0]{第#1题}%
  \makebox[\PMQuestionIndent][l]{#1．}#2#3\par%
}
\newcommand{\PMLabelLine}[2]{%
  \par\noindent\hangindent=\PMLabelIndent\hangafter=1\textbf{#1：}#2\par%
}
\newcommand{\PMAnswerFirst}[1]{%
  \par\noindent\textbf{答案：}\hspace{0.6em}#1\par%
}
\newcommand{\PMAnswerParagraph}[1]{%
  \par\noindent\hspace*{2em}#1\par%
}
% Leave solution space only when it fits on the current page; otherwise start the next question immediately.
\newcommand{\PMSolutionBlank}{%
  \par\begingroup\dimen0=\pagegoal\advance\dimen0 by-\pagetotal
  \ifdim\dimen0>11\baselineskip\vspace*{9\baselineskip}\fi\endgroup%
}
""".strip()


def _render_title(title: str) -> str:
    return (
        "\\begin{center}\n"
        "{\\PMTitleCJK\\bfseries\\fontsize{22pt}{28pt}\\selectfont "
        + _escape_latex_text(title)
        + "}\n"
        "\\end{center}"
    )


def _render_question(index: int, question: Question, options: LatexExportOptions) -> str:
    stem_lines = max(4, min(12, len(question.stem) // 55 + question.stem.count("\n") + 3))
    required = f"{stem_lines}\\baselineskip"
    if question.question_images:
        required += "+0.30\\textheight+3\\baselineskip"
    parts = ["\\Needspace{\\dimexpr " + required + "\\relax}"]
    prefix = "（多选）" if question.question_type == "多选题" else ""
    parts.append(
        "\\PMQuestionLine{"
        + str(index)
        + "}{"
        + _escape_latex_text(prefix)
        + "}{"
        + _render_text_with_inline_math(question.stem, preserve_linebreaks=True)
        + "}"
    )

    for position, path in enumerate(question.question_images, 1):
        caption = f"第{index}题图" + (f"（{position}）" if len(question.question_images) > 1 else "")
        parts.append(_render_gallery_image(path, caption, question.image_widths.get(image_marker(path))))

    if question.question_type in CHOICE_TYPES:
        choice_block = _render_choice_block(question)
        if choice_block:
            parts.append(choice_block)

    if options.include_analysis:
        parts.append(_render_answer_block(question.answer))
        widths = dict(question.image_widths)
        widths.update({key: value for key, value in image_code_settings(question.analysis, question.analysis_image_paths).items() if value is not None})
        first = True
        for kind, content, _ in split_image_content(question.analysis, question.analysis_image_paths):
            if kind == "image":
                parts.append(_render_gallery_image(content, "", widths.get(image_marker(content))))
            elif content.strip():
                parts.append(_render_labeled_block("解析", content) if first else _render_text_with_inline_math(content, preserve_linebreaks=True) + r"\par")
                first = False
    if options.include_note:
        parts.append(_render_labeled_block("备注", question.note))
    if options.leave_solution_space and question.question_type == "解答题":
        parts.append("\\PMSolutionBlank")

    return "\n".join(part for part in parts if part.strip())


def _render_choice_block(question: Question, constrained: bool = False) -> str:
    active_options = [
        (label, _normalize_inline_text(option))
        for label, option in zip(OPTION_LABELS, question.options)
        if option.strip()
    ]
    if not active_options:
        return ""

    row_size = _decide_choice_row_size([content for _, content in active_options], constrained)
    rows = [active_options[i : i + row_size] for i in range(0, len(active_options), row_size)]

    blocks: list[str] = []
    for row in rows:
        if len(row) == 1:
            label, content = row[0]
            blocks.append("\\PMOptionBlock{" + _render_option_cell(label, content) + "}")
            continue

        column_spec = _build_option_column_spec(len(row))
        cells = " & ".join(_render_option_cell(label, content) for label, content in row)
        blocks.append("\\PMOptionBlock{\\begin{tabular*}{\\linewidth}{" + column_spec + "}")
        blocks.append(cells + r" \\")
        blocks.append("\\end{tabular*}}")
    return "\n".join(blocks)


def _build_option_column_spec(column_count: int) -> str:
    if column_count >= 4:
        width = "0.235\\linewidth"
    elif column_count == 3:
        width = "0.30\\linewidth"
    else:
        width = "0.47\\linewidth"
    columns = [">{\\RaggedRight\\arraybackslash}p{" + width + "}" for _ in range(column_count)]
    return "@{}" + "@{\\extracolsep{\\fill}}".join(columns) + "@{}"


def _render_option_cell(label: str, content: str) -> str:
    return "\\PMOptionCell{" + _escape_latex_text(label) + "}{" + _render_text_with_inline_math(content) + "}"


def _render_labeled_block(label: str, content: str) -> str:
    return "\\PMLabelLine{" + _escape_latex_text(label) + "}{" + _render_text_with_inline_math(content, preserve_linebreaks=True) + "}"


def _render_answer_block(content: str) -> str:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    source_lines = [""]
    for kind, part, _ in split_math_segments(normalized):
        if kind == "math":
            source_lines[-1] += "$" + part + "$"
        else:
            pieces = part.split("\n")
            source_lines[-1] += pieces[0]
            source_lines.extend(pieces[1:])
    lines = [_render_inline_fragments(line) for line in source_lines]
    lines = [line for line in lines if line]
    if not lines:
        return "\\PMAnswerFirst{}"
    blocks = ["\\PMAnswerFirst{" + lines[0] + "}"]
    blocks.extend("\\PMAnswerParagraph{" + line + "}" for line in lines[1:])
    return "\n".join(blocks)


def _render_gallery_image(path: str, caption: str, width_percent: int | None = None) -> str:
    if not Path(path).is_file():
        return ""
    width = 0.68 if width_percent is None else min(100, max(10, width_percent)) / 100
    height = "0.30" if width_percent is None else "0.72"
    return r"\par\begin{center}\includegraphics[width=" + f"{width:.2f}" + r"\linewidth,height=" + height + r"\textheight,keepaspectratio]{\detokenize{" + Path(path).resolve().as_posix() + "}}\\par " + caption + r"\end{center}"


def _render_text_with_inline_math(text: str, preserve_linebreaks: bool = False) -> str:
    fragments = []
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    for kind, content, _ in split_math_segments(normalized):
        if kind == "math":
            fragments.append(_render_math_fragment(content))
        else:
            for part in re.split(r"(\n[ \t]*\n+|\n)", content):
                if part.startswith("\n"):
                    fragments.append(r"\par " if part.count("\n") > 1 else (r"\\ " if preserve_linebreaks else " "))
                else:
                    fragments.append(r"\underline{\hspace{4em}}".join(_escape_latex_text(piece) for piece in part.split("@")))
    return "".join(fragments).strip()


def _render_math_fragment(content: str) -> str:
    if not content:
        return ""
    expression = normalize_math_expression(content)
    spacing = r"\rule[-1.0em]{0pt}{3.2em}" if r"\dfrac" in expression else ""
    return "$" + spacing + expression + "$"


def _render_inline_fragments(text: str) -> str:
    fragments: list[str] = []
    for kind, content, _display in split_math_segments(text):
        if kind == "math":
            fragments.append(_render_math_fragment(content))
        elif content:
            fragments.append(r"\underline{\hspace{4em}}".join(_escape_latex_text(part) for part in content.split("@")))
    return "".join(fragments).strip()


def _escape_latex_text(text: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(char, char) for char in text)


def _normalize_inline_text(text: str) -> str:
    compact = text.replace("\n", " ")
    compact = re.sub(r"\s+", " ", compact).strip()
    return compact


def _estimate_text_width(text: str) -> float:
    simplified = re.sub(r"\\[a-zA-Z]+", "x", text)
    simplified = simplified.replace("{", "").replace("}", "").replace("$", "")
    width = 0.0
    for char in simplified:
        width += 1.0 if ord(char) > 127 else 0.55
    return width


def _decide_choice_row_size(options: list[str], constrained: bool = False) -> int:
    if not options:
        return 1
    widths = [_estimate_text_width(item) for item in options]
    one_row_width = sum(widths) + max(0, len(widths) - 1) * 3.2
    if not constrained and len(options) <= 4 and max(widths) <= 4.8 and one_row_width <= 22:
        return len(options)

    pair_widths = []
    for start in range(0, len(widths), 2):
        pair_widths.append(sum(widths[start : start + 2]) + (3.2 if start + 1 < len(widths) else 0))
    if max(pair_widths) <= (12 if constrained else 17):
        return 2

    return 1


def _compile_latex(tex_path: Path) -> None:
    command = [
        "xelatex",
        "-interaction=nonstopmode",
        "-halt-on-error",
        "-file-line-error",
        str(tex_path.name),
    ]
    completed = subprocess.run(
        command,
        cwd=tex_path.parent,
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        log = (completed.stdout or "") + "\n" + (completed.stderr or "")
        raise LatexExportError("XeLaTeX 编译失败：\n" + log.strip())
