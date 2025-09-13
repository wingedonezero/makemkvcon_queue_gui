# mkvq/workers/ripper.py
import os
import re
import shlex
import subprocess
import select
import time
import math
from pathlib import Path
from PySide6.QtCore import QObject, Signal

from ..utils.paths import safe_name, unique_dir, DiscInfo, create_output_structure
from ..parsers.makemkv_info import parse_label_from_info, parse_info_details, count_titles_from_info
from ..models.job import Job

_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _unescape(s: str) -> str:
    return s.replace(r'\"', '"').replace(r"\\", "\\")


def _msg_to_human(line: str) -> str | None:
    if not line.startswith("MSG:"):
        return None
    m = list(_QUOTED.finditer(line))
    return _unescape(m[0].group(1)) if m else None


def _clamp01(v: float) -> float:
    if not math.isfinite(v):
        return 0.0
    if v < 0:
        return 0.0
    if v > 1:
        return 1.0
    return v


def _size_to_bytes(size_str: str | None) -> int:
    if not size_str:
        return 0
    size_str = size_str.strip().lower()
    try:
        if size_str.endswith("gb"):
            return int(float(size_str[:-2].strip()) * 1024**3)
        if size_str.endswith("mb"):
            return int(float(size_str[:-2].strip()) * 1024**2)
        if size_str.endswith("kb"):
            return int(float(size_str[:-2].strip()) * 1024)
        return int(size_str)
    except (ValueError, TypeError):
        return 0


class MakeMKVWorker(QObject):
    progress = Signal(int, int)
    status_text = Signal(int, str)
    line_out = Signal(int, str)
    job_done = Signal(int, bool)

    def __init__(self, settings: dict):
        super().__init__()
        self.settings = settings
        self.jobs_to_run = []
        self._stop = False

    def stop(self):
        self._stop = True

    def set_jobs(self, jobs_to_run):
        self.jobs_to_run = jobs_to_run

    def run(self):
        for job_data in self.jobs_to_run:
            # Handle both old (row, job) and new (row, job, selection) formats
            if len(job_data) == 3:
                original_row, job, captured_selection = job_data
            else:
                original_row, job = job_data
                captured_selection = job.selected_titles

            if self._stop:
                self.status_text.emit(original_row, "Stopped")
                self.job_done.emit(original_row, False)
                break

            self.status_text.emit(original_row, "Starting…")
            self.progress.emit(original_row, 0)

            try:
                # Single reprobe at job start (not per title)
                if self.settings.get("reprobe_before_rip", True):
                    self.status_text.emit(original_row, "Probing disc…")
                    try:
                        out = subprocess.check_output(
                            [self.settings["makemkvcon_path"], "-r", "info", job.source_spec],
                            stderr=subprocess.STDOUT,
                            text=True,
                            timeout=180,
                        )
                        job.label_hint = job.label_hint or parse_label_from_info(out)
                        job.titles_info = job.titles_info or parse_info_details(out)
                        if job.titles_total is None:
                            job.titles_total = count_titles_from_info(out)
                        self.line_out.emit(original_row, f"Probed disc: {job.titles_total or '?'} titles found")
                    except Exception as e:
                        self.line_out.emit(original_row, f"Probe failed: {e}")

                output_root = Path(self.settings["output_root"])
                output_root.mkdir(parents=True, exist_ok=True)

                # Enhanced structure-aware output directory creation
                if (
                    hasattr(job, "relative_path")
                    and job.relative_path
                    and hasattr(job, "drop_root")
                    and job.drop_root
                ):
                    # Enhanced structure preservation
                    disc_info = DiscInfo(
                        disc_path=Path(job.source_path),
                        display_name=job.child_name,
                        relative_path=job.relative_path,
                        drop_root=job.drop_root,
                    )

                    preserve_structure = getattr(job, "preserve_structure", True)
                    dest_dir = create_output_structure(disc_info, output_root, preserve_structure)

                else:
                    # Fallback to original logic for backward compatibility
                    base = safe_name(job.group_root) if job.group_root else None
                    base_folder = output_root / (
                        base if base else safe_name(job.label_hint or job.child_name)
                    )
                    dest_dir = unique_dir(
                        base_folder / safe_name(job.child_name) if job.group_root else base_folder
                    )
                    dest_dir.mkdir(parents=True, exist_ok=True)

                pretty_log_path = dest_dir / (
                    f"{safe_name(job.child_name)}_makemkv.log"
                    if job.group_root
                    else f"{dest_dir.name}_makemkv.log"
                )
                job.out_dir, job.log_path = dest_dir, pretty_log_path

                mk, show_p, human, keep_raw, debugf = (
                    self.settings["makemkvcon_path"],
                    bool(self.settings.get("show_percent", True)),
                    bool(self.settings.get("human_log", True)),
                    bool(self.settings.get("keep_structured_messages", False)),
                    bool(self.settings.get("enable_debugfile", False)),
                )

                # Determine which titles to rip
                if isinstance(captured_selection, set):
                    if captured_selection:
                        # Non-empty set: specific titles selected
                        titles_to_rip = sorted(list(captured_selection))
                    else:
                        # Empty set: no titles selected, skip this job
                        self.line_out.emit(original_row, "No titles selected - skipping job")
                        self.job_done.emit(original_row, True)  # Mark as completed (skipped)
                        continue
                else:
                    # None: all titles selected
                    if job.titles_info:
                        titles_to_rip = sorted(list(job.titles_info.keys()))
                    else:
                        # Fallback: try to rip everything
                        self.line_out.emit(original_row, "All titles selected but no title info - using 'all'")
                        titles_to_rip = ["all"]

                total_titles_to_rip = len(titles_to_rip)
                overall_success = True

                self.line_out.emit(original_row, f"Processing {total_titles_to_rip} title(s)")

                # Process each title individually
                for title_idx, title_id in enumerate(titles_to_rip):
                    if self._stop:
                        self.status_text.emit(original_row, "Stopped")
                        overall_success = False
                        break

                    current_title_num = title_idx + 1

                    # Create unique message file for each title
                    raw_tmp_path = dest_dir / f".mkvq_messages_title_{title_id}.tmp"

                    # Build command for this specific title
                    cmd = [mk, "-r"]
                    if show_p:
                        cmd.append("--progress=-stdout")
                    cmd.extend(["--messages", str(raw_tmp_path)])
                    if debugf:
                        cmd.extend(["--debug", str(dest_dir / f"{dest_dir.name}_title_{title_id}_debug.log")])
                    if prof := self.settings.get("profile_path", "").strip():
                        cmd.extend(["--profile", prof])
                    if extra := self.settings.get("extra_args", "").strip():
                        cmd.extend(shlex.split(extra))

                    # Single title command: makemkvcon mkv source title_id dest_dir
                    mkv_command_parts = [
                        "mkv",
                        job.source_spec,
                        str(title_id),  # Single title only
                        str(dest_dir),
                        f"--minlength={int(self.settings['minlength'])}",
                    ]
                    cmd.extend(mkv_command_parts)

                    title_cmdline = " ".join(shlex.quote(c) for c in cmd)
                    self.line_out.emit(original_row, f"Title {current_title_num}/{total_titles_to_rip}: $ {title_cmdline}")

                    # Progress tracking for this title
                    title_success = False
                    last_prgv_x, last_prgv_z = 0, 65536
                    progress_history = []
                    last_progress_time = time.time()

                    raw_tmp_path.touch(exist_ok=True)
                    with (
                        open(pretty_log_path, "a", encoding="utf-8") as lf,  # Append mode for multiple titles
                        subprocess.Popen(
                            cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=1,
                            universal_newlines=True,
                        ) as proc,
                        open(raw_tmp_path, "r", encoding="utf-8", errors="replace") as tail,
                    ):

                        lf.write(f"\n=== Title {title_id} ({current_title_num}/{total_titles_to_rip}) ===\n")
                        lf.flush()

                        tail.seek(0, os.SEEK_END)
                        last_tail_pos = tail.tell()

                        def tail_messages():
                            nonlocal last_tail_pos
                            tail.seek(last_tail_pos)
                            if not (chunk := tail.read()):
                                return
                            last_tail_pos = tail.tell()
                            for raw in chunk.splitlines():
                                if not raw or raw.startswith("PRG"):
                                    continue
                                if out := (_msg_to_human(raw) if human else raw):
                                    self.line_out.emit(original_row, f"Title {title_id}: {out}")
                                    try:
                                        lf.write(f"Title {title_id}: {out}\n")
                                        lf.flush()
                                    except Exception:
                                        pass

                        self.status_text.emit(
                            original_row,
                            f"Title {current_title_num}/{total_titles_to_rip} (#{title_id})"
                        )

                        while True:
                            if self._stop:
                                proc.terminate()
                                break
                            tail_messages()
                            rl, _, _ = select.select([proc.stdout], [], [], 0.1)
                            if rl:
                                if line := proc.stdout.readline():
                                    if keep_raw:
                                        try:
                                            lf.write(f"RAW: {line}")
                                            lf.flush()
                                        except Exception:
                                            pass
                                    line = line.strip()

                                    # Progress parsing for individual title
                                    if mv := re.match(r"^PRGV:(\d+),(\d+),(\d+)\s*$", line):
                                        x, z = int(mv.group(1)), int(mv.group(3)) or 65536
                                        last_prgv_x, last_prgv_z = x, z

                                        # Calculate per-title percentage
                                        title_pct = int((100 * x) / z) if z > 0 else 0
                                        if proc.poll() is None and title_pct >= 100:
                                            title_pct = 99  # Never show 100% until actually done

                                        # Calculate overall job progress across all titles
                                        title_weight = 100.0 / total_titles_to_rip
                                        completed_titles_progress = title_idx * title_weight
                                        current_title_progress = (title_pct / 100.0) * title_weight
                                        overall_job_pct = int(completed_titles_progress + current_title_progress)

                                        # Send overall progress to UI
                                        self.progress.emit(original_row, max(0, min(100, overall_job_pct)))

                                        # ETA calculation
                                        now = time.time()
                                        eta_text = "--:--:--"

                                        if now - last_progress_time >= 3.0:
                                            progress_history.append((now, x))
                                            if len(progress_history) > 5:
                                                progress_history.pop(0)
                                            last_progress_time = now

                                        if len(progress_history) >= 2 and z > x:
                                            time_span = progress_history[-1][0] - progress_history[0][0]
                                            progress_span = progress_history[-1][1] - progress_history[0][1]

                                            if time_span > 0 and progress_span > 0:
                                                rate = progress_span / time_span
                                                remaining = z - x
                                                eta_seconds = int(remaining / rate)

                                                if eta_seconds < 3600 * 24:
                                                    h, remainder = divmod(eta_seconds, 3600)
                                                    m, s = divmod(remainder, 60)
                                                    eta_text = f"{h:02d}:{m:02d}:{s:02d}"

                                        # Status shows current title progress and ETA
                                        self.status_text.emit(
                                            original_row,
                                            f"Title {current_title_num}/{total_titles_to_rip} (#{title_id}) • {title_pct}% • ETA {eta_text}",
                                        )

                                    elif line:
                                        self.line_out.emit(original_row, f"Title {title_id}: {line}")
                            if proc.poll() is not None:
                                tail_messages()
                                break

                        title_success = proc.wait() == 0

                        if title_success:
                            self.line_out.emit(original_row, f"Title {title_id}: Completed successfully")
                        else:
                            self.line_out.emit(original_row, f"Title {title_id}: Failed")
                            overall_success = False

                    # Cleanup title-specific message file
                    try:
                        if raw_tmp_path.exists():
                            if self.settings.get("keep_structured_messages", False):
                                keep = dest_dir / f"{pretty_log_path.stem}_title_{title_id}.raw.txt"
                                if keep.exists():
                                    keep.unlink()
                                raw_tmp_path.rename(keep)
                            else:
                                raw_tmp_path.unlink()
                    except Exception:
                        pass

                # Store the overall command for reference (first title's command as example)
                if titles_to_rip:
                    example_cmd = [mk, "-r"] + (["--progress=-stdout"] if show_p else [])
                    example_cmd.extend(["mkv", job.source_spec, str(titles_to_rip[0]), str(dest_dir)])
                    job.cmdline = " ".join(shlex.quote(c) for c in example_cmd) + f" # (and {len(titles_to_rip)-1} more titles)" if len(titles_to_rip) > 1 else ""

            except FileNotFoundError:
                self.line_out.emit(original_row, "ERROR: makemkvcon not found. Check Preferences.")
                overall_success = False
            except Exception as e:
                self.line_out.emit(original_row, f"ERROR: {e}")
                overall_success = False

            # Final status update
            self.progress.emit(original_row, 100 if overall_success else 0)
            self.status_text.emit(original_row, "Done" if overall_success else "Failed")
            self.job_done.emit(original_row, overall_success)
