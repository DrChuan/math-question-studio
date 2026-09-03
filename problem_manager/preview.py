from __future__ import annotations

import html
import base64
import mimetypes
import re
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Iterable

from problem_manager.math_normalizer import normalize_math_expression
from problem_manager.models import CHOICE_TYPES, OPTION_LABELS, Question


# Preview must remain usable without a network connection.  Mathematical SVG is
# generated locally instead of loading MathJax from a CDN inside QWebEngine.
MATHJAX_SCRIPT = ""
MATHJAX_RETYPESET_SCRIPT = ""


def _render_text_with_math(text: str) -> str:
    if not text.strip():
        return "<p class='empty'>暂无内容</p>"

    segments = _split_math_segments(text)
    html_parts: list[str] = []
    for kind, content in segments:
        if kind == "math":
            html_parts.append(_render_math_segment_for_preview(content))
        else:
            html_parts.append(html.escape(content))

    body = "".join(html_parts)
    paragraphs = body.split("\n\n")
    rendered = []
    for paragraph in paragraphs:
        paragraph = paragraph.replace("\n", "<br>")
        rendered.append(f"<p>{paragraph}</p>")
    return "".join(rendered)


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
        return _render_local_math_svg(normalize_math_expression(content[2:-2]))
    if content.startswith("$") and content.endswith("$"):
        return _render_local_math_svg(normalize_math_expression(content[1:-1]))
    return content


@lru_cache(maxsize=512)
def _render_local_math_svg(expression: str) -> str:
    """Turn one inline TeX expression into a self-contained offline SVG image."""
    try:
        from matplotlib import mathtext, rc_context
        from matplotlib.font_manager import FontProperties

        # Matplotlib's mathtext has no underline primitive.  The fill-in marker
        # is equivalent here and retains the source's canonical LaTeX unchanged.
        source = _to_mathtext_compatible_tex(expression)
        stream = BytesIO()
        with rc_context({"mathtext.fontset": "stix", "mathtext.default": "it"}):
            mathtext.math_to_image(
                f"${source}$",
                stream,
                prop=FontProperties(size=13),
                format="svg",
                color="#18251f",
            )
        encoded = base64.b64encode(stream.getvalue()).decode("ascii")
        return (
            "<img class='math-expression' "
            f"src='data:image/svg+xml;base64,{encoded}' alt='{html.escape(expression, quote=True)}'>"
        )
    except Exception:
        # Preserve the user's input visibly if it uses a command beyond the
        # local renderer's supported TeX subset.
        return f"<span class='math-fallback'>${html.escape(expression)}$</span>"


def _to_mathtext_compatible_tex(expression: str) -> str:
    """Adapt a few common MathJax aliases to Matplotlib's local TeX subset."""
    source = re.sub(r"\\underline\{\\quad\\quad\}", r"\\_\\_\\_\\_\\_", expression)
    aliases = {
        r"\le": r"\leq",
        r"\ge": r"\geq",
        r"\ne": r"\neq",
    }
    for original, replacement in aliases.items():
        source = re.sub(
            rf"{re.escape(original)}(?![A-Za-z])",
            lambda _match, value=replacement: value,
            source,
        )
    return source


def render_question_preview(question: Question) -> str:
    option_html = ""
    if question.question_type in CHOICE_TYPES:
        options = []
        for label, content in zip(OPTION_LABELS, question.options):
            if content.strip():
                options.append(
                    "<div class='option'>"
                    f"<div class='option-label'>{label}.</div>"
                    f"<div class='option-body'>{_render_text_with_math(content)}</div>"
                    "</div>"
                )
        option_html = "".join(options)

    image_html = _render_question_image(question.image_path, "preview-image")
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
        ("题目", _render_text_with_math(question.stem)),
    ]
    if image_html:
        sections.append(("配图", image_html))
    if option_html:
        sections.append(("选项", option_html))
    sections.extend(
        [
            ("答案", _render_text_with_math(question.answer)),
            ("解析", _render_text_with_math(question.analysis)),
            ("备注", _render_text_with_math(question.note)),
        ]
    )

    content = "".join(
        f"<section><h3>{title}</h3><div>{body}</div></section>"
        for title, body in sections
    )

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
      line-height: 1.82;
      word-break: break-word;
    }}
    .math-expression {{ vertical-align: -0.18em; max-width: 100%; }}
    .math-fallback {{ color: #a23d32; font-family: "Menlo", monospace; font-size: .9em; }}
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
  {content}
  {MATHJAX_RETYPESET_SCRIPT}
</body>
</html>
"""


def render_browse_cards(
    questions: Iterable[Question],
    basket_question_ids: set[str],
    expanded_question_ids: set[str],
) -> str:
    """Render browse-mode cards. Their pm:// links are handled by MainWindow."""
    cards = []
    for number, question in enumerate(questions, start=1):
        in_basket = question.id in basket_question_ids
        expanded = question.id in expanded_question_ids
        detail_label = "收起详情" if expanded else "显示详情"
        basket_checked = " checked" if in_basket else ""
        tags = [*question.knowledge_tags, *question.method_tags]
        tag_html = "".join(f"<span class='tag'>{html.escape(tag)}</span>" for tag in tags)
        rendered_tags = tag_html or "<span class='untagged'>未标注标签</span>"
        source = html.escape(question.source) if question.source else "未填写来源"
        created_at = html.escape(question.created_at)
        image_html = _render_question_image(question.image_path, "card-image")
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
                f"<section><h4>解析</h4>{_render_text_with_math(question.analysis)}</section>"
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
            "<label class='basket-check'>"
            f"<input type='checkbox'{basket_checked} onchange=\"window.location.href='pm://basket/{question_id}'\">"
            "加入题目篮</label>"
            f"<a href='pm://edit/{question_id}'>编辑</a>"
            f"<a href='pm://delete/{question_id}' class='danger'>删除</a>"
            "</div>"
            "</header>"
            f"<div class='metadata'>难度 {question.difficulty} / 来源：{source} / 创建：{created_at}</div>"
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
    .actions {{ display: flex; gap: 7px; flex-wrap: wrap; }}
    .actions a {{ text-decoration: none; color: #235d4e; border: 1px solid #bad0c1; background: #fbfdf9; padding: 4px 9px; border-radius: 5px; font-size: 12px; font-family: "PingFang SC", sans-serif; transition: .15s ease; }}
    .actions a:hover {{ background: #e8f2ea; border-color: #699883; }}
    .basket-check {{ color: #80502a; border: 1px solid #dfc59e; background: #fffaf1; padding: 4px 9px; border-radius: 5px; font-size: 12px; font-family: "PingFang SC", sans-serif; }}
    .basket-check input {{ vertical-align: -1px; margin: 0 4px 0 0; }}
    .actions .danger {{ color: #a23d32; border-color: #e5b8b0; background: #fffafa; }}
    .metadata {{ color: #6d786f; font-size: 12px; padding-top: 9px; font-family: "PingFang SC", sans-serif; }}
    .card-image {{ max-width: min(100%, 520px); max-height: 340px; object-fit: contain; display: block; margin: 12px auto 4px; border-radius: 7px; }}
    p {{ margin: 8px 0; line-height: 1.82; font-size: 17px; }}
    .math-expression {{ vertical-align: -0.18em; max-width: 100%; }}
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
