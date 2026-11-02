"""A layout that fills a line with widgets and wraps onto the next one.

Qt ships no such layout, so this is the shape of its own Flow Layout example.
The Overture page needs one because a preset's layers have to wrap against the
width they are actually given rather than a width guessed while the dialog was
being built: a group that wrapped when it did not have to made the line taller,
which moved every preset below it (#230).

sizeHint() is one line, so a layout with room to spare lays the layers out on
one; minimumSize() is the widest single layer, so a narrow dialog wraps them
instead of demanding the width (#80, #193).
"""

from qgis.PyQt.QtCore import QPoint, QRect, QSize, Qt
from qgis.PyQt.QtWidgets import QLayout, QWidget


class FlowLayout(QLayout):
    def __init__(self, parent=None, spacing=6):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    # QLayout keeps no item list of its own, so these five hold one
    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        """Neither: it wraps rather than ask for more room than sizeHint()."""
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._lay_out(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._lay_out(rect, apply=True)

    def sizeHint(self):
        """Every item on one line: the width that needs no wrapping at all."""
        size = QSize(0, 0)
        for item in self._items:
            hint = item.sizeHint()
            gap = self.spacing() if size.width() else 0
            size.setWidth(size.width() + gap + hint.width())
            size.setHeight(max(size.height(), hint.height()))
        return size + self._margins()

    def minimumSize(self):
        """The widest single item: everything else can wrap below it."""
        size = QSize(0, 0)
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size + self._margins()

    def _margins(self):
        margins = self.contentsMargins()
        return QSize(
            margins.left() + margins.right(), margins.top() + margins.bottom()
        )

    def _lay_out(self, rect, apply):
        """Place the items line by line, and return the height they take up."""
        margins = self.contentsMargins()
        left = rect.x() + margins.left()
        right = rect.right() - margins.right()
        x, y, line_height = left, rect.y() + margins.top(), 0
        for item in self._items:
            if item.isEmpty():  # a hidden widget takes up no room
                continue
            size = item.sizeHint()
            if x > left and x + size.width() - 1 > right:
                x = left
                y += line_height + self.spacing()
                line_height = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), size))
            x += size.width() + self.spacing()
            line_height = max(line_height, size.height())
        return y + line_height - rect.y() + margins.bottom()


class FlowWidget(QWidget):
    """A widget that stays as tall as the FlowLayout inside it needs to be.

    A parent QBoxLayout works out how short a child may be from its
    minimumSize(), and a FlowLayout's is one line deep on purpose - being
    allowed to go narrow and wrap is the whole point of it. So the parent
    would hand this widget one line's height and clip the lines that had
    wrapped: at the dialog's own minimum width the last Divisions and Base
    layers were left a 7px sliver of their 20px, and at a 14pt UI font three
    Base layers were cut off the moment the dialog opened, Bathymetry to
    nothing at all. A clipped checkbox cannot be read or ticked, so a layer
    simply went missing (#230).

    minimumSize() cannot fix this, because the height depends on the width
    and it is not told one: at the narrowest width every layer is on a line
    of its own, and reserving that much would make the dialog far taller
    than it ever needs to be. Asking for the wrapped height once the width
    is known is the part that works. Width does not depend on height here -
    the row is laid out across the page - so this settles in one pass.
    """

    def resizeEvent(self, event):
        super().resizeEvent(event)
        layout = self.layout()
        if layout is None or not layout.hasHeightForWidth():
            return
        needed = layout.heightForWidth(self.width())
        if needed > 0 and needed != self.minimumHeight():
            self.setMinimumHeight(needed)
