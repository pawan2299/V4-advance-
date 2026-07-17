from __future__ import annotations

import os
from pathlib import Path


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def list_videos(folder: str | Path) -> list[Path]:
    p = Path(folder)
    return [x for x in sorted(p.iterdir()) if x.suffix.lower() in {'.mp4', '.mov', '.mkv', '.avi', '.webm'}]
