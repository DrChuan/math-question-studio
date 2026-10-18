from __future__ import annotations

import re

from problem_manager.latex_support import split_math_segments


def normalize_math_expression(expr: str, *, expand_shortcuts: bool = True) -> str:
    normalized = expr.strip()
    if expand_shortcuts:
        normalized = _expand_vectors(normalized)
        normalized = normalized.replace("@", r"\underline{\quad\quad}")
    normalized = normalized.replace(r"\tfrac", r"\dfrac")
    normalized = re.sub(r"(?<![A-Za-z])\\frac\b", r"\\dfrac", normalized)
    normalized = _expand_compact_fractions(normalized)
    # Accept pasted single/double backslashes, then store one canonical LaTeX command.
    normalized = re.sub(
        r"\\+blank\b",
        lambda _match: r"\underline{\quad\quad}",
        normalized,
    )
    normalized = re.sub(
        r"\\+underline\{\\+quad\\+quad\}",
        lambda _match: r"\underline{\quad\quad}",
        normalized,
    )
    # Keep relation symbols separated so MathJax and XeLaTeX receive unambiguous input.
    normalized = re.sub(r"(\\mid)(?=\S)", r"\1 ", normalized)
    normalized = re.sub(r"\s*<\s*", " < ", normalized)
    normalized = re.sub(r"\s*>\s*", " > ", normalized)

    previous = None
    rounds = 0
    while normalized != previous and rounds < 24:
        previous = normalized
        normalized = _convert_simple_fraction_once(normalized)
        rounds += 1
    return normalized


def _expand_vectors(expr: str) -> str:
    decoration = r"(?:_[A-Za-z0-9]|_\{[^{}]+\}|'+|\\prime|\^(?:\\prime|\{(?:\\prime\s*|')+\}))"
    token = r"[A-Za-z]" + decoration + "*"
    def expand(match):
        body = match.group(1)
        if re.fullmatch(token, body) and body[0].islower():
            return r"\boldsymbol{" + body + "}"
        if re.fullmatch(token + token, body) and body[0].isupper():
            letters = re.sub(decoration, "", body)
            if len(letters) == 2 and letters.isupper():
                return r"\overrightarrow{" + body + "}"
        return r"\overrightarrow{" + body + "}"
    return re.sub(r"\\v\{((?:[^{}]|\{[^{}]*\})*)\}", expand, expr)


def migrate_blank_markers(text: str) -> str:
    text = re.sub(r"\\+underline\{(?:\\+quad\s*)+\}|\\+blank\b|_{3,}|＿{2,}", "@", text)
    return text


def _expand_compact_fractions(expr: str) -> str:
    """Make TeX's compact \\frac12 form explicit for local renderers."""
    result: list[str] = []
    index = 0
    command = r"\dfrac"
    while index < len(expr):
        if not expr.startswith(command, index):
            result.append(expr[index])
            index += 1
            continue
        numerator = _read_tex_atom(expr, index + len(command))
        if numerator is None:
            result.append(command)
            index += len(command)
            continue
        denominator = _read_tex_atom(expr, numerator[1])
        if denominator is None:
            result.append(command)
            index += len(command)
            continue
        result.append(r"\dfrac{" + numerator[0] + "}{" + denominator[0] + "}")
        index = denominator[1]
    return "".join(result)


def _read_tex_atom(expr: str, index: int) -> tuple[str, int] | None:
    while index < len(expr) and expr[index].isspace():
        index += 1
    if index >= len(expr):
        return None
    if expr[index] == "{":
        end = _find_matching_close(expr, index)
        if end is None:
            return None
        return expr[index + 1 : end], end + 1
    if expr[index] == "\\":
        end = index + 1
        while end < len(expr) and expr[end].isalpha():
            end += 1
        if end == index + 1:
            end += 1
        return expr[index:end], end
    return expr[index], index + 1


def normalize_mixed_latex_text(text: str) -> str:
    parts: list[str] = []
    for kind, content, display in split_math_segments(text):
        if kind == "math":
            delimiter = "$$" if display else "$"
            parts.append(delimiter + normalize_math_expression(content, expand_shortcuts=False) + delimiter)
        else:
            parts.append(content)
    return "".join(parts)


def _convert_simple_fraction_once(expr: str) -> str:
    index = 0
    while index < len(expr):
        if expr[index] != "/":
            index += 1
            continue
        left = _find_left_operand(expr, index - 1)
        right = _find_right_operand(expr, index + 1)
        if left is None or right is None:
            index += 1
            continue
        left_text = expr[left[0] : left[1]].strip()
        right_text = expr[right[0] : right[1]].strip()
        if not left_text or not right_text:
            index += 1
            continue
        replacement = r"\dfrac{" + left_text + "}{" + right_text + "}"
        return expr[: left[0]] + replacement + expr[right[1] :]
    return expr


def _find_left_operand(expr: str, index: int) -> tuple[int, int] | None:
    while index >= 0 and expr[index].isspace():
        index -= 1
    if index < 0:
        return None

    char = expr[index]
    if char in ")}]":
        start = _find_matching_open(expr, index)
        if start is None:
            return None
        start = _extend_left_command(expr, start)
        if start > 0 and expr[start - 1] in "^_":
            base = _find_left_operand(expr, start - 2)
            if base is not None:
                start = base[0]
        return (start, index + 1)

    if char in _TOKEN_CHARS:
        start = index
        while start >= 0 and expr[start] in _TOKEN_CHARS:
            start -= 1
        start += 1
        if start > 0 and expr[start - 1] == "\\":
            start -= 1
        return (start, index + 1)

    return None


def _find_right_operand(expr: str, index: int) -> tuple[int, int] | None:
    while index < len(expr) and expr[index].isspace():
        index += 1
    if index >= len(expr):
        return None

    char = expr[index]
    if char in "([{":
        end = _find_matching_close(expr, index)
        if end is None:
            return None
        return (index, end + 1)

    if char == "\\":
        end = index + 1
        while end < len(expr) and expr[end].isalpha():
            end += 1
        while end < len(expr) and expr[end].isspace():
            end += 1
        if end < len(expr) and expr[end] == "{":
            group_end = _find_matching_close(expr, end)
            if group_end is not None:
                end = group_end + 1
        return (index, end)

    if char in _TOKEN_CHARS:
        end = index
        while end < len(expr) and expr[end] in _TOKEN_CHARS:
            end += 1
        return (index, end)

    return None


def _extend_left_command(expr: str, start: int) -> int:
    command_end = start
    command_start = start
    while command_start - 1 >= 0 and expr[command_start - 1].isalpha():
        command_start -= 1
    if command_start - 1 >= 0 and expr[command_start - 1] == "\\":
        return command_start - 1
    return command_end


def _find_matching_open(expr: str, close_index: int) -> int | None:
    pairs = {")": "(", "]": "[", "}": "{"}
    close_char = expr[close_index]
    open_char = pairs.get(close_char)
    if open_char is None:
        return None
    depth = 0
    for index in range(close_index, -1, -1):
        char = expr[index]
        if char == close_char:
            depth += 1
        elif char == open_char:
            depth -= 1
            if depth == 0:
                return index
    return None


def _find_matching_close(expr: str, open_index: int) -> int | None:
    pairs = {"(": ")", "[": "]", "{": "}"}
    open_char = expr[open_index]
    close_char = pairs.get(open_char)
    if close_char is None:
        return None
    depth = 0
    for index in range(open_index, len(expr)):
        char = expr[index]
        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return index
    return None


_TOKEN_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._^'")
_TOKEN_CHARS.update({"_", "}", "{"})
