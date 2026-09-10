from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor

from qgis.core import (
    QgsGeometry,
    QgsPointXY,
    Qgis,
)
from qgis.gui import (
    QgsMapTool,
    QgsRubberBand,
)


class PolygonAOITool(QgsMapTool):
    polygonFinished = pyqtSignal(object, object)
    captureCanceled = pyqtSignal()

    def __init__(self, canvas):
        super().__init__(canvas)
        self.canvas = canvas
        self.points = []

        self.rubber_band = QgsRubberBand(
            canvas,
            Qgis.GeometryType.Polygon,
        )
        self.rubber_band.setStrokeColor(
            QColor(0, 120, 215, 220)
        )
        self.rubber_band.setFillColor(
            QColor(0, 120, 215, 35)
        )
        self.rubber_band.setWidth(2)

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            point = self.toMapCoordinates(
                event.pos()
            )
            self.points.append(
                QgsPointXY(point)
            )
            self._refresh()

        elif event.button() == Qt.MouseButton.RightButton:
            self.finish_capture()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancel_capture()
        else:
            super().keyPressEvent(event)

    def _refresh(self):
        self.rubber_band.reset(
            Qgis.GeometryType.Polygon
        )

        if not self.points:
            return

        preview_points = list(self.points)

        if len(preview_points) >= 3:
            preview_points.append(
                preview_points[0]
            )

        geometry = QgsGeometry.fromPolygonXY(
            [preview_points]
        )

        self.rubber_band.setToGeometry(
            geometry,
            None,
        )

    def finish_capture(self):
        if len(self.points) < 3:
            return

        polygon_points = list(self.points)
        polygon_points.append(
            polygon_points[0]
        )

        geometry = QgsGeometry.fromPolygonXY(
            [polygon_points]
        )
        crs = self.canvas.mapSettings().destinationCrs()

        self.polygonFinished.emit(
            geometry,
            crs,
        )

        self.reset()

    def cancel_capture(self):
        self.captureCanceled.emit()
        self.reset()

    def reset(self):
        self.points = []
        self.rubber_band.reset(
            Qgis.GeometryType.Polygon
        )
