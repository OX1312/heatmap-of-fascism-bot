import json
import os
import pathlib
import tempfile
from pathlib import Path
from typing import Any, Union

def load_json(path: pathlib.Path, default: Any) -> Any:
    """Load JSON safely.
    If file is missing or invalid JSON, return default.
    """
    try:
        if not path.exists():
            return default
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        # Caller handles logging if needed, or we just fail safe to default
        return default

def save_json(path: Union[str, pathlib.Path], obj: Any) -> None:
    """
    Atomic JSON write.
    Important: temp file MUST be unique (launchd overlap can cause .tmp collisions).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    data = json.dumps(obj, ensure_ascii=False, indent=2)
    if not data.endswith("\n"):
        data += "\n"

    fd = None
    tmp_name = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        fd = None
        os.replace(tmp_name, path)
        tmp_name = None
    finally:
        try:
            if fd is not None:
                os.close(fd)
        except Exception:
            pass
        try:
            if tmp_name:
                Path(tmp_name).unlink(missing_ok=True)
        except Exception:
            pass

def ensure_file(path: pathlib.Path, default_content: Any) -> None:
    if not path.exists():
        save_json(path, default_content)
