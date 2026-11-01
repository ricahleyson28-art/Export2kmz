# -*- coding: utf-8 -*-
"""Export enabled QGIS vector layers to a KMZ file."""
import os
import math
import zipfile
import tempfile
import xml.etree.ElementTree as ET

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QAction, QDialog, QVBoxLayout, QHBoxLayout, QTableWidget,
    QTableWidgetItem, QPushButton, QFileDialog, QMessageBox,
    QHeaderView, QComboBox, QLabel, QCheckBox, QAbstractItemView
)
from qgis.PyQt.QtGui import QColor
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsWkbTypes, QgsCoordinateReferenceSystem,
    QgsCoordinateTransform, QgsGeometry
)

KML_NS = "http://www.opengis.net/kml/2.2"
ET.register_namespace("", KML_NS)

def qn(tag):
    return "{%s}%s" % (KML_NS, tag)

def add_text(parent, tag, value):
    node = ET.SubElement(parent, qn(tag))
    node.text = "" if value is None else str(value)
    return node

def kml_color(color, alpha=255):
    return "%02x%02x%02x%02x" % (alpha, color.blue(), color.green(), color.red())

def coordinate(point):
    z = point.z()
    if math.isnan(z):
        z = 0.0
    return "{:.8f},{:.8f},{:.3f}".format(point.x(), point.y(), z)

def coords(points):
    return " ".join(coordinate(p) for p in points)

def append_polygon(parent, rings):
    poly = ET.SubElement(parent, qn("Polygon"))
    if not rings:
        return poly
    outer = ET.SubElement(poly, qn("outerBoundaryIs"))
    lr = ET.SubElement(outer, qn("LinearRing"))
    add_text(lr, "coordinates", coords(rings[0]))
    for ring_points in rings[1:]:
        inner = ET.SubElement(poly, qn("innerBoundaryIs"))
        lr = ET.SubElement(inner, qn("LinearRing"))
        add_text(lr, "coordinates", coords(ring_points))
    return poly

def geometry_to_kml(geometry, transform):
    g = QgsGeometry(geometry)
    try:
        g.convertToStraightSegment()
    except Exception:
        pass
    g.transform(transform)
    flat = QgsWkbTypes.flatType(g.wkbType())
    multi = QgsWkbTypes.isMultiType(g.wkbType())
    if flat == QgsWkbTypes.Point:
        points = g.asMultiPoint() if multi else [g.asPoint()]
        if multi:
            node = ET.Element(qn("MultiGeometry"))
            for p in points:
                part = ET.SubElement(node, qn("Point"))
                add_text(part, "coordinates", coordinate(p))
            return node
        node = ET.Element(qn("Point"))
        add_text(node, "coordinates", coordinate(points[0]))
        return node
    if flat == QgsWkbTypes.LineString:
        lines = g.asMultiPolyline() if multi else [g.asPolyline()]
        if multi:
            node = ET.Element(qn("MultiGeometry"))
            for line in lines:
                part = ET.SubElement(node, qn("LineString"))
                add_text(part, "tessellate", "1")
                add_text(part, "coordinates", coords(line))
            return node
        node = ET.Element(qn("LineString"))
        add_text(node, "tessellate", "1")
        add_text(node, "coordinates", coords(lines[0]))
        return node
    if flat == QgsWkbTypes.Polygon:
        polygons = g.asMultiPolygon() if multi else [g.asPolygon()]
        if multi:
            node = ET.Element(qn("MultiGeometry"))
            for rings in polygons:
                append_polygon(node, rings)
            return node
        return append_polygon(ET.Element(qn("GeometryCollection")), polygons[0]) if False else polygon_node(polygons[0])
    return None

def polygon_node(rings):
    node = ET.Element(qn("Polygon"))
    if rings:
        outer = ET.SubElement(node, qn("outerBoundaryIs"))
        lr = ET.SubElement(outer, qn("LinearRing"))
        add_text(lr, "coordinates", coords(rings[0]))
        for points in rings[1:]:
            inner = ET.SubElement(node, qn("innerBoundaryIs"))
            lr = ET.SubElement(inner, qn("LinearRing"))
            add_text(lr, "coordinates", coords(points))
    return node

class ExportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("KMZ导出工具")
        self.resize(1120, 590)
        self.layers = [l for l in QgsProject.instance().mapLayers().values()
                       if isinstance(l, QgsVectorLayer) and l.isValid()]
        self.build_ui()
        self.populate()

    def build_ui(self):
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.all_btn = QPushButton("☑ 全选")
        self.invert_btn = QPushButton("✕ 反选")
        self.attrs = QCheckBox("显示属性信息")
        self.attrs.setChecked(True)
        top.addWidget(self.all_btn)
        top.addWidget(self.invert_btn)
        top.addStretch(1)
        top.addWidget(self.attrs)
        layout.addLayout(top)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["启用", "图层", "几何类型", "字段分组", "配置样式", "图标配置", "Description"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for c in range(2, 7):
            header.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        layout.addWidget(self.table)
        bottom = QHBoxLayout()
        self.output = QLabel("输出文件：未选择")
        self.browse_btn = QPushButton("浏览…")
        self.export_btn = QPushButton("导出 KMZ")
        self.cancel_btn = QPushButton("取消")
        bottom.addWidget(self.output, 1)
        bottom.addWidget(self.browse_btn)
        bottom.addWidget(self.export_btn)
        bottom.addWidget(self.cancel_btn)
        layout.addLayout(bottom)
        self.all_btn.clicked.connect(lambda: self.set_checks(True))
        self.invert_btn.clicked.connect(self.invert_checks)
        self.browse_btn.clicked.connect(self.browse)
        self.export_btn.clicked.connect(self.do_export)
        self.cancel_btn.clicked.connect(self.reject)

    def populate(self):
        self.controls = []
        self.table.setRowCount(len(self.layers))
        for row, layer in enumerate(self.layers):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            check.setCheckState(Qt.Checked)
            self.table.setItem(row, 0, check)
            name = QTableWidgetItem(layer.name())
            name.setFlags(name.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 1, name)
            self.table.setItem(row, 2, QTableWidgetItem(QgsWkbTypes.displayString(layer.wkbType())))
            group = QComboBox()
            group.addItems(["不分组"])
            self.table.setCellWidget(row, 3, group)
            style = QComboBox()
            style.addItems(["默认样式", "基础样式"])
            self.table.setCellWidget(row, 4, style)
            icon = QComboBox()
            icon.addItems(["默认", "空心圆环", "圆点", "方形"])
            icon.setEnabled(QgsWkbTypes.geometryType(layer.wkbType()) == QgsWkbTypes.PointGeometry)
            self.table.setCellWidget(row, 5, icon)
            desc = QComboBox()
            desc.addItem("(不添加说明)", "")
            for field in layer.fields():
                desc.addItem(field.name(), field.name())
            self.table.setCellWidget(row, 6, desc)
            self.controls.append({"layer": layer, "description": desc})

    def set_checks(self, checked):
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(Qt.Checked if checked else Qt.Unchecked)

    def invert_checks(self):
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)

    def browse(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存 KMZ", "export.kmz", "KMZ 文件 (*.kmz)")
        if path:
            if not path.lower().endswith(".kmz"):
                path += ".kmz"
            self.output.setText(path)

    def do_export(self):
        path = self.output.text()
        if path == "输出文件：未选择":
            self.browse()
            path = self.output.text()
        if path == "输出文件：未选择":
            return
        selected = [c for row, c in enumerate(self.controls)
                    if self.table.item(row, 0).checkState() == Qt.Checked]
        if not selected:
            QMessageBox.warning(self, "KMZ导出工具", "请至少选择一个图层。")
            return
        try:
            export_kmz(path, selected, self.attrs.isChecked())
            QMessageBox.information(self, "KMZ导出工具", "导出成功：\n" + path)
            self.accept()
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", str(exc))

def export_kmz(output_path, selected, include_attributes=True):
    doc = ET.Element(qn("kml"))
    document = ET.SubElement(doc, qn("Document"))
    add_text(document, "name", os.path.basename(output_path))
    target_crs = QgsCoordinateReferenceSystem("EPSG:4326")
    for item in selected:
        layer = item["layer"]
        folder = ET.SubElement(document, qn("Folder"))
        add_text(folder, "name", layer.name())
        renderer = layer.renderer()
        symbol = renderer.symbol() if renderer and hasattr(renderer, "symbol") else None
        color = symbol.color() if symbol else QColor("#3388ff")
        style_id = "style_" + str(abs(hash(layer.id())))
        style = ET.SubElement(document, qn("Style"), {"id": style_id})
        geom_type = QgsWkbTypes.geometryType(layer.wkbType())
        if geom_type == QgsWkbTypes.PointGeometry:
            icon_style = ET.SubElement(style, qn("IconStyle"))
            add_text(icon_style, "color", kml_color(color))
            add_text(icon_style, "scale", "0.8")
            icon = ET.SubElement(icon_style, qn("Icon"))
            add_text(icon, "href", "http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png")
        elif geom_type == QgsWkbTypes.LineGeometry:
            line = ET.SubElement(style, qn("LineStyle"))
            add_text(line, "color", kml_color(color))
            add_text(line, "width", "2")
        else:
            poly = ET.SubElement(style, qn("PolyStyle"))
            add_text(poly, "color", kml_color(color, 90))
            add_text(poly, "outline", "1")
            line = ET.SubElement(style, qn("LineStyle"))
            add_text(line, "color", kml_color(color))
            add_text(line, "width", "1.5")
        transform = QgsCoordinateTransform(layer.crs(), target_crs, QgsProject.instance())
        desc_field = item["description"].currentData()
        for feature in layer.getFeatures():
            geom = feature.geometry()
            if geom is None or geom.isEmpty():
                continue
            placemark = ET.SubElement(folder, qn("Placemark"))
            name_idx = layer.fields().indexFromName("name")
            if name_idx < 0:
                name_idx = layer.fields().indexFromName("Name")
            name_value = feature[name_idx] if name_idx >= 0 else feature.id()
            add_text(placemark, "name", name_value)
            add_text(placemark, "styleUrl", "#" + style_id)
            if desc_field:
                add_text(placemark, "description", feature[desc_field])
            elif include_attributes:
                extended = ET.SubElement(placemark, qn("ExtendedData"))
                for field in layer.fields():
                    data = ET.SubElement(extended, qn("Data"), {"name": field.name()})
                    add_text(data, "value", feature[field.name()])
            geometry_node = geometry_to_kml(geom, transform)
            if geometry_node is not None:
                placemark.append(geometry_node)
    xml = ET.tostring(doc, encoding="utf-8", xml_declaration=True)
    output_path = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        kml_path = os.path.join(tmp, "doc.kml")
        with open(kml_path, "wb") as f:
            f.write(xml)
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(kml_path, "doc.kml")

class Export2KmzPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None

    def initGui(self):
        self.action = QAction("Export2KMZ", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu("&Export2KMZ", self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.action:
            self.iface.removePluginMenu("&Export2KMZ", self.action)
            self.iface.removeToolBarIcon(self.action)

    def run(self):
        ExportDialog(self.iface.mainWindow()).exec_()
