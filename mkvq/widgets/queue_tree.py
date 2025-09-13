# mkvq/widgets/queue_tree.py
from pathlib import Path
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QTreeWidget, QTreeWidgetItem

class DropTree(QTreeWidget):
    pathsDropped = Signal(list)  # list[str]
    itemsReordered = Signal()    # Emitted after an internal drag-drop reorder

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)

        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setUniformRowHeights(True)
        self.setExpandsOnDoubleClick(True)

    def dragEnterEvent(self, event):
        """Accept the drag action if it contains file URLs."""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        """Accept the move action if it contains file URLs."""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        """Handle both external file drops and internal reordering."""
        # Check for external file drops first.
        if event.mimeData().hasUrls():
            paths = []
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    p = Path(url.toLocalFile())
                    if p.exists(): paths.append(str(p))
            if paths:
                self.pathsDropped.emit(paths)
                event.acceptProposedAction()
                return

        # If not an external drop, assume it's an internal move for reordering.
        super().dropEvent(event)
        self.itemsReordered.emit()
