# MakeMKV Queue (PySide6)

Modular GUI for `makemkvcon` that lets you queue ISO/BDMV/VIDEO_TS sources,
pick titles, and rip in batch. It writes human-friendly logs per disc and
names output folders by disc label or input folder.

## Run

```bash
python -m mkvq.app
```

(Requires Python 3.9+ and `PySide6` installed.)

## Notes
- Drag & drop ISO/folders into the queue.
- Right panel shows stream details (Video / Audio / Subtitles).
- Preferences: output root, `makemkvcon` path, minlength filter, optional profile.
- Progress bars show per-row progress. Logs stream to the bottom panel.
- Layout (column widths & splitters) is persisted in `mkv_queue_settings.json` next to the package.
