# -*- coding: utf-8 -*-
"""Export enabled QGIS vector layers to a KMZ file."""
import os
import math
import json
import zipfile
import tempfile
import xml.etree.ElementTree as ET

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QAction, QDialog, QVBoxLayout, QHBoxLayout, QTableWidget,
    QTableWidgetItem, QPushButton, QFileDialog, QMessageBox,
    QHeaderView, QComboBox, QLabel, QCheckBox, QAbstractItemView,
    QProgressDialog, QApplication, QProgressBar
)
from qgis.PyQt.QtGui import QColor, QIcon
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
    """Serialize QgsPoint or QgsPointXY; 2D points do not expose z()."""
    try:
        z = point.z() if hasattr(point, "z") else float("nan")
        if z is None or not math.isfinite(float(z)):
            z = 0.0
    except (AttributeError, TypeError, ValueError):
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


def geometry_to_kml(geometry, transform):
    """Convert a QGIS geometry to the matching KML geometry without changing its type."""
    g = QgsGeometry(geometry)

    # Only curved geometries need segmentizing. Avoid running this on every
    # geometry because behavior differs across QGIS versions and WKB types.
    try:
        if QgsWkbTypes.isCurvedType(g.wkbType()):
            g.convertToStraightSegment()
    except (AttributeError, TypeError):
        pass

    result = g.transform(transform)
    # QgsGeometry.transform returns a result code; non-zero means failure.
    if result != 0:
        raise ValueError("坐标转换失败（QGIS 错误代码：{}）".format(result))

    geometry_type = g.type()
    multi = g.isMultipart()

    if geometry_type == QgsWkbTypes.PointGeometry:
        points = g.asMultiPoint() if multi else [g.asPoint()]
        points = [p for p in points if p is not None]
        if not points:
            return None
        if multi:
            node = ET.Element(qn("MultiGeometry"))
            for point in points:
                part = ET.SubElement(node, qn("Point"))
                add_text(part, "coordinates", coordinate(point))
            return node
        node = ET.Element(qn("Point"))
        add_text(node, "coordinates", coordinate(points[0]))
        return node

    if geometry_type == QgsWkbTypes.LineGeometry:
        lines = g.asMultiPolyline() if multi else [g.asPolyline()]
        lines = [line for line in lines if line and len(line) >= 2]
        if not lines:
            return None
        if multi:
            node = ET.Element(qn("MultiGeometry"))
            for line_points in lines:
                part = ET.SubElement(node, qn("LineString"))
                add_text(part, "tessellate", "1")
                add_text(part, "coordinates", coords(line_points))
            return node
        node = ET.Element(qn("LineString"))
        add_text(node, "tessellate", "1")
        add_text(node, "coordinates", coords(lines[0]))
        return node

    if geometry_type == QgsWkbTypes.PolygonGeometry:
        polygons = g.asMultiPolygon() if multi else [g.asPolygon()]
        polygons = [rings for rings in polygons if rings and rings[0] and len(rings[0]) >= 4]
        if not polygons:
            return None
        if multi:
            node = ET.Element(qn("MultiGeometry"))
            for rings in polygons:
                append_polygon(node, rings)
            return node
        return polygon_node(polygons[0])

    return None


class ExportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("KMZ导出工具")
        self.resize(980, 590)
        self.setMinimumWidth(760)
        self.setStyleSheet("""
            QDialog { background: #f5f7fb; color: #172033; font-family: "Microsoft YaHei UI", "Segoe UI"; font-size: 10pt; }
            QPushButton { background: white; border: 1px solid #cbd5e1; border-radius: 6px; padding: 7px 13px; color: #172033; }
            QPushButton:hover { background: #edf5ff; border-color: #6aa7ff; }
            QPushButton#primaryButton { background: #1677ff; color: white; border: 1px solid #1677ff; font-weight: 600; }
            QTableWidget { background: white; alternate-background-color: #f7faff; border: 1px solid #cbd5e1; gridline-color: #dbe3ef; selection-background-color: #dbeafe; selection-color: #172033; }
            QHeaderView::section { background: #eef3fa; font-weight: 600; border: 0; border-right: 1px solid #d3dce9; border-bottom: 1px solid #cbd5e1; padding: 8px 5px; }
            QComboBox { background: white; border: 1px solid #cbd5e1; border-radius: 4px; padding: 4px 6px; min-height: 22px; }
            QLabel#outputPath { background: white; border: 1px solid #cbd5e1; border-radius: 5px; padding: 8px; }
        """)
        self.layers = [l for l in QgsProject.instance().mapLayers().values()
                       if isinstance(l, QgsVectorLayer) and l.isValid()]
        self.output_path = ""
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
        top.addStretch(1)
        self.export_template_btn = QPushButton("导出样式模板")
        self.import_template_btn = QPushButton("导入样式模板")
        top.addWidget(self.export_template_btn)
        top.addWidget(self.import_template_btn)
        layout.addLayout(top)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["启用", "图层", "几何类型", "字段分组", "配置样式", "图标配置", "Description"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        for c in range(3, 7):
            header.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        header.setStretchLastSection(False)
        layout.addWidget(self.table)

        bottom = QHBoxLayout()
        self.output = QLabel("请选择 KMZ 保存位置")
        self.output.setObjectName("outputPath")
        self.output.setMinimumWidth(180)
        self.output.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.output.setToolTip("导出文件的完整保存路径")
        self.browse_btn = QPushButton("浏览…")
        self.export_btn = QPushButton("↓  导出 KMZ")
        self.export_btn.setObjectName("primaryButton")
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
        self.export_template_btn.clicked.connect(self.export_template)
        self.import_template_btn.clicked.connect(self.import_template)

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
            name.setToolTip(layer.name())
            self.table.setItem(row, 1, name)

            geom_name = QTableWidgetItem(QgsWkbTypes.displayString(layer.wkbType()))
            geom_name.setFlags(geom_name.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 2, geom_name)

            group = QComboBox()
            group.addItem("不分组", "")
            for field in layer.fields():
                group.addItem(field.name(), field.name())
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
            self.controls.append({
                "layer": layer, "group": group, "style": style,
                "icon": icon, "description": desc
            })
        self.table.resizeColumnsToContents()
        # Keep the layer-name column content-sized but prevent very long names
        # from making the whole dialog unwieldy.
        self.table.setColumnWidth(1, min(self.table.columnWidth(1), 260))

    def set_checks(self, checked):
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(Qt.Checked if checked else Qt.Unchecked)

    def invert_checks(self):
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)

    def browse(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "保存 KMZ", self.output_path or "export.kmz", "KMZ 文件 (*.kmz)")
        if path:
            if not path.lower().endswith(".kmz"):
                path += ".kmz"
            self.output_path = path
            self.output.setText(path)
            self.output.setToolTip(path)

    def export_template(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出样式模板", "Export2kmz-template.json", "JSON 文件 (*.json)")
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        config = {
            "format": "Export2kmzStyleTemplate",
            "version": 1,
            "show_attributes": self.attrs.isChecked(),
            "output_path": self.output_path,
            "layers": []
        }
        for row, item in enumerate(self.controls):
            config["layers"].append({
                "name": item["layer"].name(),
                "enabled": self.table.item(row, 0).checkState() == Qt.Checked,
                "group_field": item["group"].currentData() or "",
                "style": item["style"].currentText(),
                "icon": item["icon"].currentText(),
                "description_field": item["description"].currentData() or ""
            })
        try:
            with open(path, "w", encoding="utf-8") as stream:
                json.dump(config, stream, ensure_ascii=False, indent=2)
            QMessageBox.information(self, "样式模板", "模板已导出：\n" + path)
        except Exception as exc:
            QMessageBox.critical(self, "导出模板失败", str(exc))

    def import_template(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入样式模板", "", "JSON 文件 (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as stream:
                config = json.load(stream)
            if config.get("format") != "Export2kmzStyleTemplate":
                raise ValueError("这不是 Export2kmz 样式模板 JSON 文件。")
            saved_layers = {entry.get("name"): entry for entry in config.get("layers", [])}
            for row, item in enumerate(self.controls):
                saved = saved_layers.get(item["layer"].name())
                if not saved:
                    continue
                self.table.item(row, 0).setCheckState(
                    Qt.Checked if saved.get("enabled", True) else Qt.Unchecked)
                self._set_combo_data(item["group"], saved.get("group_field", ""))
                self._set_combo_text(item["style"], saved.get("style", "默认样式"))
                self._set_combo_text(item["icon"], saved.get("icon", "默认"))
                self._set_combo_data(item["description"], saved.get("description_field", ""))
            self.attrs.setChecked(bool(config.get("show_attributes", True)))
            self.output_path = config.get("output_path", "") or ""
            self.output.setText(self.output_path if self.output_path else "请选择 KMZ 保存位置")
            self.output.setToolTip(self.output_path)
            QMessageBox.information(self, "样式模板", "模板配置已导入。")
        except Exception as exc:
            QMessageBox.critical(self, "导入模板失败", str(exc))

    @staticmethod
    def _set_combo_data(combo, value):
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    @staticmethod
    def _set_combo_text(combo, value):
        index = combo.findText(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def do_export(self):
        path = self.output_path
        if not path:
            self.browse()
            path = self.output_path
        if not path:
            return
        selected = []
        for row, item in enumerate(self.controls):
            if self.table.item(row, 0).checkState() == Qt.Checked:
                selected.append(item)
        if not selected:
            QMessageBox.warning(self, "KMZ导出工具", "请至少选择一个图层。")
            return

        progress = QProgressDialog("正在读取图层信息…", "取消导出", 0, 100, self)
        progress.setWindowTitle("正在导出 KMZ")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setMinimumWidth(460)
        progress.setStyleSheet("""
            QProgressDialog { background: white; font-family: "Microsoft YaHei UI", "Segoe UI"; font-size: 10pt; }
            QLabel { color: #172033; padding: 5px; }
            QProgressBar { background: #e8edf5; border: 0; border-radius: 5px; height: 14px; text-align: center; }
            QProgressBar::chunk { background: #1677ff; border-radius: 5px; }
            QPushButton { background: white; border: 1px solid #cbd5e1; border-radius: 5px; padding: 6px 14px; }
        """)
        progress.show()
        QApplication.processEvents()
        try:
            count, geometry_stats = export_kmz(path, selected, self.attrs.isChecked(), progress)
            progress.setValue(100)
            progress.setLabelText("✓  KMZ 文件已生成。")
            QApplication.processEvents()
            progress.close()
            absolute_path = os.path.abspath(path)
            success = QMessageBox(self)
            success.setWindowTitle("导出成功")
            success.setIcon(QMessageBox.Information)
            success.setText("<span style='color:#15803d;font-size:17pt;font-weight:700'>✓  导出成功！</span>")
            success.setInformativeText(
                "<div style='font-size:10pt;color:#172033'>文件已成功导出到：</div>"
                "<div style='background:#dcfce7;border:1px solid #bbf7d0;border-radius:5px;"
                "padding:9px;color:#166534;margin-top:6px'>{}</div>"
                "<hr><b>导出统计</b><br>图层数量：{}<br>成功导出要素总数：{:,}"
                "<br>点要素：{:,}<br>线要素：{:,}<br>面要素：{:,}".format(
                    absolute_path, len(selected), count,
                    geometry_stats["point"], geometry_stats["line"], geometry_stats["polygon"]
                )
            )
            copy_button = success.addButton("复制文件路径", QMessageBox.ActionRole)
            success.addButton("确定", QMessageBox.AcceptRole)
            success.setStyleSheet("""
                QMessageBox { background: white; min-width: 420px; font-family: "Microsoft YaHei UI", "Segoe UI"; }
                QLabel { color: #172033; min-width: 360px; }
                QPushButton { background: #1677ff; color: white; border: 0; border-radius: 5px; padding: 7px 16px; }
                QPushButton:hover { background: #0958d9; }
            """)
            success.exec_()
            if success.clickedButton() == copy_button:
                QApplication.clipboard().setText(absolute_path)
            self.accept()
        except InterruptedError:
            progress.close()
            QMessageBox.information(self, "KMZ导出工具", "导出已取消。")
        except Exception as exc:
            progress.close()
            QMessageBox.critical(self, "导出失败", str(exc))

def export_kmz(output_path, selected, include_attributes=True, progress=None):
    doc = ET.Element(qn("kml"))
    document = ET.SubElement(doc, qn("Document"))
    add_text(document, "name", os.path.basename(output_path))
    target_crs = QgsCoordinateReferenceSystem("EPSG:4326")
    total_features = sum(item["layer"].featureCount() for item in selected)
    processed = 0
    exported_count = 0
    geometry_stats = {"point": 0, "line": 0, "polygon": 0}
    if progress:
        progress.setValue(0)
        progress.setLabelText("正在导出图层…")
        QApplication.processEvents()

    for item in selected:
        layer = item["layer"]
        layer_folder = ET.SubElement(document, qn("Folder"))
        add_text(layer_folder, "name", layer.name())
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
        group_field = item["group"].currentData() or ""
        desc_field = item["description"].currentData() or ""
        group_folders = {}
        for feature in layer.getFeatures():
            if progress and progress.wasCanceled():
                raise InterruptedError("用户取消了导出。")
            geom = feature.geometry()
            if geom is None or geom.isEmpty():
                continue
            parent_folder = layer_folder
            if group_field:
                raw_group = feature[group_field]
                group_name = "(空值)" if raw_group is None or str(raw_group).strip() == "" else str(raw_group)
                if group_name not in group_folders:
                    subfolder = ET.SubElement(layer_folder, qn("Folder"))
                    add_text(subfolder, "name", group_name)
                    group_folders[group_name] = subfolder
                parent_folder = group_folders[group_name]

            placemark = ET.SubElement(parent_folder, qn("Placemark"))
            name_idx = layer.fields().indexFromName("name")
            if name_idx < 0:
                name_idx = layer.fields().indexFromName("Name")
            name_value = feature[name_idx] if name_idx >= 0 else feature.id()
            add_text(placemark, "name", name_value)
            add_text(placemark, "styleUrl", "#" + style_id)
            if desc_field:
                value = feature[desc_field]
                if value is not None:
                    add_text(placemark, "description", value)
            elif include_attributes:
                extended = ET.SubElement(placemark, qn("ExtendedData"))
                for field in layer.fields():
                    data = ET.SubElement(extended, qn("Data"), {"name": field.name()})
                    value = feature[field.name()]
                    add_text(data, "value", "" if value is None else value)
            geometry_node = geometry_to_kml(geom, transform)
            if geometry_node is not None:
                placemark.append(geometry_node)
                exported_count += 1
                geom_type = QgsWkbTypes.geometryType(layer.wkbType())
                if geom_type == QgsWkbTypes.PointGeometry:
                    geometry_stats["point"] += 1
                elif geom_type == QgsWkbTypes.LineGeometry:
                    geometry_stats["line"] += 1
                elif geom_type == QgsWkbTypes.PolygonGeometry:
                    geometry_stats["polygon"] += 1

            processed += 1
            if progress:
                value = int(processed * 95 / max(total_features, 1))
                progress.setValue(value)
                progress.setLabelText(
                    "正在导出：{}  |  已处理 {} / {} 个要素".format(
                        layer.name(), processed, total_features
                    )
                )
                QApplication.processEvents()

    if progress:
        progress.setValue(97)
        progress.setLabelText("正在生成 KMZ 文件…")
        QApplication.processEvents()

    xml = ET.tostring(doc, encoding="utf-8", xml_declaration=True)
    output_path = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        kml_path = os.path.join(tmp, "doc.kml")
        with open(kml_path, "wb") as stream:
            stream.write(xml)
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(kml_path, "doc.kml")
    return exported_count, geometry_stats


class Export2KmzPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), "icon.svg")
        self.action = QAction(QIcon(icon_path), "Export2KMZ", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu("&Export2KMZ", self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.action:
            self.iface.removePluginMenu("&Export2KMZ", self.action)
            self.iface.removeToolBarIcon(self.action)

    def run(self):
        ExportDialog(self.iface.mainWindow()).exec_()
