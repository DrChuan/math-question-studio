from __future__ import annotations

import json
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

from problem_manager.models import AppState, QuestionBasket
from problem_manager.repository import QuestionRepository
from problem_manager.image_markup import image_marker, replace_image_code


BACKUP_VERSION = 1
STATE_FILE = "question_bank.json"
MANIFEST_FILE = "manifest.json"
IMAGE_DIRECTORY = "images"


class BackupError(RuntimeError):
    pass


@dataclass
class RestoredBackup:
    state: AppState
    image_sources: dict[str, Path]
    staging_directory: Path


def create_backup(repository: QuestionRepository, state: AppState, destination: str | Path) -> Path:
    """Create a portable ZIP without device-specific VMware settings."""
    destination = Path(destination)
    payload = repository.to_payload(state)
    images: list[tuple[Path, str]] = []
    for question_data in payload["questions"]:
        for field in ["image_paths", "analysis_image_paths"]:
            paths = []
            for index, original_path in enumerate(question_data.get(field, [])):
                original = Path(original_path)
                if original.is_file():
                    archive_name = f"{IMAGE_DIRECTORY}/{question_data['id']}-{field}-{index}{original.suffix.lower() or '.png'}"
                    paths.append(archive_name)
                    if field == "analysis_image_paths":
                        question_data["analysis"] = replace_image_code(question_data["analysis"], str(original), archive_name, preserve_width=True)
                    widths = question_data.get("image_widths", {})
                    if image_marker(str(original)) in widths:
                        widths[image_marker(archive_name)] = widths.pop(image_marker(str(original)))
                    images.append((original, archive_name))
            question_data[field] = paths
        question_data["image_path"] = next(iter(question_data["image_paths"]), "")

    manifest = {"format": "Math Question Studio Backup", "version": BACKUP_VERSION, "state_file": STATE_FILE}
    try:
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(MANIFEST_FILE, json.dumps(manifest, ensure_ascii=False, indent=2))
            archive.writestr(STATE_FILE, json.dumps(payload, ensure_ascii=False, indent=2))
            for source, archive_name in images:
                archive.write(source, archive_name)
    except OSError as error:
        raise BackupError(f"无法创建备份：{error}") from error
    return destination


def read_backup(repository: QuestionRepository, source: str | Path) -> RestoredBackup:
    """Validate a backup and stage its images for a later merge or replacement."""
    source = Path(source)
    try:
        with zipfile.ZipFile(source) as archive:
            manifest = json.loads(archive.read(MANIFEST_FILE).decode("utf-8"))
            if manifest.get("format") != "Math Question Studio Backup" or manifest.get("version") != BACKUP_VERSION:
                raise BackupError("这不是受支持的题库备份文件。")
            payload = json.loads(archive.read(STATE_FILE).decode("utf-8"))
            state = repository.state_from_payload(payload)
            image_sources: dict[str, Path] = {}
    except (OSError, zipfile.BadZipFile, KeyError, json.JSONDecodeError) as error:
        raise BackupError(f"无法读取备份：{error}") from error

    # TemporaryDirectory cannot outlive this function, so use a deterministic
    # sibling staging folder that the caller removes after applying the import.
    staging = source.parent / f".{source.stem}-restore-images"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        with zipfile.ZipFile(source) as archive:
            for question in state.questions:
                for archive_path in question.all_images:
                    normalized = Path(archive_path)
                    if normalized.is_absolute() or ".." in normalized.parts or normalized.parts[0] != IMAGE_DIRECTORY:
                        raise BackupError("备份中包含不安全的图片路径。")
                    target = staging / normalized.name
                    with archive.open(archive_path) as image_file, target.open("wb") as output:
                        shutil.copyfileobj(image_file, output)
                    image_sources[archive_path] = target
    except (OSError, KeyError, zipfile.BadZipFile) as error:
        shutil.rmtree(staging, ignore_errors=True)
        raise BackupError(f"无法提取备份图片：{error}") from error
    return RestoredBackup(state=state, image_sources=image_sources, staging_directory=staging)


def apply_restored_backup(
    restored: RestoredBackup,
    current: AppState,
    image_directory: Path,
    *,
    merge: bool,
) -> AppState:
    """Copy images into the current library and return an imported state."""
    image_directory.mkdir(parents=True, exist_ok=True)
    incoming = restored.state
    if merge:
        questions_by_id = {question.id: question for question in current.questions}
        questions_by_id.update({question.id: question for question in incoming.questions})
        merged_baskets = _merge_baskets(current.baskets, incoming.baskets)
        result = AppState(
            questions=list(questions_by_id.values()),
            method_tag_library=_unique([*current.method_tag_library, *incoming.method_tag_library]),
            learning_tag_library=_unique([*current.learning_tag_library, *incoming.learning_tag_library]),
            baskets=merged_baskets,
            active_basket_id=current.active_basket_id or incoming.active_basket_id,
            vmware_profile=current.vmware_profile,
        )
    else:
        result = AppState(
            questions=incoming.questions,
            method_tag_library=incoming.method_tag_library,
            learning_tag_library=incoming.learning_tag_library,
            baskets=incoming.baskets,
            active_basket_id=incoming.active_basket_id,
            vmware_profile=current.vmware_profile,
        )

    try:
        for question in result.questions:
            for paths in [question.image_paths, question.analysis_image_paths]:
                for index, path in enumerate(paths):
                    source = restored.image_sources.get(path)
                    if source and source.is_file():
                        target = image_directory / source.name
                        shutil.copy2(source, target)
                        paths[index] = str(target.resolve())
            question.image_path = next(iter(question.image_paths), "")
    finally:
        shutil.rmtree(restored.staging_directory, ignore_errors=True)
    return result


def _merge_baskets(current: list[QuestionBasket], incoming: list[QuestionBasket]) -> list[QuestionBasket]:
    merged = {basket.id: QuestionBasket(basket.id, basket.name, list(basket.question_ids)) for basket in current}
    names = {basket.name for basket in current}
    for basket in incoming:
        if basket.id in merged:
            merged[basket.id].question_ids = _unique([*merged[basket.id].question_ids, *basket.question_ids])
            continue
        name = basket.name
        suffix = 2
        while name in names:
            name = f"{basket.name}（导入 {suffix}）"
            suffix += 1
        names.add(name)
        merged[basket.id] = QuestionBasket(basket.id, name, list(basket.question_ids))
    return list(merged.values())


def discard_restored_backup(restored: RestoredBackup) -> None:
    """Remove staged files when the user cancels a restore."""
    shutil.rmtree(restored.staging_directory, ignore_errors=True)


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
