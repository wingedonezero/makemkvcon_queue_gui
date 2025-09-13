# mkvq/utils/settings.py
import json
from pathlib import Path

# Top directory = folder that contains the `mkvq/` package (and mkv_queue.py)
def _top_dir() -> Path:
    # This file is mkvq/utils/settings.py → parents[2] is the folder above mkvq/
    return Path(__file__).resolve().parents[2]

APP_SETTINGS_FILE = _top_dir() / "mkv_queue_settings.json"

DEFAULT_SETTINGS = {
    "output_root": str(Path.home() / "MakeMKV_Out"),
    "makemkvcon_path": "makemkvcon",
    "minlength": 120,
    "profile_path": "",
    "naming_mode": "disc_or_folder",   # "disc_or_folder" or "folder_only"
    "extra_args": "",

    # Logging / progress
    "human_log": True,                 # show/save GUI-style human messages
    "enable_debugfile": False,         # --debug extra file from MakeMKV
    "show_percent": True,              # show % progress in UI

    # NEW toggles
    "reprobe_before_rip": True,        # run a quick `makemkvcon -r info` at job start
    "keep_structured_messages": False, # keep the structured MSG log; otherwise delete
    # layout persistence:
    # "col_widths": [...],
    # "center_split_sizes": [...],
    # "v_split_sizes": [...],
}

def load_settings() -> dict:
    p = APP_SETTINGS_FILE
    if p.exists():
        try:
            data = json.loads(p.read_text())
            return {**DEFAULT_SETTINGS, **data}
        except Exception:
            pass
    # First run or broken file → write defaults so the file exists in the top dir
    try:
        p.write_text(json.dumps(DEFAULT_SETTINGS, indent=2))
    except Exception:
        # As a last resort, write into CWD so you still get a file
        Path("mkv_queue_settings.json").write_text(json.dumps(DEFAULT_SETTINGS, indent=2))
    return DEFAULT_SETTINGS.copy()

def save_settings(data: dict) -> None:
    p = APP_SETTINGS_FILE
    try:
        p.write_text(json.dumps(data, indent=2))
    except Exception:
        # Last resort fallback to CWD
        Path("mkv_queue_settings.json").write_text(json.dumps(data, indent=2))
