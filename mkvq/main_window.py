# mkvq/main_window.py
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QLabel, QHBoxLayout, QPushButton, QSplitter,
    QTextEdit, QTreeWidgetItem, QProgressBar, QMenu, QFileDialog, QHeaderView, QDialog
)

from .utils.settings import load_settings, save_settings
from .utils.paths import safe_name, find_disc_roots_in_folder, find_disc_roots_with_structure, make_source_spec, is_iso
from .models.job import Job
from .workers.info_probe import InfoProbeWorker
from .workers.ripper import MakeMKVWorker
from .widgets.queue_tree import DropTree
from .widgets.details_panel import DetailsPanel
from .dialogs.prefs import PrefsDialog
from .parsers.makemkv_info import duration_to_seconds

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MakeMKV Queue")
        self.resize(1280, 820)
        self.settings = load_settings()
        Path(self.settings["output_root"]).mkdir(parents=True, exist_ok=True)
        save_settings(self.settings)
        self._updating_checks = False

        self.queue_label = QLabel("Queue: 0/0 done • 0 left")
        self.queue_label.setStyleSheet("font-weight:600;")

        self.tree = DropTree()
        # 8-column layout for enhanced title information
        self.tree.setColumnCount(8)
        self.tree.setHeaderLabels([
            "Source/Title",      # 0: Source path (parent) / Title # (child)
            "Video Codec",       # 1: Empty (parent) / Video codec (child)
            "Audio",       # 2: Empty (parent) / Audio codec (child)
            "Subtitles",         # 3: Empty (parent) / Subtitle count (child)
            "Chapters",          # 4: Empty (parent) / Chapter count (child)
            "Duration",          # 5: Empty (parent) / Duration (child)
            "Status",            # 6: Status (parent) / Empty (child)
            "Progress"           # 7: Progress bar (parent) / Empty (child)
        ])
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._row_menu)
        self.tree.itemChanged.connect(self._on_item_checked)
        self.tree.currentItemChanged.connect(self._on_current_item_changed)
        self.tree.pathsDropped.connect(self._add_paths)
        self.tree.itemsReordered.connect(self._on_jobs_reordered)

        hdr = self.tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)         # Source/Title - stretches
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents) # Video Codec
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents) # Audio
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents) # Subtitles
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents) # Chapters
        hdr.setSectionResizeMode(5, QHeaderView.ResizeToContents) # Duration
        hdr.setSectionResizeMode(6, QHeaderView.ResizeToContents) # Status
        hdr.setSectionResizeMode(7, QHeaderView.ResizeToContents) # Progress

        self.details = DetailsPanel()

        self.center_split = QSplitter(Qt.Horizontal)
        self.center_split.addWidget(self.tree)
        self.center_split.addWidget(self.details)
        self.center_split.setSizes([980, 360])

        self.console = QTextEdit(); self.console.setReadOnly(True)
        self.console.setPlaceholderText("makemkvcon output will appear here…")

        self.v_split = QSplitter(Qt.Vertical)
        self.v_split.addWidget(self.center_split)
        self.v_split.addWidget(self.console)
        self.v_split.setSizes([620, 220])

        self.btn_add_iso = QPushButton("Add ISO(s)…"); self.btn_add_iso.clicked.connect(self.add_isos)
        self.btn_add_folder = QPushButton("Add Folder(s)…"); self.btn_add_folder.clicked.connect(self.add_folders)
        self.btn_remove = QPushButton("Remove Selected"); self.btn_remove.clicked.connect(self.remove_selected)
        self.btn_clear = QPushButton("Clear"); self.btn_clear.clicked.connect(self.clear_all)
        self.btn_set_out = QPushButton("Set Output Root…"); self.btn_set_out.clicked.connect(self.set_output_root)
        self.btn_start = QPushButton("Start Queue"); self.btn_start.clicked.connect(self.start_queue)
        self.btn_stop = QPushButton("Stop"); self.btn_stop.setEnabled(False); self.btn_stop.clicked.connect(self.stop_queue)

        top = QHBoxLayout()
        for b in (self.btn_add_iso, self.btn_add_folder, self.btn_remove, self.btn_clear, self.btn_set_out, self.btn_start, self.btn_stop): top.addWidget(b)
        top.addStretch()

        central = QWidget(); v = QVBoxLayout(central)
        v.addWidget(self.queue_label); v.addLayout(top); v.addWidget(self.v_split)
        self.setCentralWidget(central)

        m = self.menuBar().addMenu("&Options")
        act_prefs = QAction("Preferences…", self); act_prefs.triggered.connect(self.open_prefs); m.addAction(act_prefs)

        self.jobs: list[Job] = []
        self.running = False
        self.completed_jobs = {}
        self.current_job_row: int | None = None

        self.probe_worker = InfoProbeWorker(self.settings)
        self.probe_thread = QThread(self); self.probe_worker.moveToThread(self.probe_thread)
        self.probe_worker.probed.connect(self._on_probed)
        self.probe_thread.start()

        self.worker = MakeMKVWorker(self.settings)
        self.work_thread = QThread(self); self.worker.moveToThread(self.work_thread)
        self.worker.progress.connect(self.on_progress)
        self.worker.status_text.connect(self.on_status_text)
        self.worker.line_out.connect(self.on_line)
        self.worker.job_done.connect(self.on_done)
        self.work_thread.started.connect(self.worker.run)

        self._restore_layout()
        self._refresh_queue_label()

    def _restore_layout(self):
        if cw := self.settings.get("col_widths"):
            if len(cw) == self.tree.columnCount():
                for i, w in enumerate(cw): self.tree.setColumnWidth(i, int(w))
        if cs := self.settings.get("center_split_sizes"): self.center_split.setSizes([int(x) for x in cs])
        if vs := self.settings.get("v_split_sizes"): self.v_split.setSizes([int(x) for x in vs])

    def _save_layout(self):
        self.settings["col_widths"] = [self.tree.columnWidth(i) for i in range(self.tree.columnCount())]
        self.settings["center_split_sizes"] = self.center_split.sizes()
        self.settings["v_split_sizes"] = self.v_split.sizes()
        save_settings(self.settings)

    def closeEvent(self, e):
        if hasattr(self, 'worker') and self.worker: self.worker.stop()
        if hasattr(self, 'work_thread') and self.work_thread.isRunning(): self.work_thread.quit(); self.work_thread.wait(3000)
        if hasattr(self, 'probe_thread') and self.probe_thread.isRunning(): self.probe_thread.quit(); self.probe_thread.wait(3000)
        self._save_layout()
        super().closeEvent(e)

    def _row_menu(self, pos):
        if not (item := self.tree.itemAt(pos)): return
        job = item.data(0, Qt.UserRole)
        if not isinstance(job, Job) and item.parent(): job = item.parent().data(0, Qt.UserRole)
        if not isinstance(job, Job): return

        menu = QMenu(self)
        def _open(p: Optional[Path]):
            if p and Path(p).exists(): QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))

        act_open_out = QAction("Open Output Folder", self); act_open_out.triggered.connect(lambda: _open(job.out_dir)); menu.addAction(act_open_out)
        act_open_log = QAction("Open Log File", self); act_open_log.triggered.connect(lambda: _open(job.log_path)); menu.addAction(act_open_log)
        menu.addSeparator()
        act_copy_cmd = QAction("Copy makemkvcon Command", self); act_copy_cmd.triggered.connect(lambda: QGuiApplication.clipboard().setText(job.cmdline or "")); menu.addAction(act_copy_cmd)

        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def add_isos(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select ISO files", str(Path.home()), "Images (*.iso *.img *.bin *.nrg);;All files (*)")
        if files: self._add_paths(files)

    def add_folders(self):
        d = QFileDialog.getExistingDirectory(self, "Choose disc folder", str(Path.home()))
        if d: self._add_paths([d])

    def _add_paths(self, paths):
        for p_str in paths:
            if not (pth := Path(p_str)).exists():
                continue

            # Use the enhanced structure-aware discovery
            disc_infos = find_disc_roots_with_structure(pth)

            # Determine if this should be treated as a group
            group_name = pth.name if pth.is_dir() and len(disc_infos) > 1 else None

            for disc_info in disc_infos:
                self._queue_one_with_structure(disc_info, group_name)

        self._refresh_queue_label()

    def _queue_one_with_structure(self, disc_info, group_name: str | None):
        """Queue a job with structure information from DiscInfo."""
        job = Job(
            source_type="iso" if is_iso(disc_info.disc_path) else "folder",
            source_path=str(disc_info.disc_path),
            source_spec=make_source_spec(disc_info.disc_path),
            child_name=disc_info.display_name,
            group_root=group_name,
            # NEW: Structure preservation info
            relative_path=disc_info.relative_path,
            drop_root=disc_info.drop_root,
            preserve_structure=True  # Could be made configurable in preferences
        )

        self.jobs.append(job)
        # Parent items show: source path, empty, empty, empty, empty, empty, status, progress
        item = QTreeWidgetItem([job.source_path, "", "", "", "", "", "Queued", ""])
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(0, Qt.CheckState.Checked)
        item.setData(0, Qt.UserRole, job)
        self.tree.addTopLevelItem(item)

        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setFixedHeight(12)
        bar.setTextVisible(True)
        self.tree.setItemWidget(item, 7, bar)  # Progress bar in column 7

        self.probe_worker.probe(self.tree.indexOfTopLevelItem(item), job)

    def _queue_one(self, src_path: Path, child_name: str, group_name: str | None):
        """Legacy method - converts to structure-aware version."""
        from .utils.paths import DiscInfo

        # Create a minimal DiscInfo for backward compatibility
        disc_info = DiscInfo(
            disc_path=src_path,
            display_name=child_name,
            relative_path=Path("."),
            drop_root=src_path.parent if src_path.is_file() else src_path
        )

        self._queue_one_with_structure(disc_info, group_name)

    def _on_jobs_reordered(self):
        new_jobs = [self.tree.topLevelItem(i).data(0, Qt.UserRole) for i in range(self.tree.topLevelItemCount())]
        self.jobs = [j for j in new_jobs if isinstance(j, Job)]
        self.console.append("Queue order changed.")
        self._refresh_queue_label()

    def _get_dominant_codec(self, streams: list, stream_type: str) -> str:
        """Get the most common codec of a given type from streams."""
        codecs = [s.get('codec', '') for s in streams if s.get('kind') == stream_type and s.get('codec')]
        if not codecs:
            return ""
        # Return the most common codec, or first if tie
        from collections import Counter
        most_common = Counter(codecs).most_common(1)
        return most_common[0][0] if most_common else ""

    def _on_probed(self, row: int, label: str | None, titles_total: int | None, titles_info: dict | None, err: str):
        if not (0 <= row < len(self.jobs)): return
        job, item = self.jobs[row], self.tree.topLevelItem(row)
        if not item: return

        if label: job.label_hint = label
        if titles_total is not None:
            # Don't set column text for parent - we'll show title count via children
            pass
        job.titles_total = titles_total

        if titles_info:
            job.titles_info = titles_info
            self._updating_checks = True
            try:
                item.takeChildren()
                minlen, any_child = int(self.settings.get("minlength", 0)), False

                for t_idx in sorted(titles_info):
                    info = titles_info[t_idx]
                    if (secs := duration_to_seconds(info.get("duration"))) and secs < minlen:
                        continue

                    # Extract title information for display
                    streams = info.get("streams", [])
                    video_codec = self._get_dominant_codec(streams, "Video")

                    # Count audio tracks
                    audio_count = len([s for s in streams if s.get('kind') == 'Audio'])
                    audio_text = str(audio_count) if audio_count > 0 else ""

                    # Count subtitles
                    subtitle_count = len([s for s in streams if s.get('kind') == 'Subtitles'])
                    subtitle_text = str(subtitle_count) if subtitle_count > 0 else ""

                    # Get chapters and duration
                    chapters = info.get("chapters", 0)
                    chapter_text = str(chapters) if chapters > 0 else ""
                    duration = info.get("duration", "")

                    # Child items show: title #, video codec, audio count, subtitle count, chapters, duration, empty, empty
                    child = QTreeWidgetItem([
                        f"#{t_idx}",      # Title number
                        video_codec,     # Video codec
                        audio_text,      # Audio track count
                        subtitle_text,   # Subtitle count
                        chapter_text,    # Chapter count
                        duration,        # Duration
                        "",              # Status (empty for children)
                        ""               # Progress (empty for children)
                    ])
                    child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                    child.setCheckState(0, Qt.CheckState.Checked)
                    item.addChild(child)
                    any_child = True

                if not any_child:
                    c = QTreeWidgetItem(["(no titles ≥ minlength)", "", "", "", "", "", "", ""])
                    c.setDisabled(True)
                    item.addChild(c)
                    job.selected_titles = set()
                else:
                    item.setCheckState(0, Qt.CheckState.Checked)
                    job.selected_titles = None

                if (cur := self.tree.currentItem()) and (cur is item or cur.parent() is item):
                    self._show_details_for_selection(cur)
            finally:
                self._updating_checks = False

        item.setText(6, "Ready" if not err else f"Probe error: {err}")  # Status in column 6

    def _set_children_check(self, parent_item: QTreeWidgetItem, state: Qt.CheckState):
        for i in range(parent_item.childCount()):
            if (ch := parent_item.child(i)).flags() & Qt.ItemIsUserCheckable: ch.setCheckState(0, state)

    def _set_parent_check_from_children(self, parent_item: QTreeWidgetItem):
        checkable = [parent_item.child(i) for i in range(parent_item.childCount()) if parent_item.child(i).flags() & Qt.ItemIsUserCheckable]
        if not checkable: parent_item.setCheckState(0, Qt.CheckState.Unchecked); return
        checked = sum(1 for ch in checkable if ch.checkState(0) == Qt.CheckState.Checked)
        if checked == 0: parent_item.setCheckState(0, Qt.CheckState.Unchecked)
        elif checked == len(checkable): parent_item.setCheckState(0, Qt.CheckState.Checked)
        else: parent_item.setCheckState(0, Qt.CheckState.PartiallyChecked)

    def _on_item_checked(self, changed_item: QTreeWidgetItem, column: int):
        if self._updating_checks: return

        # Don't process checkbox changes during queue execution
        if self.running:
            return

        self._updating_checks = True
        try:
            top_item = changed_item if not (parent := changed_item.parent()) else parent
            if not (job := top_item.data(0, Qt.UserRole)): return
            if not parent: self._set_children_check(changed_item, changed_item.checkState(0))
            else: self._set_parent_check_from_children(parent)
            keep, total_checkable = set(), 0
            for i in range(top_item.childCount()):
                ch = top_item.child(i)
                if not (ch.flags() & Qt.ItemIsUserCheckable): continue
                total_checkable += 1
                if ch.checkState(0) == Qt.CheckState.Checked and (txt := ch.text(0)).startswith("#"):
                    try:
                        title_id = int(txt[1:])
                        keep.add(title_id)
                    except Exception:
                        pass

            if total_checkable == 0:
                job.selected_titles = set()
            elif keep and len(keep) == total_checkable:
                job.selected_titles = None
            else:
                job.selected_titles = keep
        finally: self._updating_checks = False

    def _on_current_item_changed(self, cur: Optional[QTreeWidgetItem], prev: Optional[QTreeWidgetItem]):
        self._show_details_for_selection(cur)

    def _show_details_for_selection(self, item: Optional[QTreeWidgetItem]):
        if not item: self.details.clear(); return
        job, is_title_row = item.data(0, Qt.UserRole), False
        if not isinstance(job, Job):
            if parent := item.parent(): job, is_title_row = parent.data(0, Qt.UserRole), True
        if not isinstance(job, Job): self.details.clear(); return

        if not is_title_row:
            self.details.show_disc(job.label_hint or job.child_name, job.source_path, str(job.titles_total or "?"))
            return
        try: t_idx = int(item.text(0)[1:])
        except (ValueError, IndexError): self.details.clear(); return

        info = (job.titles_info or {}).get(t_idx, {})
        self.details.show_title(t_idx, info)

    def remove_selected(self):
        if not (item := self.tree.currentItem()): return
        if item.parent(): item = item.parent()
        if (row := self.tree.indexOfTopLevelItem(item)) >= 0:
            self.tree.takeTopLevelItem(row); self.jobs.pop(row)
            self._refresh_queue_label(); self.details.clear()

    def clear_all(self):
        self.tree.clear(); self.jobs.clear(); self.console.clear(); self.details.clear()
        self.completed_jobs.clear(); self.current_job_row = None; self._refresh_queue_label()

    def set_output_root(self):
        d = QFileDialog.getExistingDirectory(self, "Choose output root", self.settings["output_root"])
        if d:
            self.settings["output_root"] = d; save_settings(self.settings)

    def start_queue(self):
        if self.running: return

        jobs_to_run = []
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if not item:
                continue

            job = item.data(0, Qt.UserRole)
            if not isinstance(job, Job):
                continue

            # Check if this disc should be processed
            should_process = False

            # If parent is fully checked, process it
            if item.checkState(0) == Qt.CheckState.Checked:
                should_process = True
            # If parent is partially checked, check if any titles are selected
            elif item.checkState(0) == Qt.CheckState.PartiallyChecked:
                # Check if any child titles are selected
                for child_idx in range(item.childCount()):
                    child = item.child(child_idx)
                    if (child.flags() & Qt.ItemIsUserCheckable and
                        child.checkState(0) == Qt.CheckState.Checked):
                        should_process = True
                        break

            if should_process:
                # Capture the current selected titles to avoid race conditions
                current_selection = job.selected_titles.copy() if isinstance(job.selected_titles, set) else job.selected_titles
                jobs_to_run.append((i, job, current_selection))

        if not jobs_to_run:
            self.console.append("=== No jobs selected to run ===")
            return

        self.console.clear(); self.console.append("=== Starting queue ===")
        self.btn_start.setEnabled(False); self.btn_stop.setEnabled(True)
        self.running, self.current_job_row = True, jobs_to_run[0][0]
        self.completed_jobs.clear()

        self.worker.settings = self.settings
        self.worker.set_jobs(jobs_to_run)
        self._refresh_queue_label(len(jobs_to_run))
        self.work_thread.start()

    def stop_queue(self):
        if self.running:
            self.worker.stop()
            self.console.append(">>> Stop requested, terminating current job…")

    def _refresh_queue_label(self, total_running: int | None = None):
        total_in_list = len(self.jobs)
        done = len(self.completed_jobs)

        if self.running and total_running is not None:
            left = max(0, total_running - done - (1 if self.running and self.current_job_row is not None else 0))
            self.queue_label.setText(f"Queue: {done}/{total_running} done • {left} left")
            if self.current_job_row is not None and self.current_job_row < len(self.jobs):
                 self.queue_label.setText(self.queue_label.text() + f" • Working on: {self.jobs[self.current_job_row].child_name}")
        else:
            self.queue_label.setText(f"Queue: {total_in_list} jobs loaded")

    def on_progress(self, row: int, pct: int):
        if 0 <= row < self.tree.topLevelItemCount():
            if item := self.tree.topLevelItem(row):
                if bar := self.tree.itemWidget(item, 7): bar.setValue(max(0, min(100, pct)))  # Progress bar in column 7

    def on_status_text(self, row: int, text: str):
        if 0 <= row < len(self.jobs):
            if item := self.tree.topLevelItem(row): item.setText(6, text)  # Status in column 6
            self.current_job_row = row
            self._refresh_queue_label(len(self.worker.jobs_to_run))

    def on_line(self, row: int, line: str):
        self.console.append(line)

    def on_done(self, row: int, ok: bool):
        if item := self.tree.topLevelItem(row):
            item.setText(6, "Done" if ok else "Failed")  # Status in column 6
            if bar := self.tree.itemWidget(item, 7): bar.setValue(100 if ok else bar.value())  # Progress bar in column 7

        self.completed_jobs[row] = ok
        jobs_running_count = len(self.worker.jobs_to_run)

        is_last_job = (len(self.completed_jobs) == jobs_running_count)

        if self.worker._stop or is_last_job:
            self.console.append("=== Queue finished ===")
            self.btn_start.setEnabled(True); self.btn_stop.setEnabled(False)
            self.running, self.current_job_row = False, None
            self.work_thread.quit()
            self._refresh_queue_label()

            # Don't re-probe or reset selections after queue finishes
            # User selections should be preserved

    def open_prefs(self):
        dlg = PrefsDialog(self.settings, self)
        if dlg.exec() == QDialog.Accepted:
            self.settings.update(dlg.get_values())
            save_settings(self.settings)
            self.console.append("Saved preferences.")
