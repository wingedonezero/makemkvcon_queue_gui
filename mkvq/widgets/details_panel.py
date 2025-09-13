# mkvq/widgets/details_panel.py
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QHeaderView
from PySide6.QtCore import Qt

class DetailsPanel(QTreeWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderLabels(["Property", "Value"])
        self.setUniformRowHeights(False)
        self.setRootIsDecorated(True)
        hdr = self.header()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)

    def show_disc(self, label: str, path: str, total_titles: str):
        self.clear()
        disc_node = QTreeWidgetItem(["Disc", label])
        self.addTopLevelItem(disc_node)
        QTreeWidgetItem(disc_node, ["Path", path])
        QTreeWidgetItem(disc_node, ["Titles Found", total_titles])
        self.expandAll()

    def show_title(self, t_idx: int, info: dict):
        self.clear()

        # Title information
        title_node = QTreeWidgetItem(["Title", f"#{t_idx}"])
        self.addTopLevelItem(title_node)

        # Basic title metadata with correct labeling
        if info.get("duration"):
            QTreeWidgetItem(title_node, ["Duration", info["duration"]])
        if info.get("size"):
            QTreeWidgetItem(title_node, ["File Size", info["size"]])
        if info.get("chapters") is not None:
            chapters_count = info["chapters"]
            QTreeWidgetItem(title_node, ["Chapters", str(chapters_count)])
        if info.get("source"):
            QTreeWidgetItem(title_node, ["Source File", info["source"]])

        # Enhanced title metadata
        if info.get("name"):
            QTreeWidgetItem(title_node, ["Title Name", info["name"]])
        if info.get("angle_info"):
            QTreeWidgetItem(title_node, ["Angle Info", info["angle_info"]])
        if info.get("segments_count") and info.get("segments_count") != "0":
            QTreeWidgetItem(title_node, ["Segments", info["segments_count"]])
        if info.get("original_title_id"):
            QTreeWidgetItem(title_node, ["Original Title ID", info["original_title_id"]])
        if info.get("datetime"):
            QTreeWidgetItem(title_node, ["Date/Time", info["datetime"]])

        # Process streams with enhanced grouping and information
        stream_groups = {"Video": None, "Audio": None, "Subtitles": None, "Other": None}

        for s in info.get("streams", []):
            kind = s.get("kind", "Other")
            if kind not in stream_groups:
                kind = "Other"

            # Create stream group if it doesn't exist
            if stream_groups.get(kind) is None:
                stream_groups[kind] = QTreeWidgetItem([kind, ""])
                self.addTopLevelItem(stream_groups[kind])

            group_node = stream_groups[kind]

            # Enhanced track description
            track_desc_parts = []

            # Language
            if s.get('lang'):
                track_desc_parts.append(s.get('lang'))

            # Codec with enhanced detection
            if s.get('codec'):
                track_desc_parts.append(f"({s.get('codec')})")

            # Channel layout for audio
            if s.get('channels_display'):
                track_desc_parts.append(s.get('channels_display'))
            elif s.get('channels_count'):
                track_desc_parts.append(f"{s.get('channels_count')} ch")

            # Resolution for video
            if s.get('res'):
                track_desc_parts.append(s.get('res'))

            # Flags
            if s.get('flags'):
                flag_text = ", ".join(s.get('flags'))
                track_desc_parts.append(f"[{flag_text}]")

            track_desc = " ".join(track_desc_parts).strip()
            if not track_desc:
                track_desc = "Unknown"

            # Create track node with enhanced info
            track_node = QTreeWidgetItem(group_node, [f"Track #{s.get('index', '?')}", track_desc])

            # Detailed track properties
            if s.get("codec"):
                QTreeWidgetItem(track_node, ["Codec", s.get("codec")])

            if s.get("lang"):
                lang_display = s.get("lang")
                if s.get("lang_code") and s.get("lang_code") != s.get("lang"):
                    lang_display += f" ({s.get('lang_code')})"
                QTreeWidgetItem(track_node, ["Language", lang_display])

            # Video-specific properties
            if s.get("res"):
                QTreeWidgetItem(track_node, ["Resolution", s.get("res")])
            if s.get("ar"):
                QTreeWidgetItem(track_node, ["Aspect Ratio", s.get("ar")])
            if s.get("fps"):
                QTreeWidgetItem(track_node, ["Frame Rate", f"{s.get('fps')} fps"])

            # Audio-specific properties
            if s.get("channels_layout"):
                QTreeWidgetItem(track_node, ["Channel Layout", s.get("channels_layout")])
            elif s.get("channels_display"):
                QTreeWidgetItem(track_node, ["Channels", s.get("channels_display")])
            elif s.get("channels_count"):
                QTreeWidgetItem(track_node, ["Channels", s.get("channels_count")])

            if s.get("sample_rate"):
                sample_rate = s.get("sample_rate")
                if sample_rate.isdigit():
                    sample_rate = f"{int(sample_rate):,} Hz"
                QTreeWidgetItem(track_node, ["Sample Rate", sample_rate])

            if s.get("sample_size"):
                QTreeWidgetItem(track_node, ["Sample Size", f"{s.get('sample_size')} bit"])

            # General properties
            if s.get("bitrate"):
                QTreeWidgetItem(track_node, ["Bitrate", s.get("bitrate")])

            if s.get("name"):
                QTreeWidgetItem(track_node, ["Stream Name", s.get("name")])

            # Stream flags
            if s.get("flags"):
                flags_text = ", ".join(s.get("flags"))
                QTreeWidgetItem(track_node, ["Flags", flags_text])

            # Debug info (can be removed later)
            if s.get("raw_codes"):
                debug_node = QTreeWidgetItem(track_node, ["Debug", "Raw Codes"])
                debug_node.setForeground(0, Qt.GlobalColor.gray)
                debug_node.setForeground(1, Qt.GlobalColor.gray)
                for code_id, code_val in s.get("raw_codes", {}).items():
                    if code_val:  # Only show non-empty values
                        QTreeWidgetItem(debug_node, [f"Code {code_id}", str(code_val)])

        self.expandAll()

        # Collapse debug sections by default
        for i in range(self.topLevelItemCount()):
            item = self.topLevelItem(i)
            self._collapse_debug_sections(item)

    def _collapse_debug_sections(self, item):
        """Recursively collapse debug sections."""
        for i in range(item.childCount()):
            child = item.child(i)
            if child.text(0) == "Debug":
                child.setExpanded(False)
            else:
                self._collapse_debug_sections(child)
