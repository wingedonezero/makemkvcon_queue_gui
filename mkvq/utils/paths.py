import re
from pathlib import Path
from typing import NamedTuple

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

class DiscInfo(NamedTuple):
    """Information about a discovered disc."""
    disc_path: Path           # The actual BDMV/VIDEO_TS/ISO path
    display_name: str         # Name to show in UI
    relative_path: Path       # Relative path from drop root for structure preservation
    drop_root: Path          # Original dropped path

def find_disc_roots_in_folder(path: Path, max_depth: int = 5) -> list[tuple[Path, str]]:
    """
    Find disc roots with enhanced structure awareness.
    Returns list of (disc_path, display_name) tuples for backward compatibility.
    """
    disc_infos = find_disc_roots_with_structure(path, max_depth)
    return [(info.disc_path, info.display_name) for info in disc_infos]

def find_disc_roots_with_structure(path: Path, max_depth: int = 5) -> list[DiscInfo]:
    """
    Find disc roots recursively while preserving folder structure information.

    Args:
        path: Root path to search
        max_depth: Maximum recursion depth to prevent infinite loops

    Returns:
        List of DiscInfo objects containing path and structure information
    """
    discs: list[DiscInfo] = []
    drop_root = path.resolve()

    def _find_discs_recursive(current_path: Path, depth: int = 0) -> None:
        if depth > max_depth:
            return

        if not current_path.is_dir():
            return

        try:
            # Check if current directory contains disc structures
            video_ts = current_path / "VIDEO_TS"
            bdmv = current_path / "BDMV"

            if video_ts.is_dir():
                rel_path = current_path.relative_to(drop_root)
                discs.append(DiscInfo(
                    disc_path=video_ts,
                    display_name=current_path.name,
                    relative_path=rel_path,
                    drop_root=drop_root
                ))
                return  # Don't recurse further if we found a disc structure

            if bdmv.is_dir():
                rel_path = current_path.relative_to(drop_root)
                discs.append(DiscInfo(
                    disc_path=bdmv,
                    display_name=current_path.name,
                    relative_path=rel_path,
                    drop_root=drop_root
                ))
                return  # Don't recurse further if we found a disc structure

            # Look for ISO files in current directory
            iso_files = []
            subdirs = []

            for item in sorted(current_path.iterdir()):
                if item.is_file() and is_iso(item):
                    iso_files.append(item)
                elif item.is_dir():
                    subdirs.append(item)

            # Add any ISO files found
            for iso_file in iso_files:
                rel_path = iso_file.relative_to(drop_root)
                discs.append(DiscInfo(
                    disc_path=iso_file,
                    display_name=iso_file.stem,
                    relative_path=rel_path,
                    drop_root=drop_root
                ))

            # If we found ISO files, don't recurse into subdirectories
            # (assume this directory level contains the media we want)
            if iso_files:
                return

            # Recursively search subdirectories
            for subdir in subdirs:
                _find_discs_recursive(subdir, depth + 1)

        except (PermissionError, OSError):
            # Skip directories we can't access
            pass

    # Handle the initial path
    if path.is_file() and is_iso(path):
        return [DiscInfo(
            disc_path=path,
            display_name=path.stem,
            relative_path=Path(".") / path.name,
            drop_root=path.parent
        )]

    if path.is_dir():
        # Check if the path itself is a disc structure
        if (path / "VIDEO_TS").is_dir():
            return [DiscInfo(
                disc_path=path / "VIDEO_TS",
                display_name=path.name,
                relative_path=Path("."),
                drop_root=drop_root
            )]
        if (path / "BDMV").is_dir():
            return [DiscInfo(
                disc_path=path / "BDMV",
                display_name=path.name,
                relative_path=Path("."),
                drop_root=drop_root
            )]

        # Otherwise, search recursively
        _find_discs_recursive(path)

    return discs

def create_output_structure(disc_info: DiscInfo, output_root: Path, preserve_structure: bool = True) -> Path:
    """
    Create output directory structure that optionally preserves the input folder hierarchy.

    Args:
        disc_info: DiscInfo object with structure information
        output_root: Base output directory
        preserve_structure: Whether to preserve the nested folder structure

    Returns:
        Path to the created output directory
    """
    output_root.mkdir(parents=True, exist_ok=True)

    if not preserve_structure or disc_info.relative_path == Path("."):
        # Simple flat structure
        dest_dir = unique_dir(output_root / safe_name(disc_info.display_name))
    else:
        # Preserve nested structure
        # Build path: output_root / drop_root_name / relative_path
        drop_root_name = safe_name(disc_info.drop_root.name)

        # Convert relative path to safe names
        safe_parts = [safe_name(part) for part in disc_info.relative_path.parts]
        nested_path = Path(*safe_parts) if safe_parts else Path(".")

        if nested_path == Path("."):
            # Disc is at the root of dropped folder
            dest_dir = unique_dir(output_root / drop_root_name)
        else:
            # Nested structure: output_root / drop_root_name / safe_nested_path
            base_structure = output_root / drop_root_name / nested_path
            dest_dir = unique_dir(base_structure)

    dest_dir.mkdir(parents=True, exist_ok=True)
    return dest_dir
