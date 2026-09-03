from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from docx.oxml import OxmlElement
from docx.oxml.ns import qn


SYMBOL_MAP = {
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "delta": "δ",
    "theta": "θ",
    "lambda": "λ",
    "mu": "μ",
    "pi": "π",
    "sigma": "σ",
    "phi": "φ",
    "omega": "ω",
    "pm": "±",
    "mp": "∓",
    "times": "×",
    "cdot": "·",
    "div": "÷",
    "le": "≤",
    "leq": "≤",
    "ge": "≥",
    "geq": "≥",
    "neq": "≠",
    "ne": "≠",
    "approx": "≈",
    "to": "→",
    "rightarrow": "→",
    "Rightarrow": "⇒",
    "leftarrow": "←",
    "infty": "∞",
    "in": "∈",
    "notin": "∉",
    "subset": "⊂",
    "subseteq": "⊆",
    "supset": "⊃",
    "supseteq": "⊇",
    "cup": "∪",
    "cap": "∩",
    "varnothing": "∅",
    "emptyset": "∅",
    "because": "∵",
    "therefore": "∴",
    "angle": "∠",
    "triangle": "△",
    "parallel": "∥",
    "perp": "⊥",
    "ldots": "…",
    "cdots": "⋯",
    "dots": "…",
    "sum": "∑",
    "prod": "∏",
    "int": "∫",
    "forall": "∀",
    "exists": "∃",
    "mid": "|",
    "setminus": "∖",
    "sin": "sin",
    "cos": "cos",
    "tan": "tan",
    "cot": "cot",
    "sec": "sec",
    "csc": "csc",
    "log": "log",
    "ln": "ln",
    "lim": "lim",
    "max": "max",
    "min": "min",
}

SPACING_COMMANDS = {"quad": "    ", "qquad": "        ", ",": " ", ";": " ", ":": " "}
DELIMITER_COMMANDS = {
    "lbrace": "{",
    "rbrace": "}",
    "lvert": "|",
    "rvert": "|",
    "langle": "⟨",
    "rangle": "⟩",
}


@dataclass
class Token:
    kind: str
    value: str


@dataclass
class TextNode:
    text: str


@dataclass
class RowNode:
    children: list[object]


@dataclass
class FractionNode:
    numerator: object
    denominator: object


@dataclass
class SqrtNode:
    body: object
    degree: object | None = None


@dataclass
class ScriptNode:
    base: object
    sub: object | None = None
    sup: object | None = None


@dataclass
class DelimitedNode:
    left: str
    body: object
    right: str


@dataclass
class CasesNode:
    rows: list[list[object]]


def split_math_segments(text: str) -> list[tuple[str, str, bool]]:
    result: list[tuple[str, str, bool]] = []
    buffer: list[str] = []
    i = 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text) and text[i + 1] == "$":
            buffer.append("$")
            i += 2
            continue

        if text.startswith("$$", i):
            if buffer:
                result.append(("text", "".join(buffer), False))
                buffer.clear()
            end = text.find("$$", i + 2)
            if end == -1:
                buffer.append(text[i:])
                break
            result.append(("math", text[i + 2 : end].strip(), True))
            i = end + 2
            continue

        if text[i] == "$":
            if buffer:
                result.append(("text", "".join(buffer), False))
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
            result.append(("math", text[i + 1 : end].strip(), False))
            i = end + 1
            continue

        buffer.append(text[i])
        i += 1

    if buffer:
        result.append(("text", "".join(buffer), False))
    return result


def latex_to_omml(latex: str) -> OxmlElement:
    parser = LatexParser(tokenize(latex))
    ast = parser.parse()
    omath = OxmlElement("m:oMath")
    _append_node(omath, ast)
    return omath


def tokenize(expr: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    while i < len(expr):
        char = expr[i]
        if char.isspace():
            i += 1
            continue

        if char == "\\":
            if i + 1 < len(expr) and expr[i + 1] == "\\":
                tokens.append(Token("COMMAND", "newline"))
                i += 2
                continue

            if i + 1 < len(expr) and expr[i + 1] in "{}[]$%&_#^|":
                tokens.append(Token("TEXT", expr[i + 1]))
                i += 2
                continue

            j = i + 1
            while j < len(expr) and expr[j].isalpha():
                j += 1
            if j == i + 1:
                tokens.append(Token("COMMAND", expr[i + 1]))
                i += 2
            else:
                tokens.append(Token("COMMAND", expr[i + 1 : j]))
                i = j
            continue

        if char in "{}[]^_&()":
            tokens.append(Token(char, char))
            i += 1
            continue

        if char == ".":
            tokens.append(Token("TEXT", "."))
            i += 1
            continue

        tokens.append(Token("TEXT", char))
        i += 1
    return tokens


class LatexParser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.position = 0

    def parse(self) -> object:
        return self._pack_row(self._parse_until(set()))

    def _parse_until(self, stop_kinds: set[str], stop_commands: set[str] | None = None) -> list[object]:
        stop_commands = stop_commands or set()
        nodes: list[object] = []
        while not self._is_end():
            token = self._peek()
            if token.kind in stop_kinds:
                break
            if token.kind == "COMMAND" and token.value in stop_commands:
                break

            atom = self._parse_atom()
            if atom is None:
                continue
            atom = self._attach_scripts(atom)
            nodes.append(atom)
        return self._merge_text_nodes(nodes)

    def _parse_atom(self) -> object | None:
        token = self._peek()
        if token.kind == "{":
            self._advance()
            body = self._pack_row(self._parse_until({"}"}))
            self._consume("}")
            return body
        if token.kind == "TEXT":
            self._advance()
            return TextNode(token.value)
        if token.kind == "COMMAND":
            self._advance()
            return self._parse_command(token.value)
        self._advance()
        return TextNode(token.value)

    def _parse_command(self, command: str) -> object | None:
        if command == "frac":
            numerator = self._parse_group_or_atom()
            denominator = self._parse_group_or_atom()
            return FractionNode(numerator, denominator)

        if command == "sqrt":
            degree = None
            if self._match("["):
                degree = self._pack_row(self._parse_until({"]"}))
                self._consume("]")
            body = self._parse_group_or_atom()
            return SqrtNode(body, degree)

        if command == "left":
            left = self._read_delimiter()
            body = self._pack_row(self._parse_until(set(), {"right"}))
            if self._peek_command("right"):
                self._advance()
                right = self._read_delimiter()
            else:
                right = "."
            return DelimitedNode(left, body, right)

        if command == "begin":
            env = self._read_group_name()
            if env == "cases":
                return self._parse_cases()
            return TextNode(f"[{env}]")

        if command == "text" or command == "mathrm" or command == "operatorname":
            return self._parse_group_or_atom()

        if command == "underline":
            return self._parse_group_or_atom()

        if command in SPACING_COMMANDS:
            return TextNode(SPACING_COMMANDS[command])

        if command == "newline":
            return TextNode("\n")

        if command in SYMBOL_MAP:
            return TextNode(SYMBOL_MAP[command])

        if command in DELIMITER_COMMANDS:
            return TextNode(DELIMITER_COMMANDS[command])

        return TextNode(f"\\{command}")

    def _parse_cases(self) -> CasesNode:
        rows: list[list[object]] = []
        current_row: list[object] = []

        while not self._is_end():
            if self._peek_command("end"):
                self._advance()
                env = self._read_group_name()
                if env == "cases":
                    if current_row:
                        rows.append(current_row)
                    break

            part = self._pack_row(self._parse_until({"&"}, {"newline", "end"}))
            current_row.append(part)
            if self._match("&"):
                continue
            if self._peek_command("newline"):
                self._advance()
                rows.append(current_row)
                current_row = []
                continue
            if self._peek_command("end"):
                continue
            if self._is_end():
                if current_row:
                    rows.append(current_row)
                break

        return CasesNode(rows)

    def _attach_scripts(self, base: object) -> object:
        sub = None
        sup = None
        while not self._is_end() and self._peek().kind in {"_", "^"}:
            token = self._advance()
            value = self._parse_group_or_atom()
            if token.kind == "_":
                sub = value
            else:
                sup = value
        if sub is None and sup is None:
            return base
        return ScriptNode(base=base, sub=sub, sup=sup)

    def _parse_group_or_atom(self) -> object:
        if self._is_end():
            return TextNode("")
        token = self._peek()
        if token.kind == "{":
            self._advance()
            body = self._pack_row(self._parse_until({"}"}))
            self._consume("}")
            return body
        return self._attach_scripts(self._parse_atom())

    def _read_delimiter(self) -> str:
        if self._is_end():
            return "."
        token = self._advance()
        if token.kind == "TEXT":
            return token.value
        if token.kind == "COMMAND":
            return DELIMITER_COMMANDS.get(token.value, token.value)
        return token.value

    def _read_group_name(self) -> str:
        self._consume("{")
        parts = self._parse_until({"}"})
        self._consume("}")
        text = []
        for node in parts:
            if isinstance(node, TextNode):
                text.append(node.text)
        return "".join(text)

    def _pack_row(self, nodes: list[object]) -> object:
        if not nodes:
            return TextNode("")
        if len(nodes) == 1:
            return nodes[0]
        return RowNode(nodes)

    def _merge_text_nodes(self, nodes: list[object]) -> list[object]:
        merged: list[object] = []
        buffer: list[str] = []
        for node in nodes:
            if isinstance(node, TextNode):
                buffer.append(node.text)
                continue
            if buffer:
                merged.append(TextNode("".join(buffer)))
                buffer.clear()
            merged.append(node)
        if buffer:
            merged.append(TextNode("".join(buffer)))
        return merged

    def _consume(self, kind: str) -> Token:
        token = self._advance()
        if token.kind != kind:
            raise ValueError(f"Expected token {kind}, got {token.kind}")
        return token

    def _match(self, kind: str) -> bool:
        if self._is_end() or self._peek().kind != kind:
            return False
        self._advance()
        return True

    def _peek_command(self, command: str) -> bool:
        return not self._is_end() and self._peek().kind == "COMMAND" and self._peek().value == command

    def _peek(self) -> Token:
        return self.tokens[self.position]

    def _advance(self) -> Token:
        token = self.tokens[self.position]
        self.position += 1
        return token

    def _is_end(self) -> bool:
        return self.position >= len(self.tokens)


def _append_node(parent: OxmlElement, node: object) -> None:
    if isinstance(node, TextNode):
        for chunk in node.text.split("\n"):
            if chunk:
                parent.append(_math_run(chunk))
        return
    if isinstance(node, RowNode):
        for child in node.children:
            _append_node(parent, child)
        return
    if isinstance(node, FractionNode):
        frac = OxmlElement("m:f")
        num = OxmlElement("m:num")
        den = OxmlElement("m:den")
        _append_node(num, node.numerator)
        _append_node(den, node.denominator)
        frac.append(num)
        frac.append(den)
        parent.append(frac)
        return
    if isinstance(node, SqrtNode):
        rad = OxmlElement("m:rad")
        degree = OxmlElement("m:deg")
        if node.degree is not None:
            _append_node(degree, node.degree)
        rad.append(degree)
        body = OxmlElement("m:e")
        _append_node(body, node.body)
        rad.append(body)
        parent.append(rad)
        return
    if isinstance(node, ScriptNode):
        if node.sub is not None and node.sup is not None:
            container = OxmlElement("m:sSubSup")
            base = OxmlElement("m:e")
            sub = OxmlElement("m:sub")
            sup = OxmlElement("m:sup")
            _append_node(base, node.base)
            _append_node(sub, node.sub)
            _append_node(sup, node.sup)
            container.append(base)
            container.append(sub)
            container.append(sup)
        elif node.sub is not None:
            container = OxmlElement("m:sSub")
            base = OxmlElement("m:e")
            sub = OxmlElement("m:sub")
            _append_node(base, node.base)
            _append_node(sub, node.sub)
            container.append(base)
            container.append(sub)
        else:
            container = OxmlElement("m:sSup")
            base = OxmlElement("m:e")
            sup = OxmlElement("m:sup")
            _append_node(base, node.base)
            _append_node(sup, node.sup)
            container.append(base)
            container.append(sup)
        parent.append(container)
        return
    if isinstance(node, DelimitedNode):
        delimiter = OxmlElement("m:d")
        props = OxmlElement("m:dPr")
        beg = OxmlElement("m:begChr")
        beg.set(qn("m:val"), "" if node.left == "." else node.left)
        end = OxmlElement("m:endChr")
        end.set(qn("m:val"), "" if node.right == "." else node.right)
        props.append(beg)
        props.append(end)
        delimiter.append(props)
        body = OxmlElement("m:e")
        _append_node(body, node.body)
        delimiter.append(body)
        parent.append(delimiter)
        return
    if isinstance(node, CasesNode):
        delimiter = OxmlElement("m:d")
        props = OxmlElement("m:dPr")
        beg = OxmlElement("m:begChr")
        beg.set(qn("m:val"), "{")
        end = OxmlElement("m:endChr")
        end.set(qn("m:val"), "")
        props.append(beg)
        props.append(end)
        delimiter.append(props)
        array = OxmlElement("m:eqArr")
        for row in node.rows:
            item = OxmlElement("m:e")
            for index, part in enumerate(row):
                _append_node(item, part)
                if index < len(row) - 1:
                    item.append(_math_run("    "))
            array.append(item)
        delimiter.append(array)
        parent.append(delimiter)
        return

    parent.append(_math_run(str(node)))


def _math_run(text: str) -> OxmlElement:
    run = OxmlElement("m:r")
    text_element = OxmlElement("m:t")
    text_element.text = text
    run.append(text_element)
    return run
