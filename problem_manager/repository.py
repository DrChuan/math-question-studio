from __future__ import annotations

import json
from pathlib import Path

from problem_manager.models import AppState, Question
from problem_manager.sample_data import build_sample_questions
from problem_manager.tag_catalog import DEFAULT_METHOD_TAGS


class QuestionRepository:
    def __init__(self, data_file: Path) -> None:
        self.data_file = data_file
        self.data_file.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> AppState:
        if not self.data_file.exists():
            state = AppState(
                questions=build_sample_questions(),
                method_tag_library=list(DEFAULT_METHOD_TAGS),
                basket_question_ids=[],
                vmware_profile={},
            )
            self.save(state)
            return state

        data = json.loads(self.data_file.read_text(encoding="utf-8"))
        if "questions" not in data and isinstance(data, list):
            return AppState(
                questions=[Question.from_dict(item) for item in data],
                method_tag_library=list(DEFAULT_METHOD_TAGS),
                basket_question_ids=[],
                vmware_profile={},
            )

        return AppState(
            questions=[Question.from_dict(item) for item in data.get("questions", [])],
            method_tag_library=list(data.get("method_tag_library", DEFAULT_METHOD_TAGS)),
            basket_question_ids=list(data.get("basket_question_ids", [])),
            vmware_profile=dict(data.get("vmware_profile", {})),
        )

    def save(self, state: AppState) -> None:
        payload = {
            "questions": [question.to_dict() for question in state.questions],
            "method_tag_library": state.method_tag_library,
            "basket_question_ids": state.basket_question_ids,
            "vmware_profile": state.vmware_profile,
        }
        self.data_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
