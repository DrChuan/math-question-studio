from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any
from uuid import uuid4


class QuestionType(str, Enum):
    SINGLE = "单选题"
    MULTIPLE = "多选题"
    FILL_BLANK = "填空题"
    SOLUTION = "解答题"


CHOICE_TYPES = {QuestionType.SINGLE.value, QuestionType.MULTIPLE.value}
OPTION_LABELS = ["A", "B", "C", "D", "E", "F"]


@dataclass
class Question:
    id: str = field(default_factory=lambda: uuid4().hex)
    question_type: str = QuestionType.SINGLE.value
    difficulty: int = 3
    source: str = ""
    created_at: str = field(default_factory=lambda: date.today().isoformat())
    image_path: str = ""
    stem: str = ""
    options: list[str] = field(default_factory=lambda: ["", "", "", "", "", ""])
    answer: str = ""
    analysis: str = ""
    note: str = ""
    knowledge_tags: list[str] = field(default_factory=list)
    method_tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Question":
        options = list(data.get("options", []))
        while len(options) < len(OPTION_LABELS):
            options.append("")
        try:
            difficulty = int(data.get("difficulty", 3))
        except (TypeError, ValueError):
            difficulty = 3
        return cls(
            id=data.get("id") or uuid4().hex,
            question_type=data.get("question_type", QuestionType.SINGLE.value),
            difficulty=min(5, max(1, difficulty)),
            source=data.get("source", ""),
            created_at=data.get("created_at") or date.today().isoformat(),
            image_path=data.get("image_path", ""),
            stem=data.get("stem", ""),
            options=options[: len(OPTION_LABELS)],
            answer=data.get("answer", ""),
            analysis=data.get("analysis", ""),
            note=data.get("note", ""),
            knowledge_tags=list(data.get("knowledge_tags", [])),
            method_tags=list(data.get("method_tags", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "question_type": self.question_type,
            "difficulty": self.difficulty,
            "source": self.source,
            "created_at": self.created_at,
            "image_path": self.image_path,
            "stem": self.stem,
            "options": self.options[: len(OPTION_LABELS)],
            "answer": self.answer,
            "analysis": self.analysis,
            "note": self.note,
            "knowledge_tags": self.knowledge_tags,
            "method_tags": self.method_tags,
        }

    def title(self, index: int) -> str:
        text = " ".join(self.stem.split())
        preview = text[:20] + ("..." if len(text) > 20 else "")
        if not preview:
            preview = "未命名题目"
        tag_hint = ""
        if self.knowledge_tags:
            tag_hint = f" | {self.knowledge_tags[0].split('/')[-1].strip()}"
        return f"{index + 1}. {self.question_type} | {preview}{tag_hint}"


@dataclass
class AppState:
    questions: list[Question] = field(default_factory=list)
    method_tag_library: list[str] = field(default_factory=list)
    basket_question_ids: list[str] = field(default_factory=list)
    vmware_profile: dict[str, str] = field(default_factory=dict)
