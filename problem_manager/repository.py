from __future__ import annotations

import json
from pathlib import Path

from problem_manager.models import AppState, Question, QuestionBasket
from problem_manager.sample_data import build_sample_questions
from problem_manager.tag_catalog import DEFAULT_METHOD_TAGS


class QuestionRepository:
    def __init__(self, data_file: Path) -> None:
        self.data_file = data_file
        self.data_file.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> AppState:
        if not self.data_file.exists():
            default_basket = QuestionBasket()
            state = AppState(
                questions=build_sample_questions(),
                method_tag_library=list(DEFAULT_METHOD_TAGS),
                learning_tag_library=[],
                baskets=[default_basket],
                active_basket_id=default_basket.id,
                vmware_profile={},
            )
            self.save(state)
            return state

        data = json.loads(self.data_file.read_text(encoding="utf-8"))
        if "questions" not in data and isinstance(data, list):
            default_basket = QuestionBasket()
            return AppState(
                questions=[Question.from_dict(item) for item in data],
                method_tag_library=list(DEFAULT_METHOD_TAGS),
                learning_tag_library=[],
                baskets=[default_basket],
                active_basket_id=default_basket.id,
                vmware_profile={},
            )

        baskets = [QuestionBasket.from_dict(item) for item in data.get("baskets", [])]
        if not baskets:
            # Preserve selections saved by every pre-named-basket version.
            baskets = [QuestionBasket(name="默认试题篮", question_ids=list(data.get("basket_question_ids", [])))]
        active_basket_id = str(data.get("active_basket_id") or baskets[0].id)
        if active_basket_id not in {basket.id for basket in baskets}:
            active_basket_id = baskets[0].id
        return AppState(
            questions=[Question.from_dict(item) for item in data.get("questions", [])],
            method_tag_library=list(data.get("method_tag_library", DEFAULT_METHOD_TAGS)),
            learning_tag_library=list(data.get("learning_tag_library", [])),
            baskets=baskets,
            active_basket_id=active_basket_id,
            vmware_profile=dict(data.get("vmware_profile", {})),
        )

    def save(self, state: AppState) -> None:
        self.data_file.write_text(
            json.dumps(self.to_payload(state, include_vmware_profile=True), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def to_payload(self, state: AppState, *, include_vmware_profile: bool = False) -> dict:
        payload: dict = {
            "questions": [question.to_dict() for question in state.questions],
            "method_tag_library": state.method_tag_library,
            "learning_tag_library": state.learning_tag_library,
            "baskets": [basket.to_dict() for basket in state.baskets],
            "active_basket_id": state.active_basket_id,
        }
        if include_vmware_profile:
            payload["vmware_profile"] = state.vmware_profile
        return payload

    def state_from_payload(self, payload: dict) -> AppState:
        """Read a portable backup payload through the same compatibility path."""
        baskets = [QuestionBasket.from_dict(item) for item in payload.get("baskets", [])]
        if not baskets:
            baskets = [QuestionBasket(name="默认试题篮", question_ids=list(payload.get("basket_question_ids", [])))]
        active_basket_id = str(payload.get("active_basket_id") or baskets[0].id)
        if active_basket_id not in {basket.id for basket in baskets}:
            active_basket_id = baskets[0].id
        return AppState(
            questions=[Question.from_dict(item) for item in payload.get("questions", [])],
            method_tag_library=list(payload.get("method_tag_library", DEFAULT_METHOD_TAGS)),
            learning_tag_library=list(payload.get("learning_tag_library", [])),
            baskets=baskets,
            active_basket_id=active_basket_id,
            vmware_profile={},
        )
