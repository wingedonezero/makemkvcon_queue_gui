# mkvq/workers/ripper.py
import os, re, shlex, subprocess, select, time, math
from pathlib import Path
from PySide6.QtCore import QObject, Signal

from ..utils.paths import safe_name, unique_dir
from ..parsers.makemkv_info import parse_label_from_info, parse_info_details, count_titles_from_info
from ..models.job import Job

_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')

def _unescape(s: str) -> str:
    return s.replace(r'\"', '"').replace(r'\\', '\\')

def _msg_to_human(line: str) -> str | None:
    if not line.startswith("MSG:"):
        return None
    m = list(_QUOTED.finditer(line))
    return _unescape(m[0].group(1)) if m else None

def _clamp01(v: float) -> float:
    if not math.isfinite(v): return 0.0
    if v < 0: return 0.0
    if v > 1: return 1.0
    return v

def _size_to_bytes(size_str: str | None) -> int:
    if not size_str: return 0
    size_str = size_str.strip().lower()
    try:
        if size_str.endswith("gb"): return int(float(size_str[:-2].strip()) * 1024**3)
        if size_str.endswith("mb"): return int(float(size_str[:-2].strip()) * 1024**2)
        if size_str.endswith("kb"): return int(float(size_str[:-2].strip()) * 1024)
        return int(size_str)
    except (ValueError, TypeError): return 0

class MakeMKVWorker(QObject):
    progress    = Signal(int, int)
    status_text = Signal(int, str)
    line_out    = Signal(int, str)
    job_done    = Signal(int, bool)

    def __init__(self, settings: dict):
        super().__init__()
        self.settings = settings
        self.jobs_to_run = []
        self._stop = False

    def stop(self): self._stop = True
    def set_jobs(self, jobs_to_run): self.jobs_to_run = jobs_to_run

    def run(self):
        for original_row, job in self.jobs_to_run:
            if self._stop:
                self.status_text.emit(original_row, "Stopped"); self.job_done.emit(original_row, False)
                break

            self.status_text.emit(original_row, "Starting…"); self.progress.emit(original_row, 0)

            try:
                if self.settings.get("reprobe_before_rip", True):
                    try:
                        out = subprocess.check_output(
                            [self.settings["makemkvcon_path"], "-r", "info", job.source_spec],
                            stderr=subprocess.STDOUT, text=True, timeout=180
                        )
                        job.label_hint  = job.label_hint  or parse_label_from_info(out)
                        job.titles_info = job.titles_info or parse_info_details(out)
                        if job.titles_total is None: job.titles_total = count_titles_from_info(out)
                    except Exception: pass

                output_root = Path(self.settings["output_root"]); output_root.mkdir(parents=True, exist_ok=True)
                base = safe_name(job.group_root) if job.group_root else None
                base_folder = output_root / (base if base else safe_name(job.label_hint or job.child_name))
                dest_dir = unique_dir(base_folder / safe_name(job.child_name) if job.group_root else base_folder)
                dest_dir.mkdir(parents=True, exist_ok=False)

                pretty_log_path = dest_dir / (f"{safe_name(job.child_name)}_makemkv.log" if job.group_root else f"{dest_dir.name}_makemkv.log")
                raw_tmp_path = dest_dir / ".mkvq_messages.tmp"
                job.out_dir, job.log_path = dest_dir, pretty_log_path

                mk, show_p, human, keep_raw, debugf = (
                    self.settings["makemkvcon_path"], bool(self.settings.get("show_percent", True)),
                    bool(self.settings.get("human_log", True)), bool(self.settings.get("keep_structured_messages", False)),
                    bool(self.settings.get("enable_debugfile", False)),
                )

                calc_title_ids = []
                if isinstance(job.selected_titles, set): calc_title_ids = sorted(list(job.selected_titles))
                elif job.titles_info: calc_title_ids = sorted(list(job.titles_info.keys()))
                title_sizes = {tid: _size_to_bytes(job.titles_info.get(tid, {}).get("size")) for tid in calc_title_ids}
                total_rip_size = sum(title_sizes.values())
                title_slices, cumulative_frac = {}, 0.0
                if total_rip_size > 0:
                    for tid in calc_title_ids:
                        size_frac = title_sizes[tid] / total_rip_size
                        title_slices[tid] = {"start": cumulative_frac, "frac": size_frac}; cumulative_frac += size_frac

                if isinstance(job.selected_titles, set) and job.selected_titles: title_args = [str(i) for i in sorted(job.selected_titles)]
                else: title_args = ["all"]

                cmd = [mk, "-r"]
                if show_p: cmd.append("--progress=-stdout")
                cmd.extend(["--messages", str(raw_tmp_path)])
                if debugf: cmd.extend(["--debug", str(dest_dir / (dest_dir.name + '_debug.log'))])
                if prof := self.settings.get("profile_path", "").strip(): cmd.extend(["--profile", prof])
                if extra := self.settings.get("extra_args", "").strip(): cmd.extend(shlex.split(extra))

                mkv_command_parts = ["mkv", job.source_spec, *title_args, str(dest_dir), f"--minlength={int(self.settings['minlength'])}"]
                cmd.extend(mkv_command_parts)

                job.cmdline = " ".join(shlex.quote(c) for c in cmd)
                self.line_out.emit(original_row, "$ " + job.cmdline)

                total_titles_to_rip = len(calc_title_ids)
                current_title_idx, in_saving_titles = 0, False
                last_prgv_x, last_prgv_z, saving_phase_start_x = 0, 65536, 0
                ema_rate, last_x_ts, prev_x = None, None, 0

                ok = False
                raw_tmp_path.touch(exist_ok=True)
                with open(pretty_log_path, "w", encoding="utf-8") as lf, \
                     subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, universal_newlines=True) as proc, \
                     open(raw_tmp_path, "r", encoding="utf-8", errors="replace") as tail:

                    tail.seek(0, os.SEEK_END)
                    last_tail_pos = tail.tell()

                    def tail_messages():
                        nonlocal last_tail_pos
                        tail.seek(last_tail_pos)
                        if not (chunk := tail.read()): return
                        last_tail_pos = tail.tell()
                        for raw in chunk.splitlines():
                            if not raw or raw.startswith("PRG"): continue
                            if out := (_msg_to_human(raw) if human else raw):
                                self.line_out.emit(original_row, out)
                                try: lf.write(out + "\n"); lf.flush()
                                except Exception: pass

                    if total_titles_to_rip > 0: self.status_text.emit(original_row, f"Title 1/{total_titles_to_rip} • 0% • ETA --:--:--")
                    else: self.status_text.emit(original_row, "Analyzing…")

                    while True:
                        if self._stop: proc.terminate(); break
                        tail_messages()
                        rl, _, _ = select.select([proc.stdout], [], [], 0.1)
                        if rl:
                            if line := proc.stdout.readline():
                                if keep_raw:
                                    try: lf.write(line); lf.flush()
                                    except Exception: pass
                                line = line.strip()
                                if mt := re.match(r'^PRGT:(\d+),', line):
                                    if int(mt.group(1)) == 5024: in_saving_titles, saving_phase_start_x = True, last_prgv_x
                                elif mc := re.match(r'^PRGC:(\d+),', line):
                                    if int(mc.group(1)) == 5017:
                                        in_saving_titles = True
                                        current_title_idx = min(current_title_idx + 1, total_titles_to_rip)
                                        if saving_phase_start_x == 0: saving_phase_start_x = last_prgv_x
                                        if total_titles_to_rip > 0: self.status_text.emit(original_row, f"Title {current_title_idx}/{total_titles_to_rip} • 0% • ETA --:--:--")
                                elif mv := re.match(r'^PRGV:(\d+),(\d+),(\d+)\s*$', line):
                                    x, z = int(mv.group(1)), int(mv.group(3)) or 65536
                                    last_prgv_x, last_prgv_z = x, z
                                    overall_pct = int((100 * x) / z)
                                    if proc.poll() is None and overall_pct == 100: overall_pct = 99
                                    self.progress.emit(original_row, max(0, min(100, overall_pct)))
                                    now = time.time()
                                    if last_x_ts is not None:
                                        dx = x - prev_x; dt = max(1e-3, now - last_x_ts); inst_rate = dx / dt
                                        ema_rate = inst_rate if ema_rate is None else (0.8*ema_rate + 0.2*inst_rate)
                                    prev_x, last_x_ts = x, now
                                    title_pct = None
                                    if in_saving_titles and total_titles_to_rip > 0 and saving_phase_start_x > 0 and current_title_idx > 0:
                                        current_tid = calc_title_ids[current_title_idx - 1]
                                        if (slice_info := title_slices.get(current_tid)) and slice_info["frac"] > 0:
                                            if saving_range := last_prgv_z - saving_phase_start_x:
                                                phase_frac = (x - saving_phase_start_x) / saving_range
                                                title_frac = (phase_frac - slice_info["start"]) / slice_info["frac"]
                                                title_pct = int(round(100 * _clamp01(title_frac)))
                                    eta_txt = "--:--:--"
                                    if ema_rate and ema_rate > 0:
                                        secs = int(max(0, z - x) / ema_rate + 0.5)
                                        h, s = divmod(secs, 3600); m, s = divmod(s, 60)
                                        eta_txt = f"{h:02d}:{m:02d}:{s:02d}"
                                    if total_titles_to_rip > 0 and current_title_idx > 0 and title_pct is not None:
                                        self.status_text.emit(original_row, f"Title {current_title_idx}/{total_titles_to_rip} • {title_pct}% • ETA {eta_txt}")
                                    else: self.status_text.emit(original_row, f"Working… {overall_pct}% • ETA {eta_txt}")
                                elif line: self.line_out.emit(original_row, line)
                        if proc.poll() is not None: tail_messages(); break
                    ok = (proc.wait() == 0)
            except FileNotFoundError: self.line_out.emit(original_row, "ERROR: makemkvcon not found. Check Preferences."); ok = False
            except Exception as e: self.line_out.emit(original_row, f"ERROR: {e}"); ok = False
            finally:
                try:
                    if raw_tmp_path.exists():
                        if self.settings.get("keep_structured_messages", False):
                            keep = dest_dir / (pretty_log_path.stem + ".raw.txt")
                            if keep.exists(): keep.unlink()
                            raw_tmp_path.rename(keep)
                        else: raw_tmp_path.unlink()
                except Exception: pass

            final_pct = 100 if ok else (int((100 * last_prgv_x) / max(1, last_prgv_z)) if last_prgv_z > 0 else 0)
            self.progress.emit(original_row, final_pct)
            self.status_text.emit(original_row, "Done" if ok else "Failed")
            self.job_done.emit(original_row, ok)
