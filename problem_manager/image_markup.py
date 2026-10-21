from __future__ import annotations

import re
import hashlib
from pathlib import Path

IMAGE_MARKER = re.compile(
    r"!\[[^\]]*\]\(image:(?P<markdown>[A-Za-z0-9_.-]+)\)(?:\{width=(?P<width>auto|\d+(?:\.\d+)?%?)\})?"
    r"|\[\[image:(?P<legacy>[A-Za-z0-9_.-]+)(?:\|width=(?P<legacy_width>auto|\d+(?:\.\d+)?%?))?\]\]"
)


def image_marker(path: str) -> str:
    key = hashlib.sha256(Path(path).name.encode("utf-8")).hexdigest()[:12]
    return f"[[image:{key}]]"


def image_key(path: str) -> str:
    return image_marker(path)[8:-2]


def image_code(path: str, width: int | None = None) -> str:
    suffix = "" if width is None else f"{{width={width}%}}"
    return f"![图片](image:{image_key(path)})" + suffix


def _image_lookup(paths):
    return {key: path for path in paths for key in (Path(path).name, image_key(path))}


def image_code_settings(text: str, paths: list[str]) -> dict[str, int | None]:
    lookup = _image_lookup(paths)
    result = {}
    for match in IMAGE_MARKER.finditer(text):
        path = lookup.get(match.group("markdown") or match.group("legacy"))
        if path:
            width = match.group("width") or match.group("legacy_width")
            result[image_marker(path)] = None if not width or width == "auto" else min(100, max(10, round(float(width.rstrip("%")))))
    return result


def replace_image_code(text: str, old_path: str, new_path: str, width: int | None = None, *, preserve_width: bool = False) -> str:
    def replace(match):
        if (match.group("markdown") or match.group("legacy")) not in (Path(old_path).name, image_key(old_path)):
            return match.group(0)
        value = width
        if preserve_width:
            raw = match.group("width") or match.group("legacy_width")
            if raw:
                value = None if raw == "auto" else min(100, max(10, round(float(raw.rstrip("%")))))
        return image_code(new_path, value)
    return IMAGE_MARKER.sub(replace, text)


def remove_image_code(text: str, path: str) -> str:
    return IMAGE_MARKER.sub(lambda match: "" if (match.group("markdown") or match.group("legacy")) in (Path(path).name, image_key(path)) else match.group(0), text)


def split_image_content(text: str, paths: list[str]):
    images = _image_lookup(paths)
    used = set()
    offset = 0
    for match in IMAGE_MARKER.finditer(text):
        if match.start() > offset:
            yield "text", text[offset:match.start()], offset
        path = images.get(match.group("markdown") or match.group("legacy"))
        if path:
            yield "image", path, match.start()
            used.add(path)
        offset = match.end()
    if offset < len(text):
        yield "text", text[offset:], offset
    for path in paths:
        if path not in used:
            yield "image", path, len(text)
