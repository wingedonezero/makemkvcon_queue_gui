import re
from pathlib import Path

def safe_name(s: str) -> str:
    s = re.sub(r'[\\/:*?"<>|]+', " ", s).strip()
    s = re.sub(r"\s+", " ", s)
    return s or "Unnamed"

def unique_dir(base_dir: Path) -> Path:
    if not base_dir.exists():
        return base_dir
    n = 1
    while True:
        candidate = base_dir.parent / f"{base_dir.name}_{n:03d}"
        if not candidate.exists():
            return candidate
        n += 1

def is_iso(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in {".iso", ".img", ".bin", ".nrg"}

def make_source_spec(path: Path) -> str:
    return f"iso:{path}" if is_iso(path) else f"file:{path}"

def find_disc_roots_in_folder(path: Path):
    discs: list[tuple[Path, str]] = []
    if path.is_file() and is_iso(path):
        return [(path, path.stem)]
    if (path / "VIDEO_TS").is_dir():
        return [(path / "VIDEO_TS", path.name)]
    if (path / "BDMV").is_dir():
        return [(path / "BDMV", path.name)]
    if path.is_dir():
        for child in sorted(path.iterdir()):
            if child.is_file() and is_iso(child):
                discs.append((child, child.stem))
            elif child.is_dir():
                if (child / "VIDEO_TS").is_dir():
                    discs.append((child / "VIDEO_TS", child.name))
                elif (child / "BDMV").is_dir():
                    discs.append((child / "BDMV", child.name))
    return discs
