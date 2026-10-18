from __future__ import annotations

import html
import hashlib
import base64
import mimetypes
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from urllib.parse import quote
from pathlib import Path
from typing import Iterable

from problem_manager.math_normalizer import normalize_math_expression
from problem_manager.models import CHOICE_TYPES, OPTION_LABELS, Question


# Preview must remain usable without a network connection.  Mathematical SVG is
# generated locally instead of loading MathJax from a CDN inside QWebEngine.
MATHJAX_SCRIPT = ""
MATHJAX_RETYPESET_SCRIPT = ""
_REAL_LATEX_SVG_CACHE: dict[str, str] = {}
_FORMULA_CACHE_DIRECTORY: Path | None = None
_FORMULA_CACHE_VERSION = "latex-svg-depth-v1"
_ACTIVE_PREVIEW_SVGS: ContextVar[dict[str, str] | None] = ContextVar(
    "active_preview_svgs",
    default=None,
)


def configure_formula_cache(directory: Path) -> None:
    global _FORMULA_CACHE_DIRECTORY
    try:
        directory.mkdir(parents=True, exist_ok=True)
        _FORMULA_CACHE_DIRECTORY = directory
    except OSError:
        _FORMULA_CACHE_DIRECTORY = None


def _formula_cache_path(expression: str) -> Path | None:
    if _FORMULA_CACHE_DIRECTORY is None:
        return None
    digest = hashlib.sha256((_FORMULA_CACHE_VERSION + "\n" + expression).encode("utf-8")).hexdigest()
    return _FORMULA_CACHE_DIRECTORY / f"{digest}.html"


def _get_cached_formula(expression: str) -> str | None:
    if expression in _REAL_LATEX_SVG_CACHE:
        return _REAL_LATEX_SVG_CACHE[expression]
    path = _formula_cache_path(expression)
    if path is not None:
        try:
            rendered = path.read_text(encoding="utf-8")
            if rendered.startswith("<img class='math-expression'") and "data:image/svg+xml;base64," in rendered:
                _REAL_LATEX_SVG_CACHE[expression] = rendered
                return rendered
        except (OSError, UnicodeError):
            pass
    return None


def _cache_formula(expression: str, rendered: str) -> None:
    _REAL_LATEX_SVG_CACHE[expression] = rendered
    path = _formula_cache_path(expression)
    if path is not None:
        try:
            temporary = path.with_suffix(".tmp")
            temporary.write_text(rendered, encoding="utf-8")
            temporary.replace(path)
        except OSError:
            pass


def _collect_math_expressions(texts: Iterable[str]) -> list[str]:
    expressions: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for kind, content in _split_math_segments(text):
            if kind != "math":
                continue
            delimiter_length = 2 if content.startswith("$$") else 1
            expression = normalize_math_expression(content[delimiter_length:-delimiter_length])
            if expression and expression not in seen:
                seen.add(expression)
                expressions.append(expression)
    return expressions


def _question_math_expressions(question: Question) -> list[str]:
    return _collect_math_expressions(
        [
            question.stem,
            *question.options,
            question.answer,
            question.analysis,
            question.note,
            *question.knowledge_tags,
            *question.method_tags,
        ]
    )


@contextmanager
def _prepared_math(expressions: Iterable[str]):
    """Render each preview's formulas in one TeX pass instead of one process per formula."""
    token = _ACTIVE_PREVIEW_SVGS.set(_render_latex_svg_batch(expressions))
    try:
        yield
    finally:
        _ACTIVE_PREVIEW_SVGS.reset(token)


def _render_text_with_math(text: str, editor_target: str | None = None) -> str:
    if not text.strip():
        return "<p class='empty'>暂无内容</p>"

    segments = _split_math_segments(text)
    html_parts: list[str] = []
    source_offset = 0
    for kind, content in segments:
        if kind == "math":
            fragment = _render_math_segment_for_preview(content)
        else:
            fragment = html.escape(content).replace("\n", "<br>").replace("@", "<span style='display:inline-block;width:4em;border-bottom:1px solid currentColor;height:1em;vertical-align:baseline'></span>")
        if editor_target:
            fragment = (
                f"<span class='source-fragment' data-editor-target='{html.escape(editor_target, quote=True)}' "
                f"data-source-offset='{source_offset}'>{fragment}</span>"
            )
        html_parts.append(fragment)
        source_offset += len(content)
    return f"<p>{''.join(html_parts)}</p>"


def _split_math_segments(text: str) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    buffer: list[str] = []
    i = 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text) and text[i + 1] == "$":
            buffer.append("$")
            i += 2
            continue

        if text.startswith("$$", i):
            if buffer:
                result.append(("text", "".join(buffer)))
                buffer.clear()
            end = text.find("$$", i + 2)
            if end == -1:
                buffer.append(text[i:])
                break
            result.append(("math", text[i : end + 2]))
            i = end + 2
            continue

        if text[i] == "$":
            if buffer:
                result.append(("text", "".join(buffer)))
                buffer.clear()
            end = i + 1
            while end < len(text):
                if text[end] == "\\" and end + 1 < len(text):
                    end += 2
                    continue
                if text[end] == "$":
                    break
                end += 1
            if end >= len(text):
                buffer.append(text[i:])
                break
            result.append(("math", text[i : end + 1]))
            i = end + 1
            continue

        buffer.append(text[i])
        i += 1

    if buffer:
        result.append(("text", "".join(buffer)))
    return result


def _render_math_segment_for_preview(content: str) -> str:
    if content.startswith("$$") and content.endswith("$$"):
        expression = normalize_math_expression(content[2:-2])
        return _render_local_math_svg(expression)
    if content.startswith("$") and content.endswith("$"):
        expression = normalize_math_expression(content[1:-1])
        return _render_local_math_svg(expression)
    return content


def _render_local_math_svg(expression: str) -> str:
    """Turn one inline TeX expression into a self-contained real LaTeX SVG."""
    active_svgs = _ACTIVE_PREVIEW_SVGS.get()
    if active_svgs is not None and expression in active_svgs:
        return active_svgs[expression]
    cached = _get_cached_formula(expression)
    if cached is not None:
        return cached
    return _render_one_latex_svg(expression)


def _render_one_latex_svg(expression: str) -> str:
    """Render a single formula when a caller is outside a prepared preview batch."""
    try:
        latex = shutil.which("latex")
        dvisvgm = shutil.which("dvisvgm")
        if not latex or not dvisvgm:
            raise RuntimeError("未找到本机 LaTeX 渲染工具")
        with tempfile.TemporaryDirectory(prefix="math-question-preview-") as directory:
            workdir = Path(directory)
            tex_file = workdir / "formula.tex"
            tex_file.write_text(
                "\\documentclass[12pt]{article}\n"
                "\\usepackage{amsmath,amssymb}\n"
                "\\pagestyle{empty}\n"
                "\\newbox\\pmformula\n"
                "\\begin{document}\n"
                + _latex_box_page(expression, 1)
                + "\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            subprocess.run(
                [latex, "-interaction=nonstopmode", "-halt-on-error", "-no-shell-escape", f"-output-directory={workdir}", str(tex_file)],
                cwd=workdir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=12,
            )
            svg_file = workdir / "formula.svg"
            subprocess.run(
                [dvisvgm, "--no-fonts", "--exact", "--bbox=min", f"--output={svg_file}", str(workdir / "formula.dvi")],
                cwd=workdir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=12,
            )
            encoded = base64.b64encode(svg_file.read_bytes()).decode("ascii")
            depth = _read_formula_depths(workdir / "formula.log").get(1, 0.0)
        rendered = _svg_image_html(encoded, expression, depth)
        _cache_formula(expression, rendered)
        return rendered
    except Exception:
        # Preserve the user's input visibly if it uses a command beyond the
        # local renderer's supported TeX subset.
        return f"<span class='math-fallback'>${html.escape(expression)}$</span>"


def _render_latex_svg_batch(expressions: Iterable[str]) -> dict[str, str]:
    unique = [expression for expression in dict.fromkeys(expressions) if expression]
    rendered = {}
    for expression in unique:
        cached = _get_cached_formula(expression)
        if cached is not None:
            rendered[expression] = cached
    pending = [expression for expression in unique if expression not in rendered]
    if not pending:
        return rendered
    try:
        latex = shutil.which("latex")
        dvisvgm = shutil.which("dvisvgm")
        if not latex or not dvisvgm:
            return rendered
        with tempfile.TemporaryDirectory(prefix="math-question-preview-") as directory:
            workdir = Path(directory)
            tex_file = workdir / "formulas.tex"
            pages = "\\newpage\n".join(
                _latex_box_page(expression, index)
                for index, expression in enumerate(pending, start=1)
            )
            tex_file.write_text(
                "\\documentclass[12pt]{article}\n"
                "\\usepackage{amsmath,amssymb}\n"
                "\\pagestyle{empty}\n"
                "\\newbox\\pmformula\n"
                "\\begin{document}\n"
                + pages
                + "\n\\end{document}\n",
                encoding="utf-8",
            )
            subprocess.run(
                [latex, "-interaction=nonstopmode", "-halt-on-error", "-no-shell-escape", f"-output-directory={workdir}", str(tex_file)],
                cwd=workdir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=20,
            )
            subprocess.run(
                [dvisvgm, "--no-fonts", "--exact", "--bbox=min", f"--page=1-{len(pending)}", f"--output={workdir / 'formula-%p.svg'}", str(workdir / "formulas.dvi")],
                cwd=workdir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=20,
            )
            depths = _read_formula_depths(workdir / "formulas.log")
            for index, expression in enumerate(pending, start=1):
                svg_file = workdir / f"formula-{index}.svg"
                if not svg_file.exists():
                    # dvisvgm pads page numbers when a batch has multiple digits.
                    digits = len(str(len(pending)))
                    svg_file = workdir / f"formula-{index:0{digits}d}.svg"
                if not svg_file.exists():
                    continue
                image_html = _svg_image_html(
                    base64.b64encode(svg_file.read_bytes()).decode("ascii"),
                    expression,
                    depths.get(index, 0.0),
                )
                _cache_formula(expression, image_html)
                rendered[expression] = image_html
    except Exception:
        pass
    return rendered


def _latex_box_page(expression: str, index: int) -> str:
    return (
        "\\setbox\\pmformula=\\hbox{\\(\\displaystyle " + expression + "\\)}\n"
        f"\\typeout{{PMDEPTH:{index}:\\the\\dp\\pmformula}}\n"
        "\\noindent\\box\\pmformula"
    )


def _read_formula_depths(log_file: Path) -> dict[int, float]:
    if not log_file.exists():
        return {}
    matches = re.findall(r"PMDEPTH:(\d+):([0-9.]+)pt", log_file.read_text(encoding="utf-8", errors="ignore"))
    return {int(index): float(depth) for index, depth in matches}


def _svg_image_html(encoded: str, expression: str, depth_pt: float = 0.0) -> str:
    return (
        "<img class='math-expression' "
        f"style='vertical-align: -{max(0.0, depth_pt):.3f}pt' "
        f"src='data:image/svg+xml;base64,{encoded}' alt='{html.escape(expression, quote=True)}'>"
    )


def render_question_preview(question: Question) -> str:
    with _prepared_math(_question_math_expressions(question)):
        return _render_question_preview_document(_render_question_preview_sections(question))


def render_question_preview_fragment(question: Question) -> str:
    """Render only the replaceable portion of the editor preview document."""
    with _prepared_math(_question_math_expressions(question)):
        return _render_question_preview_sections(question)


def _render_question_preview_sections(question: Question) -> str:
    option_html = ""
    if question.question_type in CHOICE_TYPES:
        options = []
        for index, (label, content) in enumerate(zip(OPTION_LABELS, question.options)):
            if content.strip():
                options.append(
                    "<div class='option'>"
                    f"<div class='option-label'>{label}.</div>"
                    f"<div class='option-body'>{_render_text_with_math(content, f'option_{index}')}</div>"
                    "</div>"
                )
        option_html = "".join(options)

    image_html = "".join(_render_question_image(path, "preview-image") for path in question.question_images)
    sections = [
        ("题型", html.escape(question.question_type)),
        (
            "属性",
            "；".join(
                [
                    f"难度：{question.difficulty}",
                    f"来源：{html.escape(question.source) if question.source else '未填写'}",
                    f"创建日期：{html.escape(question.created_at)}",
                ]
            ),
        ),
        (
            "知识点标签",
            _render_text_with_math("；".join(question.knowledge_tags) if question.knowledge_tags else ""),
        ),
        (
            "方法/思想标签",
            _render_text_with_math("；".join(question.method_tags) if question.method_tags else ""),
        ),
        ("题目", _render_text_with_math(question.stem, "stem")),
    ]
    if image_html:
        sections.append(("配图", image_html))
    if option_html:
        sections.append(("选项", option_html))
    sections.extend(
        [
            ("答案", _render_text_with_math(question.answer, "answer")),
            ("解析", _render_text_with_math(question.analysis, "analysis") + "".join(_render_question_image(path, "preview-image") for path in question.analysis_image_paths)),
            ("备注", _render_text_with_math(question.note, "note")),
        ]
    )

    content = "".join(
        f"<section><h3>{title}</h3><div>{body}</div></section>"
        for title, body in sections
    )

    return content


def _render_question_preview_document(content: str) -> str:
    return f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  {MATHJAX_SCRIPT}
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      font-family: "Times New Roman", "Songti SC", "STSong", serif;
      margin: 0;
      padding: 22px 24px 44px;
      background:
        radial-gradient(circle at 100% 0%, rgba(210, 181, 130, .17), transparent 31%),
        linear-gradient(155deg, #edf2ed 0%, #f8f6f0 54%, #fafaf7 100%);
      color: #18251f;
    }}
    section {{
      position: relative;
      overflow: hidden;
      background: rgba(255, 255, 252, .94);
      border: 1px solid rgba(202, 214, 203, .9);
      border-radius: 13px;
      margin-bottom: 14px;
      padding: 16px 18px;
      box-shadow: 0 8px 26px rgba(29, 52, 42, .07);
    }}
    section::before {{
      content: "";
      position: absolute;
      top: 0; left: 0; width: 4px; height: 100%;
      background: linear-gradient(180deg, #2d7562, #b77743);
    }}
    h3 {{
      margin: 0 0 10px;
      color: #8a4d26;
      font-size: 15px;
      letter-spacing: .07em;
    }}
    p {{
      margin: 8px 0;
      line-height: 2.22;
      word-break: break-word;
    }}
    .math-expression {{ vertical-align: baseline; max-width: 100%; }}
    .math-fallback {{ color: #a23d32; font-family: "Menlo", monospace; font-size: .9em; }}
    [data-editor-target] {{ cursor: text; }}
    .option {{
      display: flex;
      gap: 10px;
      margin: 8px 0;
      align-items: flex-start;
    }}
    .option-label {{
      width: 28px;
      font-weight: 700;
      color: #1f6655;
      padding-top: 8px;
    }}
    .option-body {{
      flex: 1;
    }}
    .empty {{
      color: #768179;
      font-style: italic;
    }}
    .preview-image {{ max-width: min(100%, 520px); max-height: 360px; object-fit: contain; display: block; margin: 10px auto 2px; border-radius: 7px; }}
  </style>
</head>
<body>
  <main id="preview-content">{content}</main>
  {_preview_source_mapping_script()}
  {MATHJAX_RETYPESET_SCRIPT}
</body>
</html>
"""


def render_text_editor_preview(text: str, title: str) -> str:
    """Render one editable text field in the same offline math style as a question card."""
    with _prepared_math(_collect_math_expressions([text])):
        return _render_text_editor_preview_document(_render_text_editor_preview_fragment(text, title))


def render_text_editor_preview_fragment(text: str, title: str) -> str:
    """Render the changeable content of the expanded text editor preview."""
    with _prepared_math(_collect_math_expressions([text])):
        return _render_text_editor_preview_fragment(text, title)


def _render_text_editor_preview_fragment(text: str, title: str) -> str:
    return f"<h1>{html.escape(title)}预览</h1>{_render_text_with_math(text, 'dialog')}"


def _render_text_editor_preview_document(content: str) -> str:
    return f"""
<!DOCTYPE html>
<html lang='zh-CN'>
<head><meta charset='utf-8'>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 26px 30px 42px; color: #18251f; background: linear-gradient(145deg, #edf3ed, #faf7ef); font-family: "Times New Roman", "Songti SC", "STSong", serif; }}
  article {{ min-height: 100%; background: rgba(255,255,252,.94); border: 1px solid #d1ddd2; border-radius: 12px; padding: 20px 24px; box-shadow: 0 8px 26px rgba(34,61,48,.07); }}
  h1 {{ margin: 0 0 15px; padding-bottom: 11px; border-bottom: 1px solid #e5ece4; color: #8a4d26; font: 700 16px "PingFang SC", sans-serif; letter-spacing: .05em; }}
  p {{ margin: 8px 0; line-height: 2.22; font-size: 18px; }}
  .math-expression {{ vertical-align: baseline; max-width: 100%; }}
  .math-fallback {{ color: #a23d32; font-family: "Menlo", monospace; font-size: .9em; }}
  [data-editor-target] {{ cursor: text; }}
  .empty {{ color: #788279; font-style: italic; }}
</style></head>
<body><article id="preview-content">{content}</article>{_preview_source_mapping_script()}</body>
</html>
"""


def _preview_source_mapping_script() -> str:
    """Report Command-clicked preview fragments to the owning Qt editor."""
    return """
<script>
document.addEventListener('click', function(event) {
  if (!event.metaKey) return;
  const fragment = event.target.closest('[data-editor-target]');
  if (!fragment) return;
  event.preventDefault();
  event.stopPropagation();
  console.info('pm-edit-location:' + fragment.dataset.editorTarget + ':' + fragment.dataset.sourceOffset);
});
</script>
"""


def render_browse_cards(
    questions: Iterable[Question],
    basket_question_ids: set[str],
    expanded_question_ids: set[str],
    active_basket_name: str = "试题篮",
) -> str:
    questions = list(questions)
    texts = []
    for question in questions:
        texts.extend([question.stem, *question.options, *question.knowledge_tags, *question.method_tags])
        if question.id in expanded_question_ids:
            texts.extend([question.answer, question.analysis, question.note])
    with _prepared_math(_collect_math_expressions(texts)):
        return _render_browse_cards(questions, basket_question_ids, expanded_question_ids, active_basket_name)


def _render_browse_cards(
    questions: Iterable[Question],
    basket_question_ids: set[str],
    expanded_question_ids: set[str],
    active_basket_name: str,
) -> str:
    """Render browse-mode cards. Their pm:// links are handled by MainWindow."""
    cards = []
    for number, question in enumerate(questions, start=1):
        in_basket = question.id in basket_question_ids
        expanded = question.id in expanded_question_ids
        detail_label = "收起详情" if expanded else "显示详情"
        basket_checked = " checked" if in_basket else ""
        basket_label = f"移出 {active_basket_name}" if in_basket else f"加入 {active_basket_name}"
        tags = [*question.knowledge_tags, *question.method_tags]
        tag_html = "".join(f"<span class='tag'>{html.escape(tag)}</span>" for tag in tags)
        learning_tag_html = "".join(
            f"<span class='learning-tag'>{html.escape(tag)}</span>" for tag in question.learning_tags
        )
        learning_tags_html = f"<div class='learning-tags'>{learning_tag_html}</div>" if learning_tag_html else ""
        rendered_tags = tag_html or "<span class='untagged'>未标注标签</span>"
        source = html.escape(question.source) if question.source else "未填写来源"
        created_at = html.escape(question.created_at)
        image_html = "".join(_render_question_image(path, "card-image") for path in question.question_images)
        option_html = ""
        if question.question_type in CHOICE_TYPES:
            options = []
            for label, content in zip(OPTION_LABELS, question.options):
                if content.strip():
                    options.append(
                        "<div class='option'>"
                        f"<span class='option-label'>{label}.</span>"
                        f"<span class='option-body'>{_render_text_with_math(content)}</span>"
                        "</div>"
                    )
            option_html = "<div class='options'>" + "".join(options) + "</div>"

        details_html = ""
        if expanded:
            details_html = (
                "<div class='details'>"
                f"<section><h4>答案</h4>{_render_text_with_math(question.answer)}</section>"
                f"<section><h4>解析</h4>{_render_text_with_math(question.analysis)}{''.join(_render_question_image(path, 'card-image') for path in question.analysis_image_paths)}</section>"
                f"<section><h4>备注</h4>{_render_text_with_math(question.note)}</section>"
                "</div>"
            )

        question_id = html.escape(question.id, quote=True)
        cards.append(
            "<article class='question-card'>"
            "<header>"
            f"<div class='number'>{number}.</div>"
            f"<div class='type'>{html.escape(question.question_type)}</div>"
            f"<div class='tags'>{rendered_tags}</div>"
            "<div class='actions'>"
            f"<a href='pm://detail/{question_id}'>{detail_label}</a>"
            f"<a href='pm://labels/{question_id}'>学习标签</a>"
            "<label class='basket-check'>"
            f"<input type='checkbox'{basket_checked} onchange=\"window.location.href='pm://basket/{question_id}'\">"
            f"{html.escape(basket_label)}</label>"
            f"<a href='pm://edit/{question_id}'>编辑</a>"
            f"<a href='pm://delete/{question_id}' class='danger'>删除</a>"
            "</div>"
            "</header>"
            f"<div class='metadata'>难度 {question.difficulty} / 来源：{source} / 创建：{created_at}</div>"
            f"{learning_tags_html}"
            f"<div class='stem'>{_render_text_with_math(question.stem)}</div>"
            f"{image_html}"
            f"{option_html}{details_html}"
            "</article>"
        )

    empty = "<div class='empty-state'>没有符合当前筛选条件的题目。</div>" if not cards else ""
    return f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  {MATHJAX_SCRIPT}
  <style>
    :root {{ color-scheme: light; }}
    * {{ box-sizing: border-box; }}
    body {{
      font-family: "Times New Roman", "Songti SC", "STSong", serif;
      margin: 0; padding: 22px 26px 48px; color: #18251f;
      background:
        radial-gradient(circle at 100% 0%, rgba(202, 174, 121, .18), transparent 31%),
        linear-gradient(155deg, #eaf0ea 0%, #f7f5ef 52%, #f9f9f6 100%);
    }}
    .question-card {{
      position: relative; overflow: hidden;
      background: rgba(255, 255, 252, .94); border: 1px solid #d1ddd2; border-radius: 12px;
      margin: 0 0 16px; padding: 17px 20px 16px; box-shadow: 0 8px 26px rgba(34, 61, 48, .075);
    }}
    .question-card::before {{ content: ""; position: absolute; inset: 0 auto 0 0; width: 4px; background: linear-gradient(180deg, #2d7562 0%, #c5874f 100%); }}
    header {{ display: flex; align-items: center; gap: 9px; flex-wrap: wrap; border-bottom: 1px solid #e5ece4; padding-bottom: 11px; }}
    .number {{ font-size: 20px; font-weight: 700; color: #194d41; font-variant-numeric: oldstyle-nums; }}
    .type {{ background: #e4efe8; color: #245f4f; padding: 4px 9px; border-radius: 999px; font-size: 12px; font-family: "PingFang SC", sans-serif; }}
    .tags {{ display: flex; gap: 5px; flex: 1; flex-wrap: wrap; }}
    .tag {{ background: #f8efe3; color: #87522a; padding: 3px 8px; border-radius: 999px; font-size: 12px; font-family: "PingFang SC", sans-serif; }}
    .untagged {{ color: #798379; font-size: 12px; }}
    .learning-tags {{ display: flex; flex-wrap: wrap; gap: 5px; padding: 8px 0 0; }}
    .learning-tag {{ background: #e9e2f5; color: #614883; padding: 3px 8px; border-radius: 999px; font-size: 12px; font-family: "PingFang SC", sans-serif; }}
    .actions {{ display: flex; gap: 7px; flex-wrap: wrap; }}
    .actions a {{ text-decoration: none; color: #235d4e; border: 1px solid #bad0c1; background: #fbfdf9; padding: 4px 9px; border-radius: 5px; font-size: 12px; font-family: "PingFang SC", sans-serif; transition: .15s ease; }}
    .actions a:hover {{ background: #e8f2ea; border-color: #699883; }}
    .basket-check {{ color: #80502a; border: 1px solid #dfc59e; background: #fffaf1; padding: 4px 9px; border-radius: 5px; font-size: 12px; font-family: "PingFang SC", sans-serif; }}
    .basket-check input {{ vertical-align: -1px; margin: 0 4px 0 0; }}
    .actions .danger {{ color: #a23d32; border-color: #e5b8b0; background: #fffafa; }}
    .metadata {{ color: #6d786f; font-size: 12px; padding-top: 9px; font-family: "PingFang SC", sans-serif; }}
    .card-image {{ max-width: min(100%, 520px); max-height: 340px; object-fit: contain; display: block; margin: 12px auto 4px; border-radius: 7px; }}
    p {{ margin: 8px 0; line-height: 2.15; font-size: 17px; }}
    .math-expression {{ vertical-align: -0.32em; max-width: 100%; }}
    .math-fallback {{ color: #a23d32; font-family: "Menlo", monospace; font-size: .9em; }}
    .stem {{ padding: 6px 2px 0; }}
    .options {{ margin: 5px 0 0 2.2em; display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 4px 20px; }}
    .option {{ display: flex; align-items: flex-start; gap: 7px; min-width: 0; }}
    .option-label {{ min-width: 1.5em; font-weight: 700; padding-top: 8px; }}
    .option-body {{ min-width: 0; flex: 1; }}
    .details {{ margin-top: 14px; border-top: 1px dashed #c3d2c5; padding: 7px 4px 0; background: linear-gradient(90deg, #f7faf6, transparent); }}
    .details section {{ padding: 5px 0; }}
    h4 {{ color: #8a4d26; margin: 6px 0 0; font-size: 14px; letter-spacing: .05em; }}
    .empty, .empty-state {{ color: #788279; font-style: italic; }}
    .empty-state {{ background: rgba(255,255,252,.8); border: 1px dashed #b7c7b9; border-radius: 12px; padding: 42px; text-align: center; }}
  </style>
</head>
<body>{''.join(cards) or empty}{MATHJAX_RETYPESET_SCRIPT}</body>
</html>
"""


def render_overview(questions: Iterable[Question], chapter_counts: list[tuple[str, int]]) -> str:
    question_list = list(questions)
    learning_counts: dict[str, int] = {}
    for question in question_list:
        for tag in question.learning_tags:
            learning_counts[tag] = learning_counts.get(tag, 0) + 1
    recent = sorted(question_list, key=lambda question: question.created_at, reverse=True)[:6]
    metric_html = (
        f"<div class='metric'><strong>{len(question_list)}</strong><span>题目总数</span></div>"
        + "".join(
            f"<a class='metric interactive' href='pm://learning/{quote(tag, safe='')}'>"
            f"<strong>{count}</strong><span>{html.escape(tag)}</span></a>"
            for tag, count in sorted(learning_counts.items(), key=lambda item: (-item[1], item[0]))
        )
        or "<div class='metric'><strong>0</strong><span>题目总数</span></div>"
    )
    chapters_html = "".join(
        f"<tr><td><a href='pm://knowledge/{quote(chapter, safe='')}'>{html.escape(chapter)}</a></td><td>{count}</td></tr>"
        for chapter, count in chapter_counts
        if count
    ) or "<tr><td colspan='2' class='empty'>暂无已标注知识点的题目</td></tr>"
    recent_html = "".join(
        f"<li><span>{html.escape(question.created_at)}</span>{_render_text_with_math(question.stem)}</li>" for question in recent
    ) or "<li class='empty'>暂无题目</li>"
    return f"""
<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'><style>
* {{ box-sizing:border-box; }} body {{ margin:0; padding:28px; color:#18251f; font-family:"Times New Roman","Songti SC",serif; background:linear-gradient(145deg,#edf3ed,#faf7ef); }}
h1 {{ margin:0 0 6px; color:#194d41; font-family:"PingFang SC",sans-serif; }} .sub {{ color:#738078; margin-bottom:22px; }}
.metrics {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(155px,1fr)); gap:12px; margin-bottom:20px; }} .metric,.panel {{ background:rgba(255,255,252,.92); border:1px solid #d3dfd4; border-radius:12px; box-shadow:0 8px 24px rgba(34,61,48,.06); }} .metric {{ padding:17px; border-top:4px solid #3e806d; }} .metric strong {{ font:700 30px "Times New Roman"; color:#194d41; display:block; }} .metric span {{ color:#756858; font:13px "PingFang SC",sans-serif; }} .interactive {{ text-decoration:none; transition:.15s ease; }} .interactive:hover {{ transform:translateY(-2px); border-color:#6f9b86; box-shadow:0 11px 24px rgba(34,61,48,.12); }}
.grid {{ display:grid; grid-template-columns:minmax(280px,1fr) minmax(360px,1.4fr); gap:16px; }} .panel {{ padding:16px 18px; }} h2 {{ color:#8a4d26; margin:0 0 12px; font:700 15px "PingFang SC",sans-serif; letter-spacing:.05em; }} table {{ width:100%; border-collapse:collapse; }} td {{ padding:8px 2px; border-bottom:1px solid #e8eee8; }} td:first-child a {{ color:#294f43; text-decoration:none; }} td:first-child a:hover {{ color:#a05d2e; text-decoration:underline; }} td:last-child {{ text-align:right; color:#235d4e; font-weight:700; }} ul {{ list-style:none; padding:0; margin:0; }} li {{ padding:8px 0; border-bottom:1px solid #e8eee8; line-height:1.7; }} li span {{ display:inline-block; margin-right:10px; color:#7c897f; font:12px "PingFang SC",sans-serif; }} .empty {{ color:#7b857c; font-style:italic; }} .math-expression {{ vertical-align:baseline; max-width:100%; }}
</style></head><body><h1>题库概览</h1><p class='sub'>用标签沉淀错题与好题，用章节观察题库覆盖情况。</p><div class='metrics'>{metric_html}</div><div class='grid'><section class='panel'><h2>知识章节题量</h2><table>{chapters_html}</table></section><section class='panel'><h2>近期新增</h2><ul>{recent_html}</ul></section></div></body></html>"""


def _render_question_image(image_path: str, css_class: str) -> str:
    if not image_path:
        return ""
    path = Path(image_path)
    if not path.is_file():
        return "<p class='empty'>配图文件未找到</p>"
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    try:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return "<p class='empty'>配图无法读取</p>"
    return f"<img class='{css_class}' src='data:{mime_type};base64,{encoded}' alt='题目配图'>"
