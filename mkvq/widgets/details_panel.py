# mkvq/widgets/details_panel.py
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QHeaderView

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
        t = QTreeWidgetItem(["Disc", label])
        self.addTopLevelItem(t)
        QTreeWidgetItem(t, ["Path", path])
        QTreeWidgetItem(t, ["Titles Found", total_titles])
        self.expandAll()

    def show_title(self, t_idx: int, info: dict):
        self.clear()

        title_node = QTreeWidgetItem(["Title", f"#{t_idx}"])
        self.addTopLevelItem(title_node)
        if info.get("duration"): QTreeWidgetItem(title_node, ["Duration", info["duration"]])
        if info.get("size"): QTreeWidgetItem(title_node, ["Size", info["size"]])
        if info.get("chapters") is not None: QTreeWidgetItem(title_node, ["Chapters", str(info["chapters"])])
        if info.get("source"): QTreeWidgetItem(title_node, ["Source File", info["source"]])

        stream_groups = { "Video": None, "Audio": None, "Subtitles": None, "Other": None }
        for s in info.get("streams", []):
            kind = s.get("kind", "Other")

            if stream_groups.get(kind) is None:
                stream_groups[kind] = QTreeWidgetItem([kind, ""])
                self.addTopLevelItem(stream_groups[kind])

            group_node = stream_groups[kind]

            track_desc = s.get('lang', '')
            if s.get('codec'):
                track_desc = f"{track_desc} ({s.get('codec')})" if track_desc else s.get('codec')

            track_node = QTreeWidgetItem(group_node, [f"Track #{s.get('index')}", track_desc.strip()])

            if s.get("codec"): QTreeWidgetItem(track_node, ["Codec", s.get("codec")])
            if s.get("lang"): QTreeWidgetItem(track_node, ["Language", s.get("lang")])
            if s.get("res"): QTreeWidgetItem(track_node, ["Resolution", s.get("res")])
            if s.get("ar"): QTreeWidgetItem(track_node, ["Aspect Ratio", s.get("ar")])
            if s.get("fps"): QTreeWidgetItem(track_node, ["Frame Rate", s.get("fps")])
            if s.get("channels_layout"): QTreeWidgetItem(track_node, ["Channel Layout", s.get("channels_layout")])
            elif s.get("channels_count"): QTreeWidgetItem(track_node, ["Channels", s.get("channels_count")])
            if s.get("sample_rate"): QTreeWidgetItem(track_node, ["Sample Rate", f"{s.get('sample_rate')} Hz"])

        self.expandAll()
