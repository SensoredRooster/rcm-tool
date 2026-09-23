"""Reusable Qt widgets for the Gamepad Signal Lab desktop UI."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QFileDialog, QFrame, QLabel, QVBoxLayout, QWidget


class MetricCard(QFrame):
    def __init__(self, title: str, value: str = "—", subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)
        title_label = QLabel(title.upper())
        title_label.setObjectName("Eyebrow")
        self.value_label = QLabel(value)
        self.value_label.setObjectName("Metric")
        self.value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("Muted")
        self.subtitle_label.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(self.value_label)
        layout.addWidget(self.subtitle_label)

    def set_value(self, value: str, subtitle: str | None = None) -> None:
        self.value_label.setText(value)
        if subtitle is not None:
            self.subtitle_label.setText(subtitle)


class LineChart(QWidget):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.title = title
        self.series: list[tuple[str, list[float], QColor]] = []
        self.zoom = 1.0
        self.offset = 0.0
        self.cursor_x: float | None = None
        self.drag_origin: float | None = None
        self.setMinimumHeight(190)
        self.setMouseTracking(True)
        self.setToolTip("Wheel to zoom • drag to pan • move cursor to inspect")

    def set_series(self, series: Sequence[tuple[str, Sequence[float], str]]) -> None:
        self.series = [(name, [float(v) for v in values], QColor(color)) for name, values, color in series]
        self.update()

    def reset_view(self) -> None:
        self.zoom = 1.0
        self.offset = 0.0
        self.update()

    def export_png(self, parent: QWidget | None = None) -> Path | None:
        path, _ = QFileDialog.getSaveFileName(parent or self, "Export graph", f"{self.title.replace(' ', '_')}.png", "PNG image (*.png)")
        if not path:
            return None
        pix = QPixmap(self.size())
        pix.fill(Qt.GlobalColor.transparent)
        self.render(pix)
        pix.save(path, "PNG")
        return Path(path)

    def wheelEvent(self, event) -> None:
        self.zoom = min(12.0, max(1.0, self.zoom * (1.25 if event.angleDelta().y() > 0 else 0.8)))
        self.offset = min(max(0.0, self.offset), max(0.0, 1.0 - 1.0 / self.zoom))
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_origin = event.position().x()

    def mouseMoveEvent(self, event) -> None:
        self.cursor_x = event.position().x()
        if self.drag_origin is not None and self.zoom > 1.0 and self.width() > 1:
            delta = (self.drag_origin - event.position().x()) / self.width() / self.zoom
            self.offset = min(max(0.0, self.offset + delta), 1.0 - 1.0 / self.zoom)
            self.drag_origin = event.position().x()
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        self.drag_origin = None

    def leaveEvent(self, event) -> None:
        self.cursor_x = None
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        bg, border, text, grid = QColor("#0E1521"), QColor("#243047"), QColor("#A8B5C9"), QColor("#1E2A3E")
        painter.setPen(QPen(border, 1))
        painter.setBrush(bg)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 14, 14)
        area = QRectF(52.0, 36.0, max(10.0, self.width() - 70.0), max(10.0, self.height() - 64.0))
        painter.setPen(text)
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QRectF(16, 10, self.width() - 32, 24), Qt.AlignmentFlag.AlignLeft, self.title)
        font.setBold(False)
        painter.setFont(font)
        for i in range(5):
            y = area.top() + area.height() * i / 4.0
            painter.setPen(QPen(grid, 1))
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
        all_values = [v for _, values, _ in self.series for v in values if math.isfinite(v)]
        if not all_values:
            painter.setPen(text)
            painter.drawText(area, Qt.AlignmentFlag.AlignCenter, "No samples yet")
            return
        global_len = max((len(values) for _, values, _ in self.series), default=0)
        visible_count = max(2, int(global_len / self.zoom))
        start = int(self.offset * max(0, global_len - 1))
        start = min(start, max(0, global_len - visible_count))
        end = min(global_len, start + visible_count)
        visible = [v for _, values, _ in self.series for v in values[start:end] if math.isfinite(v)]
        if not visible:
            return
        low, high = min(visible), max(visible)
        if math.isclose(low, high):
            pad = abs(low) * 0.05 or 1.0
            low -= pad
            high += pad
        painter.setPen(text)
        painter.drawText(QRectF(2, area.top() - 8, 46, 18), Qt.AlignmentFlag.AlignRight, f"{high:.4g}")
        painter.drawText(QRectF(2, area.bottom() - 10, 46, 18), Qt.AlignmentFlag.AlignRight, f"{low:.4g}")
        for name, values, color in self.series:
            segment = values[start:end]
            if len(segment) < 2:
                continue
            path = QPainterPath()
            started = False
            for i, value in enumerate(segment):
                if not math.isfinite(value):
                    started = False
                    continue
                x = area.left() + area.width() * i / max(1, len(segment) - 1)
                y = area.bottom() - (value - low) / (high - low) * area.height()
                if started:
                    path.lineTo(x, y)
                else:
                    path.moveTo(x, y)
                    started = True
            painter.setPen(QPen(color, 1.6))
            painter.drawPath(path)
        legend_x = area.left()
        for name, _, color in self.series:
            painter.setPen(QPen(color, 3))
            painter.drawLine(QPointF(legend_x, self.height() - 13), QPointF(legend_x + 12, self.height() - 13))
            painter.setPen(text)
            painter.drawText(QPointF(legend_x + 17, self.height() - 9), name)
            legend_x += 18 + max(65, len(name) * 7)
        if self.cursor_x is not None and area.left() <= self.cursor_x <= area.right():
            painter.setPen(QPen(QColor("#7689A5"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(self.cursor_x, area.top()), QPointF(self.cursor_x, area.bottom()))
            ratio = (self.cursor_x - area.left()) / max(1.0, area.width())
            idx = start + int(ratio * max(0, end - start - 1))
            labels = [f"{name}: {values[idx]:.5g}" for name, values, _ in self.series if 0 <= idx < len(values)]
            if labels:
                painter.setPen(text)
                painter.drawText(QRectF(area.left(), area.top() + 4, area.width(), 20), Qt.AlignmentFlag.AlignHCenter, "  |  ".join(labels))


class StickView(QWidget):
    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.label, self.x, self.y = label, 0.0, 0.0
        self.setMinimumSize(180, 180)

    def set_position(self, x: float, y: float) -> None:
        self.x = max(-1.0, min(1.0, float(x)))
        self.y = max(-1.0, min(1.0, float(y)))
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        side = min(self.width(), self.height()) - 38
        rect = QRectF((self.width() - side) / 2, 24, side, side)
        painter.setPen(QPen(QColor("#2B3A54"), 1))
        painter.setBrush(QColor("#0E1521"))
        painter.drawEllipse(rect)
        center = rect.center()
        painter.drawLine(QPointF(rect.left(), center.y()), QPointF(rect.right(), center.y()))
        painter.drawLine(QPointF(center.x(), rect.top()), QPointF(center.x(), rect.bottom()))
        px = center.x() + self.x * rect.width() * 0.45
        py = center.y() - self.y * rect.height() * 0.45
        painter.setBrush(QColor("#4F86FF"))
        painter.setPen(QPen(QColor("#8FB4FF"), 2))
        painter.drawEllipse(QPointF(px, py), 9, 9)
        painter.setPen(QColor("#A8B5C9"))
        painter.drawText(QRectF(0, 2, self.width(), 20), Qt.AlignmentFlag.AlignHCenter, self.label)


class HeatMapWidget(QWidget):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.title = title
        self.points: list[tuple[float, float, float]] = []
        self.setMinimumHeight(260)

    def set_points(self, points: Sequence[tuple[float, float, float]]) -> None:
        self.points = [(float(x), float(y), float(v)) for x, y, v in points]
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor("#243047"), 1))
        painter.setBrush(QColor("#0E1521"))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 14, 14)
        painter.setPen(QColor("#A8B5C9"))
        font = painter.font(); font.setBold(True); painter.setFont(font)
        painter.drawText(QRectF(16, 10, self.width() - 32, 24), Qt.AlignmentFlag.AlignLeft, self.title)
        area = QRectF(54, 42, max(10, self.width() - 76), max(10, self.height() - 72))
        if not self.points:
            painter.drawText(area, Qt.AlignmentFlag.AlignCenter, "Run a sweep to populate response data")
            return
        xs = [math.log10(max(x, 1e-12)) for x, _, _ in self.points]
        ys = [y for _, y, _ in self.points]
        vs = [v for _, _, v in self.points]
        xmin, xmax, ymin, ymax, vmin, vmax = min(xs), max(xs), min(ys), max(ys), min(vs), max(vs)
        if math.isclose(xmin, xmax): xmax = xmin + 1
        if math.isclose(ymin, ymax): ymax = ymin + 1
        if math.isclose(vmin, vmax): vmax = vmin + 1
        for (freq, amp, value), lx in zip(self.points, xs):
            x = area.left() + (lx - xmin) / (xmax - xmin) * area.width()
            y = area.bottom() - (amp - ymin) / (ymax - ymin) * area.height()
            ratio = max(0.0, min(1.0, (value - vmin) / (vmax - vmin)))
            color = QColor.fromHsvF((0.62 - ratio * 0.58) % 1.0, 0.72, 0.95)
            painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(color)
            painter.drawRoundedRect(QRectF(x - 9, y - 9, 18, 18), 4, 4)
        painter.setPen(QColor("#8595AB"))
        painter.drawText(QRectF(area.left(), area.bottom() + 5, area.width(), 20), Qt.AlignmentFlag.AlignHCenter, "Stimulus frequency (log scale)")
