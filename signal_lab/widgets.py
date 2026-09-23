"""Reusable Qt widgets for the Gamepad Signal Lab desktop UI."""
from __future__ import annotations

from bisect import bisect_left
import math
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget
)


def _axis_text(value: float) -> str:
    value = float(value)
    magnitude = abs(value)
    if magnitude >= 1_000_000 or (magnitude and magnitude < 0.0001):
        return f"{value:.3e}"
    if magnitude >= 1000:
        return f"{value:,.1f}"
    if magnitude >= 10:
        return f"{value:.3f}"
    return f"{value:.5g}"


class MetricCard(QFrame):
    def __init__(
        self,
        title: str,
        value: str = "—",
        subtitle: str = "",
        *,
        help_text: str = "",
        source: str = "CALCULATED",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("MetricCard")
        self.setMinimumSize(190, 132)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(7)
        title_label = QLabel(title.upper())
        title_label.setObjectName("Eyebrow")
        title_label.setWordWrap(True)
        title_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        top.addWidget(title_label, 1)
        info = QLabel("?")
        info.setObjectName("InfoBadge")
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top.addWidget(info, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)

        self.value_label = QLabel(value)
        self.value_label.setObjectName("Metric")
        self.value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.value_label.setMinimumHeight(30)
        self.value_label.setWordWrap(False)
        layout.addWidget(self.value_label)

        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("Muted")
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setMinimumHeight(30)
        layout.addWidget(self.subtitle_label, 1)

        source_row = QHBoxLayout()
        source_row.addStretch(1)
        self.source_label = QLabel(source.upper())
        self.source_label.setObjectName("SourceChip")
        self.source_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        source_row.addWidget(self.source_label, 0)
        layout.addLayout(source_row)

        self.set_help(help_text)
        self._fit_value_text()

    def _fit_value_text(self) -> None:
        text = self.value_label.text()
        if not text:
            return
        available = max(110.0, float(self.width() - 34))
        point_size = 18.0
        font = self.value_label.font()
        font.setPointSizeF(point_size)
        while point_size > 12.5 and QFontMetricsF(font).horizontalAdvance(text) > available:
            point_size -= 0.5
            font.setPointSizeF(point_size)
        self.value_label.setStyleSheet(f"font-size: {point_size:.1f}pt;")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_value_text()

    def set_help(self, help_text: str) -> None:
        self.help_text = help_text.strip()
        if self.help_text:
            self.setToolTip(self.help_text)
            for child in self.findChildren(QLabel):
                child.setToolTip(self.help_text)

    def set_source(self, source: str) -> None:
        self.source_label.setText(source.upper())

    def set_value(self, value: str, subtitle: str | None = None, source: str | None = None) -> None:
        self.value_label.setText(value)
        if subtitle is not None:
            self.subtitle_label.setText(subtitle)
        if source is not None:
            self.set_source(source)
        self._fit_value_text()


class LineChart(QWidget):
    cursorRatioChanged = Signal(float)
    cursorCleared = Signal()

    def __init__(
        self,
        title: str,
        parent: QWidget | None = None,
        *,
        help_text: str = "",
        x_label: str = "Sample",
    ) -> None:
        super().__init__(parent)
        self.title = title
        self.help_text = help_text.strip()
        self.x_label = x_label
        self.series: list[tuple[str, list[float], QColor]] = []
        self.x_values: list[float] | None = None
        self.zoom = 1.0
        self.offset = 0.0
        self.cursor_x: float | None = None
        self.external_cursor_ratio: float | None = None
        self.drag_origin: float | None = None
        self.setMinimumHeight(235)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        controls = "Wheel: zoom • drag: pan • move pointer: inspect"
        self.setToolTip(f"{self.help_text}\n\n{controls}".strip())

    def set_series(
        self,
        series: Sequence[tuple[str, Sequence[float], str]],
        *,
        x_values: Sequence[float] | None = None,
        x_label: str | None = None,
    ) -> None:
        self.series = [(name, [float(v) for v in values], QColor(color)) for name, values, color in series]
        self.x_values = [float(v) for v in x_values] if x_values is not None else None
        if x_label is not None:
            self.x_label = x_label
        self.update()

    def reset_view(self) -> None:
        self.zoom = 1.0
        self.offset = 0.0
        self.update()

    def set_external_cursor_ratio(self, ratio: float | None) -> None:
        self.external_cursor_ratio = None if ratio is None else max(0.0, min(1.0, float(ratio)))
        self.update()

    def export_png(self, parent: QWidget | None = None) -> Path | None:
        path, _ = QFileDialog.getSaveFileName(
            parent or self,
            "Export graph",
            f"{self.title.replace(' ', '_')}.png",
            "PNG image (*.png)",
        )
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
        area_left = 68.0
        area_width = max(10.0, self.width() - 90.0)
        if area_left <= self.cursor_x <= area_left + area_width:
            self.cursorRatioChanged.emit((self.cursor_x - area_left) / area_width)
        if self.drag_origin is not None and self.zoom > 1.0 and self.width() > 1:
            delta = (self.drag_origin - event.position().x()) / self.width() / self.zoom
            self.offset = min(max(0.0, self.offset + delta), 1.0 - 1.0 / self.zoom)
            self.drag_origin = event.position().x()
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        self.drag_origin = None

    def leaveEvent(self, event) -> None:
        self.cursor_x = None
        self.cursorCleared.emit()
        self.update()

    def _x_segment(self, start: int, end: int) -> list[float]:
        if self.x_values is not None and len(self.x_values) >= end:
            return self.x_values[start:end]
        return [float(i) for i in range(start, end)]

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        bg, border, text, grid = QColor("#0C141F"), QColor("#23334A"), QColor("#A9B8CB"), QColor("#1C293B")
        painter.setPen(QPen(border, 1))
        painter.setBrush(bg)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 16, 16)

        area = QRectF(68.0, 42.0, max(10.0, self.width() - 90.0), max(10.0, self.height() - 92.0))
        painter.setPen(text)
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QRectF(16, 12, self.width() - 32, 24), Qt.AlignmentFlag.AlignLeft, self.title)
        font.setBold(False)
        painter.setFont(font)

        for i in range(5):
            y = area.top() + area.height() * i / 4.0
            painter.setPen(QPen(grid, 1))
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))

        all_values = [v for _, values, _ in self.series for v in values if math.isfinite(v)]
        if not all_values:
            painter.setPen(text)
            painter.drawText(area, Qt.AlignmentFlag.AlignCenter, "Unavailable")
            painter.drawText(
                QRectF(area.left(), area.bottom() + 12, area.width(), 20),
                Qt.AlignmentFlag.AlignHCenter,
                self.x_label,
            )
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

        xs = self._x_segment(start, end)
        x_low, x_high = (min(xs), max(xs)) if xs else (0.0, 1.0)
        if math.isclose(x_low, x_high):
            x_high = x_low + 1.0

        painter.setPen(text)
        painter.drawText(QRectF(2, area.top() - 8, 60, 18), Qt.AlignmentFlag.AlignRight, _axis_text(high))
        painter.drawText(QRectF(2, area.bottom() - 10, 60, 18), Qt.AlignmentFlag.AlignRight, _axis_text(low))

        for name, values, color in self.series:
            segment = values[start:end]
            if len(segment) < 2:
                continue
            x_segment = xs[:len(segment)]
            path = QPainterPath()
            started = False
            for i, value in enumerate(segment):
                if not math.isfinite(value):
                    started = False
                    continue
                xv = x_segment[i] if i < len(x_segment) else float(start + i)
                x = area.left() + (xv - x_low) / (x_high - x_low) * area.width()
                y = area.bottom() - (value - low) / (high - low) * area.height()
                if started:
                    path.lineTo(x, y)
                else:
                    path.moveTo(x, y)
                    started = True
            painter.setPen(QPen(color, 1.7))
            painter.drawPath(path)

        painter.setPen(QColor("#7E91AA"))
        painter.drawText(QRectF(area.left(), area.bottom() + 4, 90, 18), Qt.AlignmentFlag.AlignLeft, _axis_text(x_low))
        painter.drawText(QRectF(area.right() - 90, area.bottom() + 4, 90, 18), Qt.AlignmentFlag.AlignRight, _axis_text(x_high))
        painter.drawText(
            QRectF(area.left() + 90, area.bottom() + 4, max(20.0, area.width() - 180), 18),
            Qt.AlignmentFlag.AlignHCenter,
            self.x_label,
        )

        legend_x = area.left()
        for name, _, color in self.series:
            painter.setPen(QPen(color, 3))
            painter.drawLine(QPointF(legend_x, self.height() - 17), QPointF(legend_x + 12, self.height() - 17))
            painter.setPen(text)
            painter.drawText(QPointF(legend_x + 17, self.height() - 13), name)
            legend_x += 18 + max(72, len(name) * 7)

        cursor_x = self.cursor_x
        if cursor_x is None and self.external_cursor_ratio is not None:
            cursor_x = area.left() + self.external_cursor_ratio * area.width()
        if cursor_x is not None and area.left() <= cursor_x <= area.right():
            painter.setPen(QPen(QColor("#8296B2"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(cursor_x, area.top()), QPointF(cursor_x, area.bottom()))
            ratio = (cursor_x - area.left()) / max(1.0, area.width())
            target_x = x_low + ratio * (x_high - x_low)
            if xs:
                idx_local = min(range(len(xs)), key=lambda i: abs(xs[i] - target_x))
            else:
                idx_local = int(ratio * max(0, end - start - 1))
            idx = start + idx_local
            labels = [
                f"{name}: {_axis_text(values[idx])}"
                for name, values, _ in self.series
                if 0 <= idx < len(values) and math.isfinite(values[idx])
            ]
            if labels:
                painter.setPen(text)
                painter.drawText(
                    QRectF(area.left(), area.top() + 4, area.width(), 20),
                    Qt.AlignmentFlag.AlignHCenter,
                    "  |  ".join(labels),
                )


class StickView(QWidget):
    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.label, self.x, self.y = label, 0.0, 0.0
        self.setMinimumSize(190, 190)
        self.setToolTip("Decoded stick position normalized to -1…+1 on each axis.")

    def set_position(self, x: float, y: float) -> None:
        self.x = max(-1.0, min(1.0, float(x)))
        self.y = max(-1.0, min(1.0, float(y)))
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        side = min(self.width(), self.height()) - 46
        rect = QRectF((self.width() - side) / 2, 28, side, side)
        painter.setPen(QPen(QColor("#2B3A54"), 1))
        painter.setBrush(QColor("#0C141F"))
        painter.drawEllipse(rect)
        center = rect.center()
        painter.drawLine(QPointF(rect.left(), center.y()), QPointF(rect.right(), center.y()))
        painter.drawLine(QPointF(center.x(), rect.top()), QPointF(center.x(), rect.bottom()))
        px = center.x() + self.x * rect.width() * 0.45
        py = center.y() - self.y * rect.height() * 0.45
        painter.setBrush(QColor("#5D93FF"))
        painter.setPen(QPen(QColor("#9EC0FF"), 2))
        painter.drawEllipse(QPointF(px, py), 9, 9)
        painter.setPen(QColor("#A8B5C9"))
        painter.drawText(QRectF(0, 3, self.width(), 20), Qt.AlignmentFlag.AlignHCenter, self.label)



class ControllerView(QWidget):
    """Live controller visual with Xbox, DualSense, and generic skins."""

    XINPUT_BUTTONS = {
        0x0001: "D-UP", 0x0002: "D-DOWN", 0x0004: "D-LEFT", 0x0008: "D-RIGHT",
        0x0010: "START", 0x0020: "BACK", 0x0040: "L3", 0x0080: "R3",
        0x0100: "LB", 0x0200: "RB", 0x1000: "A", 0x2000: "B",
        0x4000: "X", 0x8000: "Y",
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.sample: dict = {}
        self.source = ""
        self.skin = "generic"
        self.mapping_family = "generic"
        self.setMinimumHeight(390)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setToolTip(
            "Live decoded controller state. Auto skin detection is separate from input mapping. "
            "Manual skin override changes only the drawing; it never invents button mappings."
        )

    def set_state(
        self,
        sample: dict | None,
        source: str = "",
        *,
        skin: str = "generic",
        mapping_family: str = "generic",
    ) -> None:
        self.sample = dict(sample or {})
        self.source = str(source or "")
        self.skin = skin if skin in {"xbox", "dualsense", "generic"} else "generic"
        self.mapping_family = mapping_family if mapping_family in {"xbox", "dualsense", "generic"} else "generic"
        self.update()

    @staticmethod
    def _clamp(value: float) -> float:
        return max(-1.0, min(1.0, float(value)))

    @staticmethod
    def _trigger(value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    def pressed_names(self) -> list[str]:
        if "buttons" not in self.sample:
            return []
        mask = int(self.sample.get("buttons", 0))
        if self.mapping_family == "xbox":
            return [name for bit, name in self.XINPUT_BUTTONS.items() if mask & bit]
        return [f"B{index}" for index in range(32) if mask & (1 << index)]

    def _dpad_state(self) -> tuple[int, int]:
        if "dpad_x" in self.sample or "dpad_y" in self.sample:
            return int(self.sample.get("dpad_x", 0)), int(self.sample.get("dpad_y", 0))
        if self.mapping_family == "xbox" and "buttons" in self.sample:
            mask = int(self.sample.get("buttons", 0))
            dx = (1 if mask & 0x0008 else 0) - (1 if mask & 0x0004 else 0)
            dy = (1 if mask & 0x0001 else 0) - (1 if mask & 0x0002 else 0)
            return dx, dy
        if "dpad_pov" in self.sample:
            pov = int(self.sample.get("dpad_pov", 65535))
            if pov in (65535, 4294967295):
                return 0, 0
            angle = (pov / 100.0) % 360.0
            dx = 1 if 22.5 <= angle < 157.5 else (-1 if 202.5 <= angle < 337.5 else 0)
            dy = 1 if angle >= 337.5 or angle < 67.5 else (-1 if 112.5 <= angle < 247.5 else 0)
            return dx, dy
        return 0, 0

    def _draw_stick(
        self, painter: QPainter, center: QPointF, radius: float,
        x: float, y: float, label: str,
    ) -> None:
        painter.setPen(QPen(QColor("#344761"), 1.4))
        painter.setBrush(QColor("#0A121E"))
        painter.drawEllipse(center, radius, radius)
        painter.setPen(QPen(QColor("#23354C"), 1))
        painter.drawLine(QPointF(center.x() - radius, center.y()), QPointF(center.x() + radius, center.y()))
        painter.drawLine(QPointF(center.x(), center.y() - radius), QPointF(center.x(), center.y() + radius))
        dot = QPointF(
            center.x() + self._clamp(x) * radius * 0.72,
            center.y() - self._clamp(y) * radius * 0.72,
        )
        painter.setPen(QPen(QColor("#9EC0FF"), 2))
        painter.setBrush(QColor("#5D93FF"))
        painter.drawEllipse(dot, radius * 0.18, radius * 0.18)
        painter.setPen(QColor("#8FA4BE"))
        painter.drawText(
            QRectF(center.x() - radius, center.y() + radius + 6, radius * 2, 18),
            Qt.AlignmentFlag.AlignHCenter, label,
        )

    def _draw_button(
        self, painter: QPainter, center: QPointF, radius: float,
        label: str, active: bool = False,
    ) -> None:
        painter.setPen(QPen(QColor("#6A8ABC") if active else QColor("#344A68"), 1.5))
        painter.setBrush(QColor("#376FE0") if active else QColor("#111D2C"))
        painter.drawEllipse(center, radius, radius)
        painter.setPen(QColor("#FFFFFF") if active else QColor("#A9B8CA"))
        painter.drawText(
            QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2),
            Qt.AlignmentFlag.AlignCenter, label,
        )

    def _draw_dpad(self, painter: QPainter, center: QPointF, size: float) -> None:
        dx, dy = self._dpad_state()
        arm = size * 0.34
        painter.setPen(QPen(QColor("#344A68"), 1.2))
        painter.setBrush(QColor("#111D2C"))
        painter.drawRoundedRect(QRectF(center.x() - arm * 0.45, center.y() - arm * 1.35, arm * 0.9, arm * 2.7), 4, 4)
        painter.drawRoundedRect(QRectF(center.x() - arm * 1.35, center.y() - arm * 0.45, arm * 2.7, arm * 0.9), 4, 4)
        if dx or dy:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#5D93FF"))
            painter.drawEllipse(QPointF(center.x() + dx * arm * 0.9, center.y() - dy * arm * 0.9), 6, 6)

    def _draw_trigger_bars(
        self, painter: QPainter, left: float, top: float, body_w: float,
        left_label: str, right_label: str,
    ) -> None:
        lt = self._trigger(self.sample.get("lt", 0.0))
        rt = self._trigger(self.sample.get("rt", 0.0))
        bar_w = body_w * 0.20
        for x, value, label in (
            (left + body_w * 0.12, lt, left_label),
            (left + body_w * 0.68, rt, right_label),
        ):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#182840"))
            painter.drawRoundedRect(QRectF(x, top - 24, bar_w, 8), 4, 4)
            painter.setBrush(QColor("#5D93FF"))
            painter.drawRoundedRect(QRectF(x, top - 24, bar_w * value, 8), 4, 4)
            painter.setPen(QColor("#8FA4BE"))
            painter.drawText(QRectF(x, top - 43, bar_w, 16), Qt.AlignmentFlag.AlignCenter, f"{label} {value*100:.0f}%")

    @staticmethod
    def _shell_path(left: float, top: float, width: float, height: float, *, dualsense: bool = False) -> QPainterPath:
        """Balanced gamepad shell; geometry differs by family but avoids caricatured grips."""
        p = QPainterPath()
        if dualsense:
            p.moveTo(left + width * 0.20, top + height * 0.08)
            p.cubicTo(left + width * 0.10, top, left + width * 0.02, top + height * 0.20, left + width * 0.05, top + height * 0.47)
            p.cubicTo(left + width * 0.08, top + height * 0.68, left + width * 0.12, top + height * 0.98, left + width * 0.22, top + height)
            p.cubicTo(left + width * 0.29, top + height, left + width * 0.34, top + height * 0.80, left + width * 0.39, top + height * 0.68)
            p.cubicTo(left + width * 0.46, top + height * 0.77, left + width * 0.54, top + height * 0.77, left + width * 0.61, top + height * 0.68)
            p.cubicTo(left + width * 0.66, top + height * 0.80, left + width * 0.71, top + height, left + width * 0.78, top + height)
            p.cubicTo(left + width * 0.88, top + height * 0.98, left + width * 0.92, top + height * 0.68, left + width * 0.95, top + height * 0.47)
            p.cubicTo(left + width * 0.98, top + height * 0.20, left + width * 0.90, top, left + width * 0.80, top + height * 0.08)
            p.cubicTo(left + width * 0.66, top + height * 0.01, left + width * 0.34, top + height * 0.01, left + width * 0.20, top + height * 0.08)
        else:
            p.moveTo(left + width * 0.19, top + height * 0.10)
            p.cubicTo(left + width * 0.08, top + height * 0.03, left + width * 0.02, top + height * 0.23, left + width * 0.06, top + height * 0.48)
            p.cubicTo(left + width * 0.09, top + height * 0.70, left + width * 0.12, top + height * 0.94, left + width * 0.21, top + height * 0.98)
            p.cubicTo(left + width * 0.29, top + height, left + width * 0.34, top + height * 0.76, left + width * 0.39, top + height * 0.66)
            p.cubicTo(left + width * 0.46, top + height * 0.73, left + width * 0.54, top + height * 0.73, left + width * 0.61, top + height * 0.66)
            p.cubicTo(left + width * 0.66, top + height * 0.76, left + width * 0.71, top + height, left + width * 0.79, top + height * 0.98)
            p.cubicTo(left + width * 0.88, top + height * 0.94, left + width * 0.91, top + height * 0.70, left + width * 0.94, top + height * 0.48)
            p.cubicTo(left + width * 0.98, top + height * 0.23, left + width * 0.92, top + height * 0.03, left + width * 0.81, top + height * 0.10)
            p.cubicTo(left + width * 0.67, top + height * 0.02, left + width * 0.33, top + height * 0.02, left + width * 0.19, top + height * 0.10)
        p.closeSubpath()
        return p

    def _paint_xbox(self, painter: QPainter, left: float, top: float, body_w: float, body_h: float) -> None:
        painter.setPen(QPen(QColor("#30445F"), 2))
        painter.setBrush(QColor("#0E1826"))
        painter.drawPath(self._shell_path(left, top, body_w, body_h))
        self._draw_trigger_bars(painter, left, top, body_w, "LT", "RT")

        stick_r = min(38.0, body_w * 0.055)
        self._draw_stick(
            painter, QPointF(left + body_w * 0.30, top + body_h * 0.37), stick_r,
            self.sample.get("lx", 0.0), self.sample.get("ly", 0.0), "LEFT",
        )
        self._draw_stick(
            painter, QPointF(left + body_w * 0.61, top + body_h * 0.66), stick_r,
            self.sample.get("rx", 0.0), self.sample.get("ry", 0.0), "RIGHT",
        )
        self._draw_dpad(painter, QPointF(left + body_w * 0.35, top + body_h * 0.66), 54)

        mask = int(self.sample.get("buttons", 0)) if "buttons" in self.sample else 0
        mapped = self.mapping_family == "xbox"
        face = QPointF(left + body_w * 0.74, top + body_h * 0.38)
        gap = 26.0
        self._draw_button(painter, QPointF(face.x(), face.y() + gap), 13, "A", mapped and bool(mask & 0x1000))
        self._draw_button(painter, QPointF(face.x() + gap, face.y()), 13, "B", mapped and bool(mask & 0x2000))
        self._draw_button(painter, QPointF(face.x() - gap, face.y()), 13, "X", mapped and bool(mask & 0x4000))
        self._draw_button(painter, QPointF(face.x(), face.y() - gap), 13, "Y", mapped and bool(mask & 0x8000))
        self._draw_button(painter, QPointF(left + body_w * 0.47, top + body_h * 0.39), 10, "≡", mapped and bool(mask & 0x0010))
        self._draw_button(painter, QPointF(left + body_w * 0.53, top + body_h * 0.39), 10, "◫", mapped and bool(mask & 0x0020))

    def _paint_dualsense(self, painter: QPainter, left: float, top: float, body_w: float, body_h: float) -> None:
        painter.setPen(QPen(QColor("#B9C5D4"), 2))
        painter.setBrush(QColor("#D8DEE8"))
        painter.drawPath(self._shell_path(left, top, body_w, body_h, dualsense=True))

        # Dark center section and touchpad make this unmistakably DualSense-like.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#111820"))
        center_panel = QRectF(left + body_w * 0.32, top + body_h * 0.14, body_w * 0.36, body_h * 0.48)
        painter.drawRoundedRect(center_panel, 24, 24)
        painter.setBrush(QColor("#202B38"))
        touchpad = QRectF(left + body_w * 0.385, top + body_h * 0.16, body_w * 0.23, body_h * 0.18)
        painter.drawRoundedRect(touchpad, 8, 8)
        self._draw_trigger_bars(painter, left, top, body_w, "L2", "R2")

        stick_r = min(37.0, body_w * 0.054)
        self._draw_stick(
            painter, QPointF(left + body_w * 0.42, top + body_h * 0.66), stick_r,
            self.sample.get("lx", 0.0), self.sample.get("ly", 0.0), "LEFT",
        )
        self._draw_stick(
            painter, QPointF(left + body_w * 0.58, top + body_h * 0.66), stick_r,
            self.sample.get("rx", 0.0), self.sample.get("ry", 0.0), "RIGHT",
        )
        self._draw_dpad(painter, QPointF(left + body_w * 0.25, top + body_h * 0.42), 54)

        face = QPointF(left + body_w * 0.75, top + body_h * 0.42)
        gap = 26.0
        # Current raw HID adapter does not claim a safe named DualSense button mask,
        # so these are layout labels unless a future adapter supplies that mapping.
        self._draw_button(painter, QPointF(face.x(), face.y() + gap), 13, "×", False)
        self._draw_button(painter, QPointF(face.x() + gap, face.y()), 13, "○", False)
        self._draw_button(painter, QPointF(face.x() - gap, face.y()), 13, "□", False)
        self._draw_button(painter, QPointF(face.x(), face.y() - gap), 13, "△", False)

    def _paint_generic(self, painter: QPainter, left: float, top: float, body_w: float, body_h: float) -> None:
        painter.setPen(QPen(QColor("#30445F"), 2))
        painter.setBrush(QColor("#0E1826"))
        painter.drawRoundedRect(QRectF(left + body_w * 0.08, top + body_h * 0.12, body_w * 0.84, body_h * 0.70), 50, 50)
        self._draw_trigger_bars(painter, left, top, body_w, "L", "R")
        stick_r = min(37.0, body_w * 0.054)
        self._draw_stick(
            painter, QPointF(left + body_w * 0.38, top + body_h * 0.55), stick_r,
            self.sample.get("lx", 0.0), self.sample.get("ly", 0.0), "LEFT",
        )
        self._draw_stick(
            painter, QPointF(left + body_w * 0.62, top + body_h * 0.55), stick_r,
            self.sample.get("rx", 0.0), self.sample.get("ry", 0.0), "RIGHT",
        )
        self._draw_dpad(painter, QPointF(left + body_w * 0.23, top + body_h * 0.43), 50)
        face = QPointF(left + body_w * 0.77, top + body_h * 0.43)
        for index, (ox, oy) in enumerate(((0,24),(24,0),(-24,0),(0,-24))):
            self._draw_button(painter, QPointF(face.x()+ox,face.y()+oy), 12, str(index+1), False)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = float(self.width())
        h = float(self.height())
        body_w = min(w * 0.78, 720.0)
        body_h = min(h * 0.67, 250.0)
        left = (w - body_w) / 2.0
        top = max(62.0, (h - body_h) * 0.43)

        painter.setPen(QColor("#70D6FF"))
        label = {"xbox":"XBOX LAYOUT","dualsense":"DUALSENSE LAYOUT","generic":"GENERIC LAYOUT"}[self.skin]
        painter.drawText(QRectF(0, 14, w, 22), Qt.AlignmentFlag.AlignHCenter, label)

        if self.skin == "xbox":
            self._paint_xbox(painter,left,top,body_w,body_h)
        elif self.skin == "dualsense":
            self._paint_dualsense(painter,left,top,body_w,body_h)
        else:
            self._paint_generic(painter,left,top,body_w,body_h)

        painter.setPen(QColor("#7F93AC"))
        if self.mapping_family == "xbox":
            mapping = "Xbox/XInput mapping active"
        elif self.mapping_family == "dualsense":
            mapping = "DualSense detected • named button mapping unavailable from current backend"
        else:
            mapping = "Generic/source-specific button mapping"
        painter.drawText(
            QRectF(left, top + body_h + 28, body_w, 22),
            Qt.AlignmentFlag.AlignHCenter, mapping,
        )


class HeatMapWidget(QWidget):
    def __init__(self, title: str, parent: QWidget | None = None, *, help_text: str = "") -> None:
        super().__init__(parent)
        self.title = title
        self.points: list[tuple[float, float, float]] = []
        self.setMinimumHeight(290)
        if help_text:
            self.setToolTip(help_text)

    def set_points(self, points: Sequence[tuple[float, float, float]]) -> None:
        self.points = [(float(x), float(y), float(v)) for x, y, v in points]
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor("#23334A"), 1))
        painter.setBrush(QColor("#0C141F"))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 16, 16)
        painter.setPen(QColor("#A8B5C9"))
        font = painter.font(); font.setBold(True); painter.setFont(font)
        painter.drawText(QRectF(16, 12, self.width() - 32, 24), Qt.AlignmentFlag.AlignLeft, self.title)
        area = QRectF(62, 48, max(10, self.width() - 86), max(10, self.height() - 88))
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
        painter.drawText(QRectF(area.left(), area.bottom() + 8, area.width(), 20), Qt.AlignmentFlag.AlignHCenter, "Stimulus frequency (log scale)")
