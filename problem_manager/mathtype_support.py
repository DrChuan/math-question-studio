from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class MathTypeStatus:
    available: bool
    runtime_path: str | None
    detail: str


def detect_mathtype() -> MathTypeStatus:
    candidates = [
        Path("/Applications/MathType.app"),
        Path.home() / "Applications" / "MathType.app",
    ]
    for candidate in candidates:
        if candidate.exists():
            return MathTypeStatus(True, str(candidate), f"已检测到可调用的 MathType 运行环境：{candidate}")

    installer_candidates = [
        Path.home() / "Virtual Machines.localized" / "MathType-mac.pkg",
        Path.home() / "Virtual Machines.localized" / "MathType永久最新mac电脑马丁(1).zip",
    ]
    for candidate in installer_candidates:
        if candidate.exists():
            return MathTypeStatus(
                False,
                None,
                f"发现安装包 {candidate.name}，但未检测到已安装且可调用的 MathType 运行环境。",
            )

    return MathTypeStatus(False, None, "未检测到已安装且可调用的 MathType 运行环境。")
