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
                if self.settings.get("reprobe_before_rip", True):
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
                    except Exception:
                        pass

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
                raw_tmp_path = dest_dir / ".mkvq_messages.tmp"
                job.out_dir, job.log_path = dest_dir, pretty_log_path

                mk, show_p, human, keep_raw, debugf = (
                    self.settings["makemkvcon_path"],
                    bool(self.settings.get("show_percent", True)),
                    bool(self.settings.get("human_log", True)),
                    bool(self.settings.get("keep_structured_messages", False)),
                    bool(self.settings.get("enable_debugfile", False)),
                )

                # Simplified, accurate progress tracking
                calc_title_ids = []
                if isinstance(captured_selection, set):
                    calc_title_ids = sorted(list(captured_selection))
                elif job.titles_info:
                    calc_title_ids = sorted(list(job.titles_info.keys()))

                total_titles_to_rip = len(calc_title_ids)
                current_title_idx = 0

                # Handle title selection logic properly using captured selection
                if isinstance(captured_selection, set):
                    if captured_selection:
                        # Non-empty set: specific titles selected
                        title_args = [str(i) for i in sorted(captured_selection)]
                    else:
                        # Empty set: no titles selected, skip this job
                        self.line_out.emit(original_row, "No titles selected - skipping job")
                        self.job_done.emit(original_row, True)  # Mark as completed (skipped)
                        continue
                else:
                    # None: all titles selected
                    title_args = ["all"]

                cmd = [mk, "-r"]
                if show_p:
                    cmd.append("--progress=-stdout")
                cmd.extend(["--messages", str(raw_tmp_path)])
                if debugf:
                    cmd.extend(["--debug", str(dest_dir / (dest_dir.name + "_debug.log"))])
                if prof := self.settings.get("profile_path", "").strip():
                    cmd.extend(["--profile", prof])
                if extra := self.settings.get("extra_args", "").strip():
                    cmd.extend(shlex.split(extra))

                mkv_command_parts = [
                    "mkv",
                    job.source_spec,
                    *title_args,
                    str(dest_dir),
                    f"--minlength={int(self.settings['minlength'])}",
                ]
                cmd.extend(mkv_command_parts)

                job.cmdline = " ".join(shlex.quote(c) for c in cmd)
                self.line_out.emit(original_row, "$ " + job.cmdline)

                # Simple progress tracking variables
                current_title_idx = 0
                last_prgv_x, last_prgv_z = 0, 65536

                # Simple ETA calculation
                progress_history = []
                last_progress_time = time.time()

                ok = False
                raw_tmp_path.touch(exist_ok=True)
                with (
                    open(pretty_log_path, "w", encoding="utf-8") as lf,
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
                                self.line_out.emit(original_row, out)
                                try:
                                    lf.write(out + "\n")
                                    lf.flush()
                                except Exception:
                                    pass

                    if total_titles_to_rip > 0:
                        self.status_text.emit(
                            original_row, f"Starting • {total_titles_to_rip} titles selected"
                        )
                    else:
                        self.status_text.emit(original_row, "Starting…")

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
                                        lf.write(line)
                                        lf.flush()
                                    except Exception:
                                        pass
                                line = line.strip()

                                # Simple progress parsing - just track what MakeMKV reports
                                if mc := re.match(r"^PRGC:(\d+),", line):
                                    prgc_code = int(mc.group(1))
                                    if prgc_code == 5017:  # Title completed
                                        current_title_idx = min(current_title_idx + 1, total_titles_to_rip)
                                elif mv := re.match(r"^PRGV:(\d+),(\d+),(\d+)\s*$", line):
                                    x, z = int(mv.group(1)), int(mv.group(3)) or 65536
                                    last_prgv_x, last_prgv_z = x, z

                                    # Calculate per-operation percentage for status text
                                    operation_pct = int((100 * x) / z) if z > 0 else 0
                                    if proc.poll() is None and operation_pct >= 100:
                                        operation_pct = 99  # Never show 100% until actually done

                                    # Calculate overall job progress for the main progress bar
                                    if total_titles_to_rip > 0:
                                        # Each title is worth (100 / total_titles) percent of the job
                                        title_weight = 100.0 / total_titles_to_rip
                                        completed_titles = max(0, current_title_idx - 1)  # Already finished
                                        current_title_progress = (operation_pct / 100.0) * title_weight
                                        overall_job_pct = int(
                                            completed_titles * title_weight + current_title_progress
                                        )
                                    else:
                                        overall_job_pct = operation_pct

                                    # Send overall job progress to the main progress bar
                                    self.progress.emit(original_row, max(0, min(100, overall_job_pct)))

                                    # Simple ETA calculation based on current operation
                                    now = time.time()
                                    eta_text = "--:--:--"

                                    # Track progress over time for ETA
                                    if now - last_progress_time >= 3.0:  # Update every 3 seconds
                                        progress_history.append((now, x))
                                        # Keep only last 5 data points (15 seconds of history)
                                        if len(progress_history) > 5:
                                            progress_history.pop(0)
                                        last_progress_time = now

                                    if len(progress_history) >= 2 and z > x:
                                        # Simple rate calculation
                                        time_span = progress_history[-1][0] - progress_history[0][0]
                                        progress_span = progress_history[-1][1] - progress_history[0][1]

                                        if time_span > 0 and progress_span > 0:
                                            rate = progress_span / time_span  # progress units per second
                                            remaining = z - x
                                            eta_seconds = int(remaining / rate)

                                            if eta_seconds < 3600 * 24:  # Less than 24 hours
                                                h, remainder = divmod(eta_seconds, 3600)
                                                m, s = divmod(remainder, 60)
                                                eta_text = f"{h:02d}:{m:02d}:{s:02d}"

                                    # Status text shows per-title progress and ETA
                                    if total_titles_to_rip > 1 and current_title_idx > 0:
                                        self.status_text.emit(
                                            original_row,
                                            f"Title {current_title_idx}/{total_titles_to_rip} • {operation_pct}% • ETA {eta_text}",
                                        )
                                    else:
                                        self.status_text.emit(
                                            original_row, f"Working • {operation_pct}% • ETA {eta_text}"
                                        )

                                elif line:
                                    self.line_out.emit(original_row, line)
                        if proc.poll() is not None:
                            tail_messages()
                            break

                    ok = proc.wait() == 0
            except FileNotFoundError:
                self.line_out.emit(original_row, "ERROR: makemkvcon not found. Check Preferences.")
                ok = False
            except Exception as e:
                self.line_out.emit(original_row, f"ERROR: {e}")
                ok = False
            finally:
                try:
                    if raw_tmp_path.exists():
                        if self.settings.get("keep_structured_messages", False):
                            keep = dest_dir / (pretty_log_path.stem + ".raw.txt")
                            if keep.exists():
                                keep.unlink()
                            raw_tmp_path.rename(keep)
                        else:
                            raw_tmp_path.unlink()
                except Exception:
                    pass

            final_pct = (
                100
                if ok
                else (int((100 * last_prgv_x) / max(1, last_prgv_z)) if last_prgv_z > 0 else 0)
            )
            self.progress.emit(original_row, final_pct)
            self.status_text.emit(original_row, "Done" if ok else "Failed")
            self.job_done.emit(original_row, ok)
