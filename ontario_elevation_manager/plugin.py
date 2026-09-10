from contextlib import suppress
import os
import shutil
import zipfile
from datetime import datetime

from osgeo import gdal

from qgis.PyQt.QtCore import (
    Qt,
    QSettings,
    QUrl,
)
from qgis.PyQt.QtGui import (
    QIcon,
    QColor,
    QDesktopServices,
)
from qgis.PyQt.QtWidgets import (
    QAction,
    QApplication,
    QCheckBox,
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from qgis.core import (
    QgsApplication,
    QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureRequest,
    QgsField,
    QgsFillSymbol,
    QgsGeometry,
    QgsPalLayerSettings,
    QgsPointXY,
    QgsProject,
    QgsProviderRegistry,
    QgsRasterLayer,
    QgsRendererCategory,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
    Qgis,
)

from qgis.PyQt.QtCore import QVariant
from qgis.gui import QgsRubberBand

from .maptools import PolygonAOITool
from .services import (
    query_tiles_by_envelope,
    ensure_local_tile_index,
    resolve_package_jobs,
    get_package_metadata,
)
from .tasks import (
    OntarioDTMDownloadTask,
    build_vrt,
    OUTPUT_NODATA,
)
from .exporter import (
    TerrainExportTask,
    export_is_current,
    inspect_raster,
    load_export_manifest,
    manifest_path_for,
)
from .widgets import CollapsibleSection, HelpButton, ResponsiveRow
from .datasets import RASTER_DATASETS


PLUGIN_NAME = "Ontario Elevation Manager"
TILE_LAYER_NAME = "Ontario DTM - Available Tiles"
TERRAIN_LAYER_NAME = "Ontario DTM - Project Terrain"
GROUP_NAME = "Ontario Elevation"
PROJECT_PROPERTY_PREFIX = "OntarioDTMManager"

PLUGIN_ICON_PATH = os.path.join(
    os.path.dirname(__file__),
    "icon.png",
)

BUFFER_UNIT_FACTORS = {
    "m": 1.0,
    "km": 1000.0,
    "ft": 0.3048,
    "mi": 1609.344,
}


class OntarioDTMDock(QDockWidget):
    """Responsive V1 dock. Backend attribute names remain compatible."""

    def __init__(self, plugin):
        super().__init__(
            PLUGIN_NAME,
            plugin.iface.mainWindow(),
        )

        self.plugin = plugin
        self.setObjectName("OntarioDTMManagerDock")
        self.setWindowIcon(QIcon(PLUGIN_ICON_PATH))
        self.setMinimumWidth(270)

        body = QWidget()
        body.setMinimumWidth(0)
        body.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        layout = QVBoxLayout(body)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        def configure_form(form):
            form.setFieldGrowthPolicy(
                QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
            )
            form.setRowWrapPolicy(
                QFormLayout.RowWrapPolicy.WrapLongRows
            )
            form.setHorizontalSpacing(6)
            form.setVerticalSpacing(5)

        def shrink_field(widget):
            widget.setMinimumWidth(0)
            policy = widget.sizePolicy()
            policy.setHorizontalPolicy(
                QSizePolicy.Policy.Expanding
            )
            widget.setSizePolicy(policy)
            return widget

        def label_help(text, tip):
            wrapper = QWidget()
            row = QHBoxLayout(wrapper)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(3)
            label = QLabel(text)
            label.setWordWrap(True)
            row.addWidget(label)
            row.addWidget(HelpButton(tip))
            row.addStretch(1)
            return wrapper

        def stat_row(parent_layout, label_text, help_text):
            wrapper = QWidget()
            row = QHBoxLayout(wrapper)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)
            label = QLabel(label_text)
            label.setWordWrap(True)
            label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            row.addWidget(label, 1)
            row.addWidget(HelpButton(help_text))
            parent_layout.addWidget(wrapper)
            return label

        # ====================================================
        # 1. ONTARIO ELEVATION DATASET
        # ====================================================
        dataset_group = QGroupBox()
        dataset_group.setFlat(True)
        dataset_layout = QVBoxLayout(dataset_group)

        dataset_form = QFormLayout()
        configure_form(dataset_form)

        self.raster_dataset_combo = shrink_field(
            QComboBox()
        )
        for dataset_key, dataset in RASTER_DATASETS.items():
            self.raster_dataset_combo.addItem(
                dataset["name"],
                dataset_key,
            )
        self.raster_dataset_combo.currentIndexChanged.connect(
            plugin.raster_dataset_changed
        )

        self.dataset_help = HelpButton(
            "Choose the Ontario native elevation source used by the entire "
            "workflow below. LiDAR DTM is the bare-earth terrain model and "
            "LiDAR DSM is the surface model. The selected dataset controls "
            "the 1 km tile index, package download, selective extraction, "
            "cache, VRT lifecycle and export workflow."
        )
        dataset_field = QWidget()
        dataset_field_layout = QHBoxLayout(dataset_field)
        dataset_field_layout.setContentsMargins(0, 0, 0, 0)
        dataset_field_layout.setSpacing(4)
        dataset_field_layout.addWidget(
            self.raster_dataset_combo,
            1,
        )
        dataset_field_layout.addWidget(
            self.dataset_help
        )
        dataset_form.addRow(
            "Dataset:",
            dataset_field,
        )
        dataset_layout.addLayout(dataset_form)

        # Compatibility objects retained for backend methods, but V1 moves
        # their verbose content into contextual help/tooltips.
        self.raster_dataset_description = QLabel()
        self.raster_dataset_description.hide()
        self.dataset_workflow_note = QLabel()
        self.dataset_workflow_note.hide()

        self.refresh_dataset_index_button = QPushButton(
            "Refresh Dataset Index"
        )
        self.refresh_dataset_index_button.clicked.connect(
            plugin.refresh_dataset_index
        )
        self.refresh_dataset_index_button.setToolTip(
            "Force-download the latest official Ontario tile index when the "
            "active dataset uses a locally cached index. Live indexes do not "
            "require manual refresh."
        )

        self.open_dataset_geohub_button = QPushButton(
            "Open GeoHub"
        )
        self.open_dataset_geohub_button.clicked.connect(
            plugin.open_selected_dataset_geohub
        )
        self.open_dataset_geohub_button.setToolTip(
            "Open the official Ontario GeoHub page for the selected dataset."
        )

        dataset_layout.addWidget(
            ResponsiveRow(
                [
                    self.refresh_dataset_index_button,
                    self.open_dataset_geohub_button,
                ]
            )
        )

        index_row = QWidget()
        index_row_layout = QHBoxLayout(index_row)
        index_row_layout.setContentsMargins(0, 0, 0, 0)
        index_row_layout.setSpacing(4)
        self.dataset_index_status = QLabel("Index: --")
        self.dataset_index_status.setWordWrap(True)
        index_row_layout.addWidget(
            self.dataset_index_status,
            1,
        )
        self.dataset_index_help = HelpButton(
            "Shows whether the current dataset is using a live Ontario index "
            "or a locally cached official index. Hover the status text for "
            "full path/source details."
        )
        index_row_layout.addWidget(
            self.dataset_index_help
        )
        dataset_layout.addWidget(index_row)

        self.dataset_section = CollapsibleSection(
            "1. Ontario Elevation Dataset",
            dataset_group,
            settings=plugin.settings,
            settings_key="OntarioDTMManager/ui/v1/section1Expanded",
            default_expanded=True,
        )
        layout.addWidget(self.dataset_section)

        # ====================================================
        # 2. AREA OF INTEREST
        # ====================================================
        aoi_group = QGroupBox()
        aoi_group.setFlat(True)
        aoi_layout = QVBoxLayout(aoi_group)

        self.current_extent_radio = QRadioButton(
            "Current map extent"
        )
        self.current_extent_radio.setChecked(True)

        self.selected_polygon_radio = QRadioButton(
            "Polygon dataset"
        )

        self.draw_polygon_radio = QRadioButton(
            "Draw polygon on map"
        )

        self.aoi_radio_group = QButtonGroup(self)
        self.aoi_radio_group.setExclusive(True)
        self.aoi_radio_group.addButton(self.current_extent_radio)
        self.aoi_radio_group.addButton(self.selected_polygon_radio)
        self.aoi_radio_group.addButton(self.draw_polygon_radio)

        for radio in (
            self.current_extent_radio,
            self.selected_polygon_radio,
            self.draw_polygon_radio,
        ):
            radio.toggled.connect(
                plugin.sync_aoi_preview_visibility
            )
            radio.toggled.connect(
                plugin.update_aoi_source_controls
            )
            radio.toggled.connect(
                plugin.save_project_settings
            )

        aoi_layout.addWidget(
            self.current_extent_radio
        )

        self.aoi_from_project_button = QPushButton(
            "From Project..."
        )
        self.aoi_from_project_button.clicked.connect(
            plugin.choose_aoi_project_layer
        )
        self.aoi_from_project_button.setToolTip(
            "Choose one polygon vector layer already loaded in this QGIS "
            "project. If that layer has selected features, only those are "
            "used; otherwise the entire layer is used."
        )

        self.aoi_browse_button = QPushButton(
            "Browse..."
        )
        self.aoi_browse_button.clicked.connect(
            plugin.browse_aoi_vector
        )
        self.aoi_browse_button.setToolTip(
            "Browse to one external polygon vector dataset without loading it "
            "into the project. Polygon Shapefiles, GeoPackages and other "
            "OGR-readable polygon sources are accepted; non-polygon sources "
            "are rejected."
        )

        self.aoi_polygon_source_row = ResponsiveRow(
            [
                self.selected_polygon_radio,
                self.aoi_from_project_button,
                self.aoi_browse_button,
            ],
            breakpoint=430,
        )
        aoi_layout.addWidget(
            self.aoi_polygon_source_row
        )

        self.aoi_polygon_source_label = QLabel()
        self.aoi_polygon_source_label.setWordWrap(True)
        self.aoi_polygon_source_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.aoi_polygon_source_label.hide()
        aoi_layout.addWidget(
            self.aoi_polygon_source_label
        )

        self.draw_button = QPushButton(
            "Draw / Redraw..."
        )
        self.draw_button.clicked.connect(
            plugin.start_draw_aoi
        )

        self.clear_aoi_button = QPushButton(
            "Clear AOI"
        )
        self.clear_aoi_button.clicked.connect(
            plugin.clear_drawn_aoi
        )

        self.draw_row_widget = ResponsiveRow(
            [
                self.draw_polygon_radio,
                self.draw_button,
                self.clear_aoi_button,
            ],
            breakpoint=430,
        )
        aoi_layout.addWidget(
            self.draw_row_widget
        )

        self.draw_status_label = QLabel(
            "No drawn AOI."
        )
        self.draw_status_label.setWordWrap(True)
        self.draw_status_label.hide()
        aoi_layout.addWidget(
            self.draw_status_label
        )

        buffer_widget = QWidget()
        buffer_layout = QHBoxLayout(
            buffer_widget
        )
        buffer_layout.setContentsMargins(
            0, 0, 0, 0
        )
        buffer_layout.setSpacing(4)

        buffer_layout.addWidget(
            QLabel("Buffer:")
        )

        self.buffer_spin = shrink_field(
            QDoubleSpinBox()
        )
        self.buffer_spin.setRange(
            0.0,
            1000000.0,
        )
        self.buffer_spin.setDecimals(1)
        self.buffer_spin.setValue(0.0)
        self.buffer_spin.valueChanged.connect(
            plugin.buffer_value_changed
        )
        self.buffer_spin.editingFinished.connect(
            plugin.save_project_settings
        )
        buffer_layout.addWidget(
            self.buffer_spin,
            1,
        )

        self.buffer_unit_combo = shrink_field(
            QComboBox()
        )
        self.buffer_unit_combo.addItem(
            "metres",
            "m",
        )
        self.buffer_unit_combo.addItem(
            "kilometres",
            "km",
        )
        self.buffer_unit_combo.addItem(
            "feet",
            "ft",
        )
        self.buffer_unit_combo.addItem(
            "miles",
            "mi",
        )
        self.buffer_unit_combo.currentIndexChanged.connect(
            plugin.buffer_unit_changed
        )
        buffer_layout.addWidget(
            self.buffer_unit_combo,
            1,
        )
        buffer_layout.addWidget(
            HelpButton(
                "Optional AOI buffer. The entered value may be metres, "
                "kilometres, feet or miles. Internally the plugin converts it "
                "to metres and applies the existing local-UTM buffering method "
                "for spatial accuracy."
            )
        )
        aoi_layout.addWidget(
            buffer_widget
        )

        self.auto_select_checkbox = QCheckBox(
            "Automatically select found tiles"
        )
        self.auto_select_checkbox.setChecked(True)
        self.auto_select_checkbox.setToolTip(
            "When enabled, every tile intersecting the AOI is selected after "
            "the query. You can then deselect unwanted edge tiles."
        )
        aoi_layout.addWidget(
            self.auto_select_checkbox
        )

        self.find_tiles_button = QPushButton(
            "Find Available DTM Tiles"
        )
        self.find_tiles_button.clicked.connect(
            plugin.find_tiles
        )
        aoi_layout.addWidget(
            self.find_tiles_button
        )

        self.aoi_section = CollapsibleSection(
            "2. Area of Interest",
            aoi_group,
            settings=plugin.settings,
            settings_key="OntarioDTMManager/ui/v1/section2Expanded",
            default_expanded=True,
        )
        layout.addWidget(self.aoi_section)

        # ====================================================
        # 3. TILE SELECTION
        # ====================================================
        selection_group = QGroupBox()
        selection_group.setFlat(True)
        selection_layout = QVBoxLayout(
            selection_group
        )

        self.available_label = QLabel(
            "Available tiles: 0"
        )
        self.selected_label = QLabel(
            "Selected tiles: 0"
        )
        self.cached_label = QLabel(
            "Already cached: 0"
        )
        self.missing_label = QLabel(
            "Need download: 0"
        )

        for label in (
            self.available_label,
            self.selected_label,
            self.cached_label,
            self.missing_label,
        ):
            label.setWordWrap(True)
            selection_layout.addWidget(label)

        self.labels_checkbox = QCheckBox(
            "Show full TileName labels"
        )
        self.labels_checkbox.setChecked(False)
        self.labels_checkbox.toggled.connect(
            plugin.toggle_tile_labels
        )
        selection_layout.addWidget(
            self.labels_checkbox
        )

        self.select_all_button = QPushButton(
            "Select All"
        )
        self.clear_button = QPushButton(
            "Clear"
        )
        self.zoom_button = QPushButton(
            "Zoom to Tiles"
        )
        self.select_all_button.clicked.connect(
            plugin.select_all_tiles
        )
        self.clear_button.clicked.connect(
            plugin.clear_tile_selection
        )
        self.zoom_button.clicked.connect(
            plugin.zoom_to_tiles
        )
        selection_layout.addWidget(
            ResponsiveRow(
                [
                    self.select_all_button,
                    self.clear_button,
                    self.zoom_button,
                ]
            )
        )

        self.selection_section = CollapsibleSection(
            "3. Tile Selection",
            selection_group,
            settings=plugin.settings,
            settings_key="OntarioDTMManager/ui/v1/section3Expanded",
            default_expanded=False,
        )
        layout.addWidget(
            self.selection_section
        )

        # ====================================================
        # 4. TERRAIN
        # ====================================================
        terrain_group = QGroupBox()
        terrain_group.setFlat(True)
        terrain_layout = QVBoxLayout(
            terrain_group
        )

        # Compatibility labels retained, but path/status detail is exposed
        # through tooltips rather than permanently consuming vertical space.
        self.project_label = QLabel()
        self.project_label.hide()

        terrain_form = QFormLayout()
        configure_form(
            terrain_form
        )

        self.global_cache_checkbox = QCheckBox(
            "Use global/shared cache"
        )
        self.global_cache_checkbox.setChecked(True)
        self.global_cache_checkbox.setToolTip(
            "Checked: use one cache root shared across QGIS projects. "
            "Unchecked: use a custom cache root stored with this project. "
            "DTM and DSM data remain isolated inside the selected cache."
        )
        self.global_cache_checkbox.toggled.connect(
            plugin.cache_mode_changed
        )
        terrain_layout.addWidget(
            self.global_cache_checkbox
        )

        self.cache_edit = shrink_field(
            QLineEdit()
        )
        self.cache_edit.editingFinished.connect(
            plugin.cache_path_changed
        )
        self.cache_browse = QPushButton(
            "Browse..."
        )
        self.cache_browse.clicked.connect(
            plugin.browse_cache
        )
        cache_row = ResponsiveRow(
            [
                self.cache_edit,
                self.cache_browse,
            ],
            breakpoint=370,
        )
        terrain_form.addRow(
            label_help(
                "Cache root:",
                "Root folder used by the active global/shared or project-specific "
                "cache. Hover the path field to see the full active dataset cache."
            ),
            cache_row,
        )

        self.active_cache_path_label = QLabel()
        self.active_cache_path_label.hide()

        self.terrain_name_edit = shrink_field(
            QLineEdit()
        )
        self.terrain_name_edit.editingFinished.connect(
            plugin.project_output_changed
        )
        terrain_form.addRow(
            label_help(
                "Terrain name:",
                "Name used for the working VRT filename and the QGIS terrain layer."
            ),
            self.terrain_name_edit,
        )

        self.project_output_checkbox = QCheckBox(
            "Use QGIS project folder"
        )
        self.project_output_checkbox.setChecked(True)
        self.project_output_checkbox.setToolTip(
            "Checked: save the working VRT beside the current QGIS project. "
            "Unchecked: choose a custom output directory for this project."
        )
        self.project_output_checkbox.toggled.connect(
            plugin.output_mode_changed
        )
        terrain_layout.addWidget(
            self.project_output_checkbox
        )

        self.output_edit = shrink_field(
            QLineEdit()
        )
        self.output_edit.editingFinished.connect(
            plugin.project_output_changed
        )
        self.output_browse = QPushButton(
            "Browse..."
        )
        self.output_browse.clicked.connect(
            plugin.browse_output_folder
        )
        output_row = ResponsiveRow(
            [
                self.output_edit,
                self.output_browse,
            ],
            breakpoint=370,
        )
        terrain_form.addRow(
            label_help(
                "Output folder:",
                "Location for the working VRT. In automatic mode this is the "
                "current .qgz/.qgs project folder; otherwise it is your chosen directory."
            ),
            output_row,
        )

        self.vrt_edit = shrink_field(
            QLineEdit()
        )
        self.vrt_edit.setReadOnly(True)
        terrain_form.addRow(
            label_help(
                "Project VRT:",
                "Calculated working VRT path. The VRT references cached native "
                "Ontario rasters without duplicating them."
            ),
            self.vrt_edit,
        )

        terrain_layout.addLayout(
            terrain_form
        )

        # Nested collapsible lifecycle subsection.
        lifecycle_group = QGroupBox()
        lifecycle_group.setFlat(True)
        lifecycle_layout = QVBoxLayout(
            lifecycle_group
        )

        self.terrain_state_label = QLabel(
            "Terrain status: Not created"
        )
        self.terrain_current_label = QLabel(
            "Current terrain tiles: 0"
        )
        self.terrain_keep_label = QLabel(
            "Keep unchanged: --"
        )
        self.terrain_add_label = QLabel(
            "Add to terrain: --"
        )
        self.terrain_remove_label = QLabel(
            "Remove from terrain: --"
        )

        for label in (
            self.terrain_state_label,
            self.terrain_current_label,
            self.terrain_keep_label,
            self.terrain_add_label,
            self.terrain_remove_label,
        ):
            label.setWordWrap(True)
            lifecycle_layout.addWidget(
                label
            )

        self.analyze_terrain_button = QPushButton(
            "Refresh Change Summary"
        )
        self.analyze_terrain_button.clicked.connect(
            plugin.refresh_terrain_lifecycle
        )

        self.select_existing_button = QPushButton(
            "Select Current Terrain Tiles"
        )
        self.select_existing_button.clicked.connect(
            plugin.select_existing_terrain_tiles
        )

        lifecycle_layout.addWidget(
            ResponsiveRow(
                [
                    self.analyze_terrain_button,
                    self.select_existing_button,
                ],
                breakpoint=410,
            )
        )

        self.backup_vrt_checkbox = QCheckBox(
            "Keep VRT backup before update"
        )
        self.backup_vrt_checkbox.setChecked(True)
        self.backup_vrt_checkbox.setToolTip(
            "Before replacing an existing terrain VRT, keep a timestamped copy "
            "of the previous VRT definition. Removing a tile from a terrain "
            "does not delete the cached Ontario source raster."
        )
        self.backup_vrt_checkbox.toggled.connect(
            plugin.save_project_settings
        )
        lifecycle_layout.addWidget(
            self.backup_vrt_checkbox
        )

        self.lifecycle_note = QLabel()
        self.lifecycle_note.hide()

        self.lifecycle_section = CollapsibleSection(
            "Terrain Update / Lifecycle",
            lifecycle_group,
            settings=plugin.settings,
            settings_key="OntarioDTMManager/ui/v1/lifecycleExpanded",
            default_expanded=False,
        )
        terrain_layout.addWidget(
            self.lifecycle_section
        )

        self.project_defaults_button = QPushButton(
            "Use QGIS Project Output Defaults"
        )
        self.project_defaults_button.clicked.connect(
            plugin.use_project_defaults
        )
        self.project_defaults_button.setToolTip(
            "Reset the terrain name and output folder to defaults derived from "
            "the current QGIS project."
        )
        terrain_layout.addWidget(
            self.project_defaults_button
        )

        self.delete_zip_checkbox = QCheckBox(
            "Delete source ZIP after extraction"
        )
        self.delete_zip_checkbox.setChecked(True)
        self.delete_zip_checkbox.setToolTip(
            "Delete a completed Ontario package ZIP after every requested raster "
            "has been extracted successfully. Cached raster tiles are retained."
        )
        terrain_layout.addWidget(
            self.delete_zip_checkbox
        )

        self.add_to_project_checkbox = QCheckBox(
            "Add VRT to QGIS project"
        )
        self.add_to_project_checkbox.setChecked(True)
        terrain_layout.addWidget(
            self.add_to_project_checkbox
        )

        self.build_button = QPushButton(
            "Download / Build Terrain"
        )
        self.build_button.clicked.connect(
            plugin.download_and_build
        )

        self.cancel_button = QPushButton(
            "Cancel"
        )
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(
            plugin.cancel_active_task
        )

        terrain_layout.addWidget(
            ResponsiveRow(
                [
                    self.build_button,
                    self.cancel_button,
                ]
            )
        )

        self.progress = QProgressBar()
        self.progress.setRange(
            0,
            100,
        )
        self.progress.setValue(0)
        terrain_layout.addWidget(
            self.progress
        )

        self.status_label = QLabel(
            "Ready"
        )
        self.status_label.setWordWrap(True)
        terrain_layout.addWidget(
            self.status_label
        )

        self.terrain_section = CollapsibleSection(
            "4. Terrain",
            terrain_group,
            settings=plugin.settings,
            settings_key="OntarioDTMManager/ui/v1/section4Expanded",
            default_expanded=False,
        )
        layout.addWidget(
            self.terrain_section
        )

        # ====================================================
        # 5. CACHE / DATASET MANAGEMENT
        # ====================================================
        cache_manager_group = QGroupBox()
        cache_manager_group.setFlat(True)
        cache_manager_layout = QVBoxLayout(
            cache_manager_group
        )

        self.cache_scope_label = QLabel(
            "Cache scope: --"
        )
        self.cache_scope_label.setWordWrap(True)
        self.cache_scope_label.setToolTip(
            "Shows whether the active cache root is shared across projects or "
            "stored specifically for the current QGIS project."
        )
        cache_manager_layout.addWidget(
            self.cache_scope_label
        )

        self.cache_stat_tiles = stat_row(
            cache_manager_layout,
            "Raster tiles: --",
            "Number and disk size of native Ontario raster tiles currently "
            "stored in the active dataset cache."
        )
        self.cache_stat_sidecars = stat_row(
            cache_manager_layout,
            "Sidecar/support files: --",
            "Auxiliary files associated with cached source rasters, such as "
            "overview or metadata files."
        )
        self.cache_stat_zips = stat_row(
            cache_manager_layout,
            "Completed package ZIPs: --",
            "Fully downloaded Ontario source-package archives still retained in cache."
        )
        self.cache_stat_partials = stat_row(
            cache_manager_layout,
            "Partial downloads: --",
            "Incomplete .part package downloads which can normally be resumed."
        )
        self.cache_stat_extracting = stat_row(
            cache_manager_layout,
            "Temporary extracts: --",
            "Temporary .extracting files created during selective extraction. "
            "These normally disappear after a successful extraction."
        )
        self.cache_stat_current = stat_row(
            cache_manager_layout,
            "Current terrain cached tiles: --",
            "Cached source tiles referenced by the currently configured terrain VRT."
        )
        self.cache_stat_unused = stat_row(
            cache_manager_layout,
            "Not used by current terrain/selection: --",
            "Cached tiles not referenced by the current terrain and not selected "
            "in the current tile layer. In a global cache they may still be useful "
            "to another QGIS project."
        )
        self.cache_stat_total = stat_row(
            cache_manager_layout,
            "Total cache size: --",
            "Total disk space occupied by the active dataset cache."
        )
        self.cache_stat_free = stat_row(
            cache_manager_layout,
            "Free disk space: --",
            "Free space on the disk containing the active dataset cache."
        )

        # Compatibility object retained for older backend error/status code.
        self.cache_summary_label = QLabel()
        self.cache_summary_label.hide()

        self.refresh_cache_button = QPushButton(
            "Refresh Cache Summary"
        )
        self.refresh_cache_button.clicked.connect(
            plugin.refresh_cache_manager
        )
        self.open_cache_button = QPushButton(
            "Open Cache Folder"
        )
        self.open_cache_button.clicked.connect(
            plugin.open_cache_folder
        )
        self.cleanup_cache_button = QPushButton(
            "Manage / Cleanup..."
        )
        self.cleanup_cache_button.clicked.connect(
            plugin.manage_cache_cleanup
        )

        cache_manager_layout.addWidget(
            ResponsiveRow(
                [
                    self.refresh_cache_button,
                    self.open_cache_button,
                    self.cleanup_cache_button,
                ],
                breakpoint=430,
            )
        )

        self.cache_section = CollapsibleSection(
            "5. Cache / Dataset Management",
            cache_manager_group,
            settings=plugin.settings,
            settings_key="OntarioDTMManager/ui/v1/section5Expanded",
            default_expanded=False,
        )
        layout.addWidget(
            self.cache_section
        )

        # ====================================================
        # 6. TERRAIN EXPORT / DELIVERY
        # ====================================================
        export_group = QGroupBox()
        export_group.setFlat(True)
        export_layout = QVBoxLayout(
            export_group
        )

        self.export_state_label = QLabel(
            "Export status: Not checked"
        )
        self.export_state_label.setWordWrap(True)
        self.export_state_label.setToolTip(
            "Indicates whether the configured deliverable exists and whether "
            "it still matches the current working terrain and export settings."
        )
        export_layout.addWidget(
            self.export_state_label
        )

        self.export_qa_label = QLabel(
            "Terrain QA not analyzed yet."
        )
        self.export_qa_label.setWordWrap(True)
        self.export_qa_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.export_qa_label.setToolTip(
            "Terrain QA summarizes source tile count, pixel size, CRS, NoData, "
            "elevation range and available Ontario source metadata."
        )
        export_layout.addWidget(
            self.export_qa_label
        )

        self.refresh_export_qa_button = QPushButton(
            "Refresh Terrain QA"
        )
        self.refresh_export_qa_button.clicked.connect(
            plugin.refresh_export_qa
        )
        self.open_export_button = QPushButton(
            "Open Export Folder"
        )
        self.open_export_button.clicked.connect(
            plugin.open_export_folder
        )
        export_layout.addWidget(
            ResponsiveRow(
                [
                    self.refresh_export_qa_button,
                    self.open_export_button,
                ]
            )
        )

        export_form = QFormLayout()
        configure_form(
            export_form
        )

        self.export_name_edit = shrink_field(
            QLineEdit()
        )
        self.export_name_edit.editingFinished.connect(
            plugin.export_options_changed
        )
        export_form.addRow(
            label_help(
                "Deliverable name:",
                "Filename stem used for the optional exported terrain deliverable."
            ),
            self.export_name_edit,
        )

        self.export_clip_combo = shrink_field(
            QComboBox()
        )
        self.export_clip_combo.addItem(
            "AOI + buffer (recommended)",
            "buffer",
        )
        self.export_clip_combo.addItem(
            "AOI only",
            "aoi",
        )
        self.export_clip_combo.addItem(
            "Polygon layer from current project",
            "project_polygon",
        )
        self.export_clip_combo.addItem(
            "Polygon vector file",
            "file_polygon",
        )
        self.export_clip_combo.addItem(
            "No clip — working VRT extent",
            "none",
        )
        self.export_clip_combo.currentIndexChanged.connect(
            plugin.export_options_changed
        )
        self.export_clip_combo.currentIndexChanged.connect(
            plugin.update_export_clip_controls
        )
        export_form.addRow(
            label_help(
                "Clip extent:",
                "Choose whether to export the AOI, buffered AOI, a polygon layer "
                "already in the project, an external polygon vector file, or the "
                "full working VRT extent. Non-polygon vector sources are rejected."
            ),
            self.export_clip_combo,
        )

        project_clip_widget = QWidget()
        project_clip_layout = QHBoxLayout(
            project_clip_widget
        )
        project_clip_layout.setContentsMargins(
            0, 0, 0, 0
        )
        self.export_project_polygon_combo = shrink_field(
            QComboBox()
        )
        self.export_project_polygon_combo.currentIndexChanged.connect(
            plugin.export_options_changed
        )
        project_clip_layout.addWidget(
            self.export_project_polygon_combo,
            1,
        )
        self.refresh_project_polygon_button = QPushButton(
            "Refresh"
        )
        self.refresh_project_polygon_button.clicked.connect(
            plugin.refresh_export_project_polygon_layers
        )
        project_clip_layout.addWidget(
            self.refresh_project_polygon_button
        )
        self.export_project_polygon_widget = project_clip_widget
        export_form.addRow(
            "Project polygon:",
            project_clip_widget,
        )

        file_clip_widget = QWidget()
        file_clip_layout = QHBoxLayout(
            file_clip_widget
        )
        file_clip_layout.setContentsMargins(
            0, 0, 0, 0
        )
        self.export_file_polygon_edit = shrink_field(
            QLineEdit()
        )
        self.export_file_polygon_edit.setReadOnly(
            True
        )
        file_clip_layout.addWidget(
            self.export_file_polygon_edit,
            1,
        )
        self.browse_export_file_polygon_button = QPushButton(
            "Browse..."
        )
        self.browse_export_file_polygon_button.clicked.connect(
            plugin.browse_export_clip_vector
        )
        file_clip_layout.addWidget(
            self.browse_export_file_polygon_button
        )
        self.export_file_polygon_widget = file_clip_widget
        export_form.addRow(
            "Vector file:",
            file_clip_widget,
        )

        self.export_format_combo = shrink_field(
            QComboBox()
        )
        self.export_format_combo.addItem(
            "GeoTIFF",
            "GTiff",
        )
        self.export_format_combo.addItem(
            "Cloud Optimized GeoTIFF (COG)",
            "COG",
        )
        self.export_format_combo.currentIndexChanged.connect(
            plugin.export_options_changed
        )
        export_form.addRow(
            label_help(
                "Output format:",
                "GeoTIFF creates a conventional tiled raster. COG creates a "
                "Cloud Optimized GeoTIFF for efficient range-based access. "
                "The working VRT and cached Ontario source rasters are not modified."
            ),
            self.export_format_combo,
        )

        self.export_crs_combo = shrink_field(
            QComboBox()
        )
        self.export_crs_combo.addItem(
            "Native Ontario source CRS",
            "native",
        )
        self.export_crs_combo.addItem(
            "Current QGIS project CRS",
            "project",
        )
        self.export_crs_combo.currentIndexChanged.connect(
            plugin.export_options_changed
        )
        export_form.addRow(
            label_help(
                "Output CRS:",
                "Keep the native source CRS by default, or reproject the exported "
                "deliverable to the current QGIS project CRS."
            ),
            self.export_crs_combo,
        )

        self.export_resolution_combo = shrink_field(
            QComboBox()
        )
        self.export_resolution_combo.addItem(
            "Native resolution",
            None,
        )
        self.export_resolution_combo.addItem(
            "0.5 m",
            0.5,
        )
        self.export_resolution_combo.addItem(
            "1 m",
            1.0,
        )
        self.export_resolution_combo.addItem(
            "2 m",
            2.0,
        )
        self.export_resolution_combo.addItem(
            "5 m",
            5.0,
        )
        self.export_resolution_combo.currentIndexChanged.connect(
            plugin.export_options_changed
        )
        export_form.addRow(
            label_help(
                "Output resolution:",
                "Native preserves the source pixel size. Choosing another "
                "resolution resamples only the exported deliverable."
            ),
            self.export_resolution_combo,
        )

        self.export_path_edit = shrink_field(
            QLineEdit()
        )
        self.export_path_edit.setReadOnly(
            True
        )
        export_form.addRow(
            label_help(
                "Deliverable path:",
                "Calculated output path generated from the project output folder "
                "and deliverable name."
            ),
            self.export_path_edit,
        )

        export_layout.addLayout(
            export_form
        )

        self.add_export_to_project_checkbox = QCheckBox(
            "Add exported raster to QGIS project"
        )
        self.add_export_to_project_checkbox.setChecked(
            True
        )
        self.add_export_to_project_checkbox.toggled.connect(
            plugin.save_project_settings
        )
        export_layout.addWidget(
            self.add_export_to_project_checkbox
        )

        self.export_button = QPushButton(
            "Export Terrain Deliverable"
        )
        self.export_button.clicked.connect(
            plugin.export_terrain
        )
        self.cancel_export_button = QPushButton(
            "Cancel Export"
        )
        self.cancel_export_button.setEnabled(
            False
        )
        self.cancel_export_button.clicked.connect(
            plugin.cancel_export_task
        )
        export_layout.addWidget(
            ResponsiveRow(
                [
                    self.export_button,
                    self.cancel_export_button,
                ]
            )
        )

        self.export_progress = QProgressBar()
        self.export_progress.setRange(
            0,
            100,
        )
        self.export_progress.setValue(
            0
        )
        export_layout.addWidget(
            self.export_progress
        )

        self.export_status_label = QLabel(
            "Ready"
        )
        self.export_status_label.setWordWrap(
            True
        )
        self.export_status_label.setToolTip(
            "Export uses bilinear resampling only when reprojection or an "
            "explicit output-resolution change is requested. The working VRT "
            "and cached Ontario source rasters remain unchanged."
        )
        export_layout.addWidget(
            self.export_status_label
        )

        self.export_section = CollapsibleSection(
            "6. Terrain Export / Delivery",
            export_group,
            settings=plugin.settings,
            settings_key="OntarioDTMManager/ui/v1/section6Expanded",
            default_expanded=False,
        )
        layout.addWidget(
            self.export_section
        )

        layout.addStretch(1)

        # Global responsive policy: no horizontal scrolling, fields may shrink,
        # and button rows stack automatically.
        for widget_type in (QLineEdit, QComboBox):
            for field in body.findChildren(widget_type):
                field.setMinimumWidth(0)
                policy = field.sizePolicy()
                policy.setHorizontalPolicy(
                    QSizePolicy.Policy.Expanding
                )
                field.setSizePolicy(policy)
                if isinstance(field, QComboBox):
                    field.setSizeAdjustPolicy(
                        QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
                    )
                    field.setMinimumContentsLength(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(
            True
        )
        scroll.setFrameShape(
            QScrollArea.Shape.NoFrame
        )
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll.setWidget(
            body
        )
        self.setWidget(
            scroll
        )

class CacheCleanupDialog(QDialog):
    def __init__(
        self,
        parent,
        inventory,
        global_cache,
    ):
        super().__init__(parent)
        self.setWindowTitle(
            "Ontario Elevation Cache Cleanup"
        )
        self.setModal(True)
        self.resize(560, 360)

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Choose which cache items to delete. Nothing is selected by default."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.zip_checkbox = QCheckBox(
            "Delete completed package ZIPs "
            f"({inventory['zip_count']} file(s), "
            f"{inventory['zip_size_text']})"
        )
        self.zip_checkbox.setChecked(False)
        self.zip_checkbox.setEnabled(
            inventory["zip_count"] > 0
        )
        layout.addWidget(
            self.zip_checkbox
        )

        self.partial_checkbox = QCheckBox(
            "Delete partial downloads (.part) "
            f"({inventory['partial_count']} file(s), "
            f"{inventory['partial_size_text']})"
        )
        self.partial_checkbox.setChecked(False)
        self.partial_checkbox.setEnabled(
            inventory["partial_count"] > 0
        )
        self.partial_checkbox.setToolTip(
            "Deleting a .part file removes the ability to resume that partial download."
        )
        layout.addWidget(
            self.partial_checkbox
        )

        self.extracting_checkbox = QCheckBox(
            "Delete temporary extraction files (.extracting) "
            f"({inventory['extracting_count']} file(s), "
            f"{inventory['extracting_size_text']})"
        )
        self.extracting_checkbox.setChecked(False)
        self.extracting_checkbox.setEnabled(
            inventory["extracting_count"] > 0
        )
        layout.addWidget(
            self.extracting_checkbox
        )

        unused_text = (
            "Delete cached terrain tiles not used by the CURRENT terrain "
            "or current tile selection "
            f"({inventory['unused_tile_count']} tile(s), "
            f"{inventory['unused_tile_size_text']} including sidecars)"
        )
        self.unused_tiles_checkbox = QCheckBox(
            unused_text
        )
        self.unused_tiles_checkbox.setChecked(False)
        self.unused_tiles_checkbox.setEnabled(
            inventory["terrain_exists"]
            and inventory["unused_tile_count"] > 0
        )

        if not inventory["terrain_exists"]:
            self.unused_tiles_checkbox.setToolTip(
                "Disabled because the configured project terrain VRT does not exist."
            )
        elif global_cache:
            self.unused_tiles_checkbox.setToolTip(
                "CAUTION: this is a global/shared cache. Tiles not used by the "
                "current project may still be used by other projects."
            )

        layout.addWidget(
            self.unused_tiles_checkbox
        )

        if global_cache:
            warning = QLabel(
                "Global/shared cache warning: pruning raster tiles can force another "
                "project to re-download them later. Completed ZIP and partial-file "
                "cleanup does not modify any existing VRT."
            )
            warning.setWordWrap(True)
            layout.addWidget(
                warning
            )

        self.selected_size_label = QLabel(
            "Selected cleanup: 0 B"
        )
        self.selected_size_label.setWordWrap(True)
        layout.addWidget(
            self.selected_size_label
        )

        for checkbox in (
            self.zip_checkbox,
            self.partial_checkbox,
            self.extracting_checkbox,
            self.unused_tiles_checkbox,
        ):
            checkbox.toggled.connect(
                self._update_selected_size
            )

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(
            QDialogButtonBox.StandardButton.Ok
        ).setText(
            "Delete Selected"
        )
        buttons.accepted.connect(
            self.accept
        )
        buttons.rejected.connect(
            self.reject
        )
        layout.addWidget(
            buttons
        )

        self.inventory = inventory
        self._update_selected_size()

    def _update_selected_size(self, *args):
        total = 0

        if self.zip_checkbox.isChecked():
            total += self.inventory["zip_size"]

        if self.partial_checkbox.isChecked():
            total += self.inventory["partial_size"]

        if self.extracting_checkbox.isChecked():
            total += self.inventory["extracting_size"]

        if self.unused_tiles_checkbox.isChecked():
            total += self.inventory["unused_tile_size"]

        self.selected_size_label.setText(
            "Selected cleanup: "
            + self.inventory["format_bytes"](total)
        )

    def cleanup_options(self):
        return {
            "zips": self.zip_checkbox.isChecked(),
            "partials": self.partial_checkbox.isChecked(),
            "extracting": self.extracting_checkbox.isChecked(),
            "unused_tiles": self.unused_tiles_checkbox.isChecked(),
        }


class OntarioDTMManagerPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dock = None
        self.tile_layer = None
        self.active_task = None
        self.settings = QSettings()

        self.draw_tool = None
        self.drawn_aoi_3857 = None
        self.aoi_preview_band = None
        self.buffer_preview_band = None
        self._restoring_project_settings = False
        self.custom_cache_folder = ""
        self.custom_output_folder = ""
        self.export_task = None
        self._package_metadata_cache = {}
        self.active_dataset_key = "lidar_dtm"
        self.dataset_terrain_names = {}

        # V1 AOI polygon source. Exactly one project layer or external vector
        # source can be active at a time.
        self.aoi_polygon_source_type = ""
        self.aoi_polygon_project_layer_id = ""
        self.aoi_polygon_file_uri = ""
        self.aoi_polygon_file_display = ""
        self._last_buffer_unit = "m"

        # Optional polygon sources for terrain export clipping.
        self.export_clip_file_uri = ""
        self.export_clip_file_display = ""

    # --------------------------------------------------------
    # PLUGIN LIFECYCLE
    # --------------------------------------------------------
    def initGui(self):
        self.action = QAction(
            QIcon(PLUGIN_ICON_PATH),
            PLUGIN_NAME,
            self.iface.mainWindow(),
        )
        self.action.setObjectName(
            "OntarioDTMManagerAction"
        )
        self.action.triggered.connect(
            self.show_dock
        )

        self.iface.addPluginToRasterMenu(
            PLUGIN_NAME,
            self.action,
        )
        self.iface.addToolBarIcon(
            self.action
        )

        self.dock = OntarioDTMDock(
            self
        )
        self.iface.addDockWidget(
            Qt.DockWidgetArea.RightDockWidgetArea,
            self.dock,
        )
        self.dock.hide()
        self._ensure_aoi_preview_bands()
        self.refresh_export_project_polygon_layers()

        self.restore_project_settings()
        self._set_default_vrt_if_blank()
        self.update_export_defaults()
        self.update_counts()
        self.refresh_terrain_lifecycle()
        self.update_export_clip_controls()
        self.update_aoi_source_controls()
        self.refresh_export_status()

    def unload(self):
        if self.active_task is not None:
            with suppress(Exception):
                self.active_task.cancel()

        if self.export_task is not None:
            with suppress(Exception):
                self.export_task.cancel()

        if self.draw_tool is not None:
            with suppress(Exception):
                self.draw_tool.reset()

        for band in (
            self.aoi_preview_band,
            self.buffer_preview_band,
        ):
            if band is not None:
                with suppress(Exception):
                    band.reset(Qgis.GeometryType.Polygon)
                    band.hide()

        if self.action:
            self.iface.removePluginRasterMenu(
                PLUGIN_NAME,
                self.action,
            )
            self.iface.removeToolBarIcon(
                self.action
            )
            self.action.deleteLater()
            self.action = None

        if self.dock:
            self.iface.removeDockWidget(
                self.dock
            )
            self.dock.deleteLater()
            self.dock = None

    def show_dock(self):
        self._set_default_vrt_if_blank()
        self.refresh_project_label()
        self.refresh_export_project_polygon_layers()
        self.raster_dataset_changed()
        self.dock.show()
        self.dock.raise_()
        self.update_export_defaults()
        self.update_export_clip_controls()
        self.update_counts()
        self.refresh_terrain_lifecycle()
        self.refresh_cache_manager()
        self.update_aoi_source_controls()
        self.refresh_export_status()

    # --------------------------------------------------------
    # ONTARIO ELEVATION DATASETS
    # --------------------------------------------------------
    def _selected_dataset_key(self):
        if not self.dock:
            return "lidar_dtm"
        return self.dock.raster_dataset_combo.currentData() or "lidar_dtm"

    def _selected_raster_dataset(self):
        key = self._selected_dataset_key()
        return RASTER_DATASETS.get(
            key,
            RASTER_DATASETS["lidar_dtm"],
        )

    def _dataset_short_name(self):
        return self._selected_raster_dataset()["short_name"]

    def _dataset_tile_layer_name(self):
        return f"Ontario {self._dataset_short_name()} - Available Tiles"

    def _dataset_terrain_group_name(self):
        return "Ontario Elevation"

    def _dataset_cache_folder(self, base_folder=None):
        if base_folder is None:
            base_folder = self._base_cache_folder()
        if not base_folder:
            return ""
        subdir = self._selected_raster_dataset().get("cache_subdir", "")
        return os.path.join(base_folder, subdir) if subdir else base_folder

    def _base_cache_folder(self):
        if not self.dock:
            return ""
        if self.dock.global_cache_checkbox.isChecked():
            return self._global_cache_folder()
        return self.custom_cache_folder

    def raster_dataset_changed(self, *args):
        if not self.dock:
            return

        dataset = self._selected_raster_dataset()
        short_name = dataset["short_name"]
        old_key = getattr(self, "active_dataset_key", None)
        new_key = self._selected_dataset_key()

        # Remember an edited terrain name while switching datasets during the
        # current QGIS session. Project persistence still records the active
        # dataset/name when the project is saved.
        if (
            old_key
            and old_key != new_key
            and not self._restoring_project_settings
            and self.dock.terrain_name_edit.text().strip()
        ):
            old_name = self._sanitize_terrain_name(
                self.dock.terrain_name_edit.text()
            )
            self.dataset_terrain_names[old_key] = old_name
            self._write_project_entry(
                f"terrainName_{old_key}",
                old_name,
            )

        self.active_dataset_key = new_key
        self.dock.raster_dataset_description.setText(dataset["description"])
        if hasattr(self.dock, "dataset_help"):
            self.dock.dataset_help.set_help_text(
                dataset["description"]
                + "\n\nThe selected dataset is used end-to-end by the AOI, "
                "tile selection, package download, cache, VRT lifecycle and "
                "terrain export sections."
            )

        if dataset.get("tile_index_mode") == "feature_service":
            self.dock.refresh_dataset_index_button.setEnabled(False)
            self.dock.dataset_index_status.setText("Index: Live")
            self.dock.dataset_index_status.setToolTip(
                "The active dataset uses Ontario's live ArcGIS 1 km tile index."
            )
        else:
            self.dock.refresh_dataset_index_button.setEnabled(True)
            index_path = self._local_dataset_index_path()
            if index_path and os.path.exists(index_path):
                self.dock.dataset_index_status.setText("Index: Cached")
                self.dock.dataset_index_status.setToolTip(
                    "Official Ontario tile index cached locally at:\n"
                    + os.path.normpath(index_path)
                    + "\nUse Refresh Dataset Index to force a current copy."
                )
            else:
                self.dock.dataset_index_status.setText("Index: Not cached")
                self.dock.dataset_index_status.setToolTip(
                    "The official Ontario tile index will be downloaded "
                    "automatically on first use."
                )

        self.dock.find_tiles_button.setText(
            f"Find Available {short_name} Tiles"
        )
        self.dock.global_cache_checkbox.setText(
            "Use global/shared cache"
        )
        self.dock.global_cache_checkbox.setToolTip(
            f"Checked: use one cache root shared across QGIS projects. "
            f"Unchecked: use a custom cache root stored with this project. "
            f"The active dataset is {short_name}; DTM and DSM cache data remain isolated."
        )
        self.dock.cache_edit.setToolTip(
            "Cache root selected by the user. DTM keeps the legacy cache layout; "
            "DSM is stored in a separate DSM subfolder to prevent collisions."
        )
        self.dock.lifecycle_note.setText(
            f"Tiles removed from a {short_name} terrain are removed from the VRT only; "
            "cached Ontario source raster files are never deleted by a terrain update."
        )
        self.dock.backup_vrt_checkbox.setToolTip(
            f"Before replacing an existing {short_name} terrain VRT, keep a "
            "timestamped copy of the previous VRT definition. Removing tiles "
            "from the terrain does not delete cached Ontario source rasters."
        )

        if not self._restoring_project_settings:
            if old_key != new_key:
                remembered = (
                    self.dataset_terrain_names.get(new_key)
                    or self._read_project_entry(
                        f"terrainName_{new_key}",
                        "",
                    )
                )
                self.dock.terrain_name_edit.setText(
                    remembered or self._default_terrain_name()
                )
                self.update_vrt_path()
                self.update_export_defaults(force_name=True)

                # Tile selection belongs to one source dataset. Remove only the
                # temporary tile-index layer when switching; completed terrain
                # layers/VRTs remain in the project.
                self._remove_all_available_tile_layers()
            elif not self.dock.terrain_name_edit.text().strip():
                self.dock.terrain_name_edit.setText(
                    self._default_terrain_name()
                )
                self.update_vrt_path()

            self.refresh_cache_controls()
            self.refresh_cache_manager()
            self.refresh_terrain_lifecycle()
            self.refresh_export_status()
            self.save_project_settings()

    def _remove_all_available_tile_layers(self):
        for name in (
            "Ontario DTM - Available Tiles",
            "Ontario DSM - Available Tiles",
        ):
            for layer in QgsProject.instance().mapLayersByName(name):
                QgsProject.instance().removeMapLayer(layer.id())
        self.tile_layer = None
        self.update_counts()

    def _dataset_index_cache_root(self):
        # Keep dataset indexes in the QGIS user profile so querying DSM does
        # not depend on the user's terrain-cache choice.
        return os.path.join(
            QgsApplication.qgisSettingsDirPath(),
            "ontario_dtm_manager",
            "indexes",
            self._dataset_short_name(),
        )

    def _local_dataset_index_path(self):
        dataset = self._selected_raster_dataset()
        if dataset.get("tile_index_mode") != "zip_shapefile":
            return ""
        index_root = self._dataset_index_cache_root()
        index_dir = os.path.join(index_root, "index")
        if not os.path.isdir(index_dir):
            return ""
        for name in os.listdir(index_dir):
            if name.lower().endswith(".shp"):
                return os.path.join(index_dir, name)
        return ""

    def refresh_dataset_index(self):
        dataset = self._selected_raster_dataset()
        if dataset.get("tile_index_mode") != "zip_shapefile":
            self.iface.messageBar().pushMessage(
                PLUGIN_NAME,
                "The selected dataset uses a live Ontario tile index; no local refresh is required.",
                level=Qgis.MessageLevel.Info,
                duration=6,
            )
            return

        index_root = self._dataset_index_cache_root()

        try:
            self.dock.dataset_index_status.setText("Index: Refreshing...")
            self.dock.dataset_index_status.setToolTip(
                f"Downloading the current official Ontario {dataset['short_name']} tile index."
            )
            QApplication.processEvents()
            path = ensure_local_tile_index(
                self._selected_dataset_key(),
                index_root,
                force=True,
            )
            self.dock.dataset_index_status.setText("Index: Cached")
            self.dock.dataset_index_status.setToolTip(
                "Official Ontario tile index refreshed and cached at:\n"
                + os.path.normpath(path)
            )
            self.iface.messageBar().pushMessage(
                PLUGIN_NAME,
                f"Ontario {dataset['short_name']} tile index refreshed.",
                level=Qgis.MessageLevel.Success,
                duration=8,
            )
        except Exception as exc:
            self.dock.dataset_index_status.setText("Index: Refresh failed")
            self.dock.dataset_index_status.setToolTip(
                "The tile-index refresh failed. See the error dialog for details."
            )
            QMessageBox.critical(
                self.iface.mainWindow(),
                PLUGIN_NAME,
                str(exc),
            )

    def open_selected_dataset_geohub(self):
        import webbrowser
        dataset = self._selected_raster_dataset()
        url = dataset.get("geohub_url", "")
        if url:
            webbrowser.open(url)

    def _query_local_dataset_tiles(self, aoi_geometry_3857):
        dataset_key = self._selected_dataset_key()
        index_root = self._dataset_index_cache_root()
        index_path = ensure_local_tile_index(
            dataset_key,
            index_root,
            force=False,
        )

        index_layer = QgsVectorLayer(
            index_path,
            f"Ontario {self._dataset_short_name()} Tile Index",
            "ogr",
        )
        if not index_layer.isValid():
            raise RuntimeError(
                "The downloaded Ontario tile-index shapefile could not be opened by QGIS."
            )
        if index_layer.geometryType() != Qgis.GeometryType.Polygon:
            raise RuntimeError(
                "The Ontario tile index is not polygon geometry."
            )

        required_fields = {"TileName", "FileName", "Project", "Package"}
        available_fields = {field.name() for field in index_layer.fields()}
        missing = sorted(required_fields - available_fields)
        if missing:
            raise RuntimeError(
                "Ontario tile-index schema is missing required field(s): "
                + ", ".join(missing)
            )

        service_crs = QgsCoordinateReferenceSystem("EPSG:3857")
        query_geometry = QgsGeometry(aoi_geometry_3857)
        if index_layer.crs() != service_crs:
            transform_to_index = QgsCoordinateTransform(
                service_crs,
                index_layer.crs(),
                QgsProject.instance(),
            )
            query_geometry.transform(transform_to_index)

        transform_to_3857 = None
        if index_layer.crs() != service_crs:
            transform_to_3857 = QgsCoordinateTransform(
                index_layer.crs(),
                service_crs,
                QgsProject.instance(),
            )

        request = QgsFeatureRequest().setFilterRect(
            query_geometry.boundingBox()
        )
        records = []

        for feature in index_layer.getFeatures(request):
            geometry = QgsGeometry(feature.geometry())
            if geometry.isNull() or geometry.isEmpty():
                continue
            if not geometry.intersects(query_geometry):
                continue

            if transform_to_3857 is not None:
                geometry.transform(transform_to_3857)

            records.append(
                {
                    "attributes": {
                        "TileName": feature["TileName"],
                        "FileName": feature["FileName"],
                        "Project": feature["Project"],
                        "Package": feature["Package"],
                    },
                    "qgs_geometry": geometry,
                }
            )

        return records, False

    # --------------------------------------------------------
    # EXPORT CLIP POLYGON SOURCES
    # --------------------------------------------------------
    def refresh_export_project_polygon_layers(self, *args):
        if not self.dock:
            return

        current_id = self.dock.export_project_polygon_combo.currentData()
        self.dock.export_project_polygon_combo.blockSignals(True)
        self.dock.export_project_polygon_combo.clear()

        polygon_layers = []
        for layer in QgsProject.instance().mapLayers().values():
            if (
                isinstance(layer, QgsVectorLayer)
                and layer.isValid()
                and layer.geometryType() == Qgis.GeometryType.Polygon
            ):
                polygon_layers.append(layer)

        polygon_layers.sort(
            key=lambda layer: layer.name().lower()
        )

        for layer in polygon_layers:
            self.dock.export_project_polygon_combo.addItem(
                layer.name(),
                layer.id(),
            )

        if current_id:
            index = self.dock.export_project_polygon_combo.findData(current_id)
            if index >= 0:
                self.dock.export_project_polygon_combo.setCurrentIndex(index)

        self.dock.export_project_polygon_combo.blockSignals(False)
        self.update_export_clip_controls()

    def update_export_clip_controls(self, *args):
        if not self.dock:
            return

        mode = self.dock.export_clip_combo.currentData() or "buffer"
        project_mode = mode == "project_polygon"
        file_mode = mode == "file_polygon"

        self.dock.export_project_polygon_widget.setEnabled(project_mode)
        self.dock.export_file_polygon_widget.setEnabled(file_mode)

    def _polygon_layer_geometry_3857(self, layer, prefer_selection=True):
        if layer is None or not layer.isValid():
            raise RuntimeError("The selected polygon layer is not valid.")

        if layer.geometryType() != Qgis.GeometryType.Polygon:
            raise RuntimeError(
                f"'{layer.name()}' is not a polygon layer. Choose a polygon "
                "Shapefile, GeoPackage layer, or project layer."
            )

        features = []
        if prefer_selection:
            try:
                features = layer.selectedFeatures()
            except Exception:
                features = []

        if not features:
            features = list(layer.getFeatures())

        geometries = [
            QgsGeometry(feature.geometry())
            for feature in features
            if feature.hasGeometry()
            and not feature.geometry().isEmpty()
        ]

        if not geometries:
            raise RuntimeError(
                f"Polygon layer '{layer.name()}' contains no usable polygon features."
            )

        geometry = QgsGeometry.unaryUnion(geometries)
        if geometry.isNull() or geometry.isEmpty():
            raise RuntimeError(
                f"Could not combine polygon geometry from '{layer.name()}'."
            )

        service_crs = QgsCoordinateReferenceSystem("EPSG:3857")
        if layer.crs() != service_crs:
            transform = QgsCoordinateTransform(
                layer.crs(),
                service_crs,
                QgsProject.instance(),
            )
            geometry.transform(transform)

        return geometry

    def _project_export_clip_geometry(self):
        layer_id = self.dock.export_project_polygon_combo.currentData()
        if not layer_id:
            raise RuntimeError(
                "Choose a polygon layer from the current QGIS project."
            )

        layer = QgsProject.instance().mapLayer(layer_id)
        if layer is None:
            raise RuntimeError(
                "The configured project polygon layer is no longer available. "
                "Click Refresh and choose another polygon layer."
            )

        # If features are selected, clip to those. Otherwise use all polygon
        # features in the layer.
        return self._polygon_layer_geometry_3857(
            layer,
            prefer_selection=True,
        )

    def _load_polygon_vector_source(self, uri, display_name="Clip Polygon"):
        layer = QgsVectorLayer(
            uri,
            display_name,
            "ogr",
        )

        if not layer.isValid():
            raise RuntimeError(
                "QGIS could not open the selected vector source."
            )

        if layer.geometryType() != Qgis.GeometryType.Polygon:
            raise RuntimeError(
                "The selected vector source is not polygon geometry. "
                "Choose a polygon Shapefile or polygon GeoPackage layer."
            )

        return layer

    def browse_export_clip_vector(self):
        start = ""
        if self.export_clip_file_display:
            start = self.export_clip_file_display.split(" — ", 1)[0]
        if not start:
            start = self._project_folder()

        path, _ = QFileDialog.getOpenFileName(
            self.iface.mainWindow(),
            "Select Polygon Vector for Terrain Clip",
            start,
            (
                "Polygon vector files (*.shp *.gpkg);;"
                "Shapefile (*.shp);;GeoPackage (*.gpkg);;All files (*.*)"
            ),
        )

        if not path:
            return

        path = os.path.normpath(path)
        candidates = []

        # QGIS can report individual GeoPackage sublayers. Inspect each and
        # retain polygon layers only. This also works for single-layer OGR data.
        try:
            details = QgsProviderRegistry.instance().querySublayers(path)
        except Exception:
            details = []

        for detail in details:
            with suppress(Exception):
                name = detail.name()
                uri = detail.uri()
                layer = QgsVectorLayer(uri, name, "ogr")
                if (
                    layer.isValid()
                    and layer.geometryType() == Qgis.GeometryType.Polygon
                ):
                    candidates.append((name, uri))

        if not candidates:
            # Fallback for simple Shapefiles or provider builds where
            # querySublayers does not enumerate the data source.
            layer = QgsVectorLayer(
                path,
                os.path.basename(path),
                "ogr",
            )

            if not layer.isValid():
                raise RuntimeError(
                    "QGIS could not open the selected vector file."
                )

            if layer.geometryType() != Qgis.GeometryType.Polygon:
                QMessageBox.critical(
                    self.iface.mainWindow(),
                    PLUGIN_NAME,
                    "The selected vector file is not polygon geometry. "
                    "Choose a polygon Shapefile or polygon GeoPackage layer.",
                )
                return

            candidates = [(layer.name(), path)]

        if len(candidates) > 1:
            names = [name for name, uri in candidates]
            selected_name, ok = QInputDialog.getItem(
                self.iface.mainWindow(),
                "Choose GeoPackage Polygon Layer",
                "Polygon layer:",
                names,
                0,
                False,
            )
            if not ok:
                return
            selected_index = names.index(selected_name)
            layer_name, uri = candidates[selected_index]
        else:
            layer_name, uri = candidates[0]

        # Final validation before accepting the source.
        self._load_polygon_vector_source(
            uri,
            layer_name,
        )

        self.export_clip_file_uri = uri
        self.export_clip_file_display = (
            path
            if len(candidates) == 1 and layer_name == os.path.basename(path)
            else f"{path} — {layer_name}"
        )
        self.dock.export_file_polygon_edit.setText(
            self.export_clip_file_display
        )
        self.dock.export_clip_combo.setCurrentIndex(
            self.dock.export_clip_combo.findData("file_polygon")
        )
        self.export_options_changed()

    def _file_export_clip_geometry(self):
        if not self.export_clip_file_uri:
            raise RuntimeError(
                "Browse to a polygon Shapefile or GeoPackage layer first."
            )

        layer = self._load_polygon_vector_source(
            self.export_clip_file_uri,
            "Terrain Clip Polygon",
        )
        return self._polygon_layer_geometry_3857(
            layer,
            prefer_selection=False,
        )


    # --------------------------------------------------------
    # V1 AOI POLYGON SOURCE
    # --------------------------------------------------------
    def _polygon_project_layers(self):
        layers = [
            layer
            for layer in QgsProject.instance().mapLayers().values()
            if isinstance(layer, QgsVectorLayer)
            and layer.isValid()
            and layer.geometryType() == Qgis.GeometryType.Polygon
        ]
        return sorted(
            layers,
            key=lambda layer: layer.name().lower(),
        )

    def choose_aoi_project_layer(self):
        layers = self._polygon_project_layers()

        if not layers:
            QMessageBox.information(
                self.iface.mainWindow(),
                PLUGIN_NAME,
                "No polygon vector layers are currently loaded in the QGIS project.",
            )
            return

        # Disambiguate duplicate layer names without exposing internal IDs in
        # the normal case.
        name_counts = {}
        for layer in layers:
            name_counts[layer.name()] = name_counts.get(layer.name(), 0) + 1

        labels = []
        for layer in layers:
            label = layer.name()
            if name_counts[label] > 1:
                provider = layer.providerType() or "vector"
                label = f"{label} [{provider}] — {layer.id()[-8:]}"
            labels.append(label)

        selected_label, ok = QInputDialog.getItem(
            self.iface.mainWindow(),
            "Choose AOI Polygon Layer",
            "Polygon layer:",
            labels,
            0,
            False,
        )

        if not ok:
            return

        index = labels.index(selected_label)
        layer = layers[index]

        self.aoi_polygon_source_type = "project"
        self.aoi_polygon_project_layer_id = layer.id()
        self.aoi_polygon_file_uri = ""
        self.aoi_polygon_file_display = ""
        self.dock.selected_polygon_radio.setChecked(True)
        self.update_aoi_source_controls()
        self.save_project_settings()
        self.refresh_export_status()

    def _browse_polygon_vector_source(self, title, start=""):
        path, _ = QFileDialog.getOpenFileName(
            self.iface.mainWindow(),
            title,
            start,
            (
                "Vector data (*.shp *.gpkg *.geojson *.json *.sqlite *.gml "
                "*.kml *.tab *.mif);;"
                "Shapefile (*.shp);;GeoPackage (*.gpkg);;"
                "GeoJSON (*.geojson *.json);;All files (*.*)"
            ),
        )

        if not path:
            return None

        path = os.path.normpath(path)
        candidates = []

        try:
            details = QgsProviderRegistry.instance().querySublayers(path)
        except Exception:
            details = []

        for detail in details:
            with suppress(Exception):
                name = detail.name()
                uri = detail.uri()
                layer = QgsVectorLayer(
                    uri,
                    name,
                    "ogr",
                )
                if (
                    layer.isValid()
                    and layer.geometryType() == Qgis.GeometryType.Polygon
                ):
                    candidates.append(
                        (name, uri)
                    )

        if not candidates:
            layer = QgsVectorLayer(
                path,
                os.path.basename(path),
                "ogr",
            )

            if not layer.isValid():
                QMessageBox.critical(
                    self.iface.mainWindow(),
                    PLUGIN_NAME,
                    "QGIS could not open the selected vector dataset.",
                )
                return None

            if layer.geometryType() != Qgis.GeometryType.Polygon:
                QMessageBox.critical(
                    self.iface.mainWindow(),
                    PLUGIN_NAME,
                    "The selected vector dataset is not polygon geometry. "
                    "Choose a polygon vector layer.",
                )
                return None

            candidates = [
                (layer.name(), path)
            ]

        if len(candidates) > 1:
            names = [
                name
                for name, uri in candidates
            ]
            selected_name, ok = QInputDialog.getItem(
                self.iface.mainWindow(),
                "Choose Polygon Layer",
                "Polygon layer:",
                names,
                0,
                False,
            )

            if not ok:
                return None

            selected_index = names.index(
                selected_name
            )
            layer_name, uri = candidates[
                selected_index
            ]
        else:
            layer_name, uri = candidates[0]

        # Final geometry validation.
        self._load_polygon_vector_source(
            uri,
            layer_name,
        )

        display = (
            path
            if (
                len(candidates) == 1
                and layer_name == os.path.basename(path)
            )
            else f"{path} — {layer_name}"
        )
        return uri, display

    def browse_aoi_vector(self):
        start = ""
        if self.aoi_polygon_file_display:
            start = self.aoi_polygon_file_display.split(
                " — ",
                1,
            )[0]

        if not start:
            start = self._project_folder()

        result = self._browse_polygon_vector_source(
            "Select Polygon Vector for Area of Interest",
            start,
        )

        if not result:
            return

        uri, display = result
        self.aoi_polygon_source_type = "file"
        self.aoi_polygon_project_layer_id = ""
        self.aoi_polygon_file_uri = uri
        self.aoi_polygon_file_display = display
        self.dock.selected_polygon_radio.setChecked(True)
        self.update_aoi_source_controls()
        self.save_project_settings()
        self.refresh_export_status()

    def _aoi_polygon_source_geometry(self):
        if self.aoi_polygon_source_type == "project":
            layer = QgsProject.instance().mapLayer(
                self.aoi_polygon_project_layer_id
            )

            if layer is None:
                raise RuntimeError(
                    "The selected project AOI polygon layer is no longer available. "
                    "Click 'From Project...' and choose another polygon layer."
                )

            # Agreed V1 behavior: selected features if present; otherwise all.
            return self._polygon_layer_geometry_3857(
                layer,
                prefer_selection=True,
            )

        if self.aoi_polygon_source_type == "file":
            if not self.aoi_polygon_file_uri:
                raise RuntimeError(
                    "Browse to a polygon vector dataset for the AOI first."
                )

            layer = self._load_polygon_vector_source(
                self.aoi_polygon_file_uri,
                "AOI Polygon",
            )

            # External source is not part of the project selection model, so
            # use the whole chosen polygon layer.
            return self._polygon_layer_geometry_3857(
                layer,
                prefer_selection=False,
            )

        raise RuntimeError(
            "Choose an AOI polygon source using 'From Project...' or 'Browse...'."
        )

    def update_aoi_source_controls(self, *args):
        if not self.dock:
            return

        polygon_mode = self.dock.selected_polygon_radio.isChecked()
        draw_mode = self.dock.draw_polygon_radio.isChecked()

        self.dock.aoi_from_project_button.setEnabled(
            polygon_mode
        )
        self.dock.aoi_browse_button.setEnabled(
            polygon_mode
        )

        source_text = ""
        source_tooltip = ""

        if self.aoi_polygon_source_type == "project":
            layer = QgsProject.instance().mapLayer(
                self.aoi_polygon_project_layer_id
            )
            if layer is not None:
                source_text = "AOI source: " + layer.name()
                source_tooltip = (
                    "Project polygon layer: "
                    + layer.name()
                    + "\\n"
                    + (layer.source() or "")
                    + "\\nSelected features are used when present; otherwise "
                    "the whole polygon layer is used."
                )
            else:
                source_text = "AOI source: project layer unavailable"
                source_tooltip = (
                    "The previously selected project polygon layer is no longer loaded."
                )

        elif self.aoi_polygon_source_type == "file":
            display = self.aoi_polygon_file_display or self.aoi_polygon_file_uri
            short_display = os.path.basename(
                display.split(" — ", 1)[0]
            )
            if " — " in display:
                short_display += " — " + display.split(" — ", 1)[1]
            source_text = "AOI source: " + short_display
            source_tooltip = display

        self.dock.aoi_polygon_source_label.setText(
            source_text
        )
        self.dock.aoi_polygon_source_label.setToolTip(
            source_tooltip
        )
        self.dock.aoi_polygon_source_label.setVisible(
            polygon_mode and bool(source_text)
        )

        # Draw guidance is intentionally dynamic in V1. It is visible while
        # drawing or when Draw mode has no AOI, then disappears after capture.
        if not draw_mode:
            self.dock.draw_status_label.hide()
        elif self.drawn_aoi_3857 is None:
            if not self.dock.draw_status_label.text().strip():
                self.dock.draw_status_label.setText(
                    "No drawn AOI."
                )
            self.dock.draw_status_label.show()
        elif self.draw_tool is not None and self.iface.mapCanvas().mapTool() is self.draw_tool:
            self.dock.draw_status_label.show()
        else:
            self.dock.draw_status_label.hide()


    # --------------------------------------------------------
    # V1 BUFFER UNITS
    # --------------------------------------------------------
    def _buffer_unit_key(self):
        if not self.dock:
            return "m"
        return self.dock.buffer_unit_combo.currentData() or "m"

    def _buffer_distance_meters(self):
        unit = self._buffer_unit_key()
        factor = BUFFER_UNIT_FACTORS.get(
            unit,
            1.0,
        )
        return float(
            self.dock.buffer_spin.value()
        ) * factor

    def _configure_buffer_spin_for_unit(self, unit):
        if not self.dock:
            return

        if unit == "m":
            decimals, step, maximum = 1, 10.0, 1000000.0
        elif unit == "km":
            decimals, step, maximum = 3, 0.1, 1000.0
        elif unit == "ft":
            decimals, step, maximum = 1, 25.0, 3280840.0
        else:  # miles
            decimals, step, maximum = 3, 0.1, 621.371

        self.dock.buffer_spin.setDecimals(
            decimals
        )
        self.dock.buffer_spin.setSingleStep(
            step
        )
        self.dock.buffer_spin.setMaximum(
            maximum
        )

    def _set_buffer_from_meters(
        self,
        distance_m,
        unit=None,
    ):
        if not self.dock:
            return

        if unit is None:
            unit = self._buffer_unit_key()

        factor = BUFFER_UNIT_FACTORS.get(
            unit,
            1.0,
        )

        index = self.dock.buffer_unit_combo.findData(
            unit
        )

        self.dock.buffer_unit_combo.blockSignals(
            True
        )
        if index >= 0:
            self.dock.buffer_unit_combo.setCurrentIndex(
                index
            )
        self.dock.buffer_unit_combo.blockSignals(
            False
        )

        self._last_buffer_unit = unit
        self._configure_buffer_spin_for_unit(
            unit
        )

        self.dock.buffer_spin.blockSignals(
            True
        )
        self.dock.buffer_spin.setValue(
            float(distance_m) / factor
            if factor
            else float(distance_m)
        )
        self.dock.buffer_spin.blockSignals(
            False
        )

    def buffer_value_changed(self, *args):
        self.update_aoi_preview()
        self.refresh_export_status()

    def buffer_unit_changed(self, *args):
        if not self.dock:
            return

        new_unit = self._buffer_unit_key()
        old_unit = getattr(
            self,
            "_last_buffer_unit",
            "m",
        )

        old_factor = BUFFER_UNIT_FACTORS.get(
            old_unit,
            1.0,
        )
        distance_m = float(
            self.dock.buffer_spin.value()
        ) * old_factor

        self._last_buffer_unit = new_unit
        self._configure_buffer_spin_for_unit(
            new_unit
        )

        new_factor = BUFFER_UNIT_FACTORS.get(
            new_unit,
            1.0,
        )

        self.dock.buffer_spin.blockSignals(
            True
        )
        self.dock.buffer_spin.setValue(
            distance_m / new_factor
            if new_factor
            else distance_m
        )
        self.dock.buffer_spin.blockSignals(
            False
        )

        self.update_aoi_preview()
        self.refresh_export_status()

        if not self._restoring_project_settings:
            self.save_project_settings()

    # --------------------------------------------------------
    # DRAWN AOI / PERSISTENT PREVIEW
    # --------------------------------------------------------
    def _ensure_aoi_preview_bands(self):
        canvas = self.iface.mapCanvas()

        if self.aoi_preview_band is None:
            self.aoi_preview_band = QgsRubberBand(
                canvas,
                Qgis.GeometryType.Polygon,
            )
            self.aoi_preview_band.setStrokeColor(
                QColor(0, 110, 220, 255)
            )
            self.aoi_preview_band.setFillColor(
                QColor(0, 110, 220, 12)
            )
            self.aoi_preview_band.setWidth(2.5)
            self.aoi_preview_band.setLineStyle(
                Qt.PenStyle.SolidLine
            )
            self.aoi_preview_band.hide()

        if self.buffer_preview_band is None:
            self.buffer_preview_band = QgsRubberBand(
                canvas,
                Qgis.GeometryType.Polygon,
            )
            self.buffer_preview_band.setStrokeColor(
                QColor(200, 45, 145, 255)
            )
            self.buffer_preview_band.setFillColor(
                QColor(200, 45, 145, 0)
            )
            self.buffer_preview_band.setWidth(2.0)
            self.buffer_preview_band.setLineStyle(
                Qt.PenStyle.DashLine
            )
            self.buffer_preview_band.hide()

    def _buffer_geometry_meters(
        self,
        geometry_3857,
        distance_m,
    ):
        """Buffer a Web-Mercator geometry in a local UTM CRS.

        EPSG:3857 map units are nominal metres but are scale-distorted.
        For project buffers we pick the local WGS84 UTM zone from the
        AOI centroid, apply the requested metre buffer there, then
        transform back to EPSG:3857 for the Ontario service.
        """
        if distance_m <= 0:
            return QgsGeometry(geometry_3857)

        project = QgsProject.instance()
        crs_3857 = QgsCoordinateReferenceSystem("EPSG:3857")
        crs_4326 = QgsCoordinateReferenceSystem("EPSG:4326")

        centroid = geometry_3857.centroid()
        centroid_to_4326 = QgsCoordinateTransform(
            crs_3857,
            crs_4326,
            project,
        )
        centroid.transform(centroid_to_4326)

        point = centroid.asPoint()
        longitude = point.x()
        zone = int((longitude + 180.0) // 6.0) + 1
        zone = max(1, min(60, zone))

        utm_crs = QgsCoordinateReferenceSystem(
            f"EPSG:{32600 + zone}"
        )

        to_utm = QgsCoordinateTransform(
            crs_3857,
            utm_crs,
            project,
        )
        to_3857 = QgsCoordinateTransform(
            utm_crs,
            crs_3857,
            project,
        )

        buffered = QgsGeometry(geometry_3857)
        buffered.transform(to_utm)
        buffered = buffered.buffer(
            float(distance_m),
            16,
        )
        buffered.transform(to_3857)
        return buffered

    def update_aoi_preview(self, *args):
        self._ensure_aoi_preview_bands()

        if self.drawn_aoi_3857 is None:
            self.aoi_preview_band.reset(
                Qgis.GeometryType.Polygon
            )
            self.buffer_preview_band.reset(
                Qgis.GeometryType.Polygon
            )
            self.aoi_preview_band.hide()
            self.buffer_preview_band.hide()
            return

        if not self.dock.draw_polygon_radio.isChecked():
            self.aoi_preview_band.hide()
            self.buffer_preview_band.hide()
            return

        crs_3857 = QgsCoordinateReferenceSystem(
            "EPSG:3857"
        )

        self.aoi_preview_band.setToGeometry(
            QgsGeometry(self.drawn_aoi_3857),
            crs_3857,
        )
        self.aoi_preview_band.show()

        buffer_m = self._buffer_distance_meters()

        if buffer_m > 0:
            buffered = self._buffer_geometry_meters(
                self.drawn_aoi_3857,
                buffer_m,
            )
            self.buffer_preview_band.setToGeometry(
                buffered,
                crs_3857,
            )
            self.buffer_preview_band.show()
        else:
            self.buffer_preview_band.reset(
                Qgis.GeometryType.Polygon
            )
            self.buffer_preview_band.hide()

        self.iface.mapCanvas().refresh()

    def sync_aoi_preview_visibility(self, *args):
        if not self.dock:
            return

        if self.dock.draw_polygon_radio.isChecked():
            self.update_aoi_preview()
        else:
            if self.aoi_preview_band is not None:
                self.aoi_preview_band.hide()
            if self.buffer_preview_band is not None:
                self.buffer_preview_band.hide()

        self.update_aoi_source_controls()

    def clear_drawn_aoi(self):
        self.drawn_aoi_3857 = None

        if self.draw_tool is not None:
            with suppress(Exception):
                self.draw_tool.reset()

        self.update_aoi_preview()
        self.dock.draw_status_label.setText(
            "No drawn AOI."
        )
        self.dock.status_label.setText(
            "Ready"
        )
        self.update_aoi_source_controls()
        self.save_project_settings()
        self.refresh_export_status()

    def start_draw_aoi(self):
        self.dock.draw_polygon_radio.setChecked(
            True
        )

        canvas = self.iface.mapCanvas()

        self.draw_tool = PolygonAOITool(
            canvas
        )
        self.draw_tool.polygonFinished.connect(
            self._draw_aoi_finished
        )
        self.draw_tool.captureCanceled.connect(
            self._draw_aoi_canceled
        )

        canvas.setMapTool(
            self.draw_tool
        )

        if self.drawn_aoi_3857 is not None:
            self.dock.draw_status_label.setText(
                "Redrawing: existing AOI remains visible until the new polygon is finished. "
                "Left-click vertices; right-click to finish; Esc to cancel."
            )
        else:
            self.dock.draw_status_label.setText(
                "Drawing: left-click vertices; right-click to finish; Esc to cancel."
            )

        self.dock.draw_status_label.show()
        self.dock.status_label.setText(
            "Draw the AOI polygon on the map."
        )

    def _draw_aoi_finished(
        self,
        geometry,
        source_crs,
    ):
        try:
            service_crs = QgsCoordinateReferenceSystem(
                "EPSG:3857"
            )
            transformed = QgsGeometry(
                geometry
            )

            if source_crs != service_crs:
                transform = QgsCoordinateTransform(
                    source_crs,
                    service_crs,
                    QgsProject.instance(),
                )
                transformed.transform(
                    transform
                )

            if (
                transformed.isNull()
                or transformed.isEmpty()
            ):
                raise RuntimeError(
                    "The drawn AOI is empty."
                )

            self.drawn_aoi_3857 = transformed
            self.update_aoi_preview()

            self.dock.status_label.setText(
                "Drawn AOI ready."
            )
            self.save_project_settings()
            self.refresh_export_status()
            self.update_aoi_source_controls()

        except Exception as exc:
            QMessageBox.critical(
                self.iface.mainWindow(),
                PLUGIN_NAME,
                str(exc),
            )

        finally:
            self.iface.actionPan().trigger()
            self.update_aoi_source_controls()

    def _draw_aoi_canceled(self):
        if self.drawn_aoi_3857 is not None:
            self.dock.draw_status_label.setText(
                "Redraw canceled; existing AOI retained."
            )
            self.update_aoi_preview()
        else:
            self.dock.draw_status_label.setText(
                "No drawn AOI."
            )

        self.dock.status_label.setText(
            "Ready"
        )
        self.iface.actionPan().trigger()
        self.update_aoi_source_controls()

    # --------------------------------------------------------
    # PROJECT / OUTPUT MANAGEMENT
    # --------------------------------------------------------
    def _project_key(self, name):
        return str(name)

    def _write_project_entry(self, name, value):
        """Persist a string in the QGIS project using QgsProject entries.

        QgsProject in QGIS 3.44 exposes readEntry/writeEntry rather than
        QObject-style customProperty/customProperty methods.
        """
        QgsProject.instance().writeEntry(
            PROJECT_PROPERTY_PREFIX,
            self._project_key(name),
            str(value if value is not None else ""),
        )

    def _read_project_entry(self, name, default=""):
        result = QgsProject.instance().readEntry(
            PROJECT_PROPERTY_PREFIX,
            self._project_key(name),
            str(default),
        )
        # SIP bindings return (value, ok) for methods with an output bool.
        if isinstance(result, tuple):
            return result[0]
        return result

    def _sanitize_terrain_name(self, value):
        value = str(value or "").strip()
        fallback = f"Ontario_{self._dataset_short_name()}_Project_Terrain"
        if not value:
            return fallback

        invalid = '<>:"/\\|?*'
        for char in invalid:
            value = value.replace(char, "_")

        value = value.rstrip(" .")
        return value or fallback

    def _qgis_project_basename(self):
        project_path = QgsProject.instance().fileName()
        if project_path:
            return os.path.splitext(
                os.path.basename(project_path)
            )[0]
        return "Ontario_Elevation_Project"

    def _default_terrain_name(self):
        base = self._sanitize_terrain_name(
            self._qgis_project_basename()
        )
        suffix = "_" + self._dataset_short_name()
        if base.lower().endswith(suffix.lower()):
            return base
        # If a fallback/default from another dataset was passed through the
        # sanitizer, strip that suffix before applying the active dataset.
        for other in ("_DTM", "_DSM"):
            if base.lower().endswith(other.lower()):
                base = base[:-len(other)]
                break
        return base + suffix

    def _project_folder(self):
        project_path = QgsProject.instance().fileName()
        if project_path:
            return os.path.dirname(project_path)
        return ""

    def _global_cache_folder(self):
        return self.settings.value(
            "OntarioDTMManager/cacheFolder",
            "",
            type=str,
        ) or ""

    def _effective_cache_folder(self):
        return self._dataset_cache_folder(
            self._base_cache_folder()
        )

    def _effective_output_folder(self):
        if self.dock.project_output_checkbox.isChecked():
            return self._project_folder()
        return self.custom_output_folder

    def refresh_project_label(self):
        if not self.dock:
            return

        project_path = QgsProject.instance().fileName()
        if project_path:
            self.dock.project_label.setText(
                "QGIS project: " + os.path.basename(project_path)
            )
            self.dock.project_label.setToolTip(
                os.path.normpath(project_path)
            )
        else:
            self.dock.project_label.setText(
                "QGIS project: Unsaved project"
            )
            self.dock.project_label.setToolTip(
                "Save the QGIS project or switch Output Folder to custom mode."
            )

    def _set_default_vrt_if_blank(self):
        if not self.dock:
            return

        self.refresh_project_label()

        if not self.dock.terrain_name_edit.text().strip():
            self.dock.terrain_name_edit.setText(
                self._default_terrain_name()
            )

        self.refresh_cache_controls()
        self.refresh_output_controls()
        self.update_vrt_path()

    def refresh_cache_controls(self):
        if not self.dock:
            return

        if self.dock.global_cache_checkbox.isChecked():
            path = self._global_cache_folder()
            tooltip = (
                "Global/shared cache. This path is remembered in QGIS and "
                "can be reused by all projects."
            )
        else:
            path = self.custom_cache_folder
            tooltip = (
                "Custom cache for this QGIS project. This path is saved "
                "inside the project file."
            )

        self.dock.cache_edit.setText(
            os.path.normpath(path) if path else ""
        )
        active_path = self._dataset_cache_folder(path) if path else ""
        if active_path:
            tooltip += (
                "\nActive " + self._dataset_short_name() + " cache: "
                + os.path.normpath(active_path)
            )
        self.dock.cache_edit.setToolTip(tooltip)
        if hasattr(self.dock, "active_cache_path_label"):
            self.dock.active_cache_path_label.setText(
                "Active dataset cache: "
                + (os.path.normpath(active_path) if active_path else "--")
            )

    def refresh_output_controls(self):
        if not self.dock:
            return

        automatic = self.dock.project_output_checkbox.isChecked()

        if automatic:
            path = self._project_folder()
            self.dock.output_edit.setReadOnly(True)
            self.dock.output_browse.setEnabled(False)
            if path:
                tooltip = "Automatic output: current QGIS project folder."
            else:
                tooltip = (
                    "The QGIS project is unsaved. Save it or uncheck the "
                    "automatic output option and choose a custom folder."
                )
        else:
            path = self.custom_output_folder
            self.dock.output_edit.setReadOnly(False)
            self.dock.output_browse.setEnabled(True)
            tooltip = "Custom terrain output folder saved with this QGIS project."

        self.dock.output_edit.setText(
            os.path.normpath(path) if path else ""
        )
        self.dock.output_edit.setToolTip(tooltip)
        self.update_vrt_path()

    def update_vrt_path(self):
        if not self.dock:
            return

        terrain_name = self._sanitize_terrain_name(
            self.dock.terrain_name_edit.text()
        )
        output_folder = self.dock.output_edit.text().strip()

        if output_folder:
            vrt_path = os.path.join(
                output_folder,
                terrain_name + ".vrt",
            )
            self.dock.vrt_edit.setText(
                os.path.normpath(vrt_path)
            )
        else:
            self.dock.vrt_edit.clear()

        self.refresh_terrain_lifecycle()
        self.update_export_defaults()

    def use_project_defaults(self):
        self.dock.terrain_name_edit.setText(
            self._default_terrain_name()
        )
        self.dock.project_output_checkbox.setChecked(True)
        self.refresh_output_controls()
        self.update_export_defaults(force_name=True)
        self.save_project_settings()
        self.dock.status_label.setText(
            "Terrain name/output reset from the current QGIS project."
        )

    def project_output_changed(self):
        if self._restoring_project_settings:
            return

        clean_name = self._sanitize_terrain_name(
            self.dock.terrain_name_edit.text()
        )
        self.dock.terrain_name_edit.setText(clean_name)

        if not self.dock.project_output_checkbox.isChecked():
            output_folder = self.dock.output_edit.text().strip()
            self.custom_output_folder = (
                os.path.normpath(output_folder)
                if output_folder
                else ""
            )
            self.dock.output_edit.setText(
                self.custom_output_folder
            )

        self.update_vrt_path()
        self.save_project_settings()

    def output_mode_changed(self, *args):
        if not self.dock or self._restoring_project_settings:
            return

        # If the user leaves custom mode, retain the current custom path
        # so switching back does not lose it.
        if (
            self.dock.project_output_checkbox.isChecked()
            and not self.dock.output_edit.isReadOnly()
        ):
            current = self.dock.output_edit.text().strip()
            if current:
                self.custom_output_folder = os.path.normpath(current)

        self.refresh_output_controls()
        self.save_project_settings()

    def cache_mode_changed(self, *args):
        if not self.dock or self._restoring_project_settings:
            return

        # Preserve whichever editable path was visible before switching.
        current = self.dock.cache_edit.text().strip()
        if current:
            if self.dock.global_cache_checkbox.isChecked():
                # We have just switched TO global mode, so the prior text
                # belonged to the custom mode.
                self.custom_cache_folder = os.path.normpath(current)
            else:
                # We have just switched TO custom mode, so the prior text
                # belonged to the global mode.
                self.settings.setValue(
                    "OntarioDTMManager/cacheFolder",
                    os.path.normpath(current),
                )

        self.refresh_cache_controls()
        self.refresh_cache_status()
        self.update_counts()
        self.refresh_cache_manager()
        self.save_project_settings()

    def cache_path_changed(self):
        if self._restoring_project_settings:
            return

        cache_root = self.dock.cache_edit.text().strip()
        cache_root = (
            os.path.normpath(cache_root)
            if cache_root
            else ""
        )
        self.dock.cache_edit.setText(cache_root)

        if self.dock.global_cache_checkbox.isChecked():
            self.settings.setValue(
                "OntarioDTMManager/cacheFolder",
                cache_root,
            )
        else:
            self.custom_cache_folder = cache_root

        self.refresh_cache_status()
        self.update_counts()
        self.refresh_cache_manager()
        self.save_project_settings()

    def browse_cache(self):
        start = self.dock.cache_edit.text().strip()
        if not start:
            start = self._project_folder()

        title = (
            "Select Global/Shared Ontario Elevation Cache Root"
            if self.dock.global_cache_checkbox.isChecked()
            else "Select Custom Ontario Elevation Cache Root for This Project"
        )

        folder = QFileDialog.getExistingDirectory(
            self.iface.mainWindow(),
            title,
            start,
        )

        if folder:
            self.dock.cache_edit.setText(
                os.path.normpath(folder)
            )
            self.cache_path_changed()

    def browse_output_folder(self):
        if self.dock.project_output_checkbox.isChecked():
            return

        start = self.dock.output_edit.text().strip()
        if not start:
            start = self._project_folder()

        folder = QFileDialog.getExistingDirectory(
            self.iface.mainWindow(),
            "Select Custom Project Terrain Output Folder",
            start,
        )

        if folder:
            self.custom_output_folder = os.path.normpath(folder)
            self.dock.output_edit.setText(
                self.custom_output_folder
            )
            self.update_vrt_path()
            self.save_project_settings()

    def save_project_settings(self, *args):
        if (
            not self.dock
            or self._restoring_project_settings
        ):
            return

        current_terrain_name = self._sanitize_terrain_name(
            self.dock.terrain_name_edit.text()
        )
        self._write_project_entry(
            "terrainName",
            current_terrain_name,
        )
        self._write_project_entry(
            f"terrainName_{self._selected_dataset_key()}",
            current_terrain_name,
        )
        self.dataset_terrain_names[
            self._selected_dataset_key()
        ] = current_terrain_name
        self._write_project_entry(
            "rasterDataset",
            self.dock.raster_dataset_combo.currentData() or "lidar_dtm",
        )
        self._write_project_entry(
            "outputMode",
            "project"
            if self.dock.project_output_checkbox.isChecked()
            else "custom",
        )
        self._write_project_entry(
            "customOutputFolder",
            self.custom_output_folder,
        )
        self._write_project_entry(
            "cacheMode",
            "global"
            if self.dock.global_cache_checkbox.isChecked()
            else "custom",
        )
        self._write_project_entry(
            "customCacheFolder",
            self.custom_cache_folder,
        )
        self._write_project_entry(
            "bufferM",
            self._buffer_distance_meters(),
        )
        self._write_project_entry(
            "bufferUnit",
            self._buffer_unit_key(),
        )
        aoi_mode = (
            "polygon"
            if self.dock.selected_polygon_radio.isChecked()
            else "draw"
            if self.dock.draw_polygon_radio.isChecked()
            else "extent"
        )
        self._write_project_entry(
            "aoiMode",
            aoi_mode,
        )
        self._write_project_entry(
            "aoiPolygonSourceType",
            self.aoi_polygon_source_type,
        )
        self._write_project_entry(
            "aoiProjectLayerId",
            self.aoi_polygon_project_layer_id,
        )
        self._write_project_entry(
            "aoiFileUri",
            self.aoi_polygon_file_uri,
        )
        self._write_project_entry(
            "aoiFileDisplay",
            self.aoi_polygon_file_display,
        )
        self._write_project_entry(
            "backupVRT",
            "1" if self.dock.backup_vrt_checkbox.isChecked() else "0",
        )
        self._write_project_entry(
            "drawnAOI3857Wkt",
            self.drawn_aoi_3857.asWkt()
            if self.drawn_aoi_3857 is not None
            else "",
        )
        self._write_project_entry(
            "exportName",
            self._sanitize_terrain_name(
                self.dock.export_name_edit.text()
            ),
        )
        self._write_project_entry(
            "exportClip",
            self.dock.export_clip_combo.currentData() or "buffer",
        )
        self._write_project_entry(
            "exportClipProjectLayerId",
            self.dock.export_project_polygon_combo.currentData() or "",
        )
        self._write_project_entry(
            "exportClipFileUri",
            self.export_clip_file_uri,
        )
        self._write_project_entry(
            "exportClipFileDisplay",
            self.export_clip_file_display,
        )
        self._write_project_entry(
            "exportFormat",
            self.dock.export_format_combo.currentData() or "GTiff",
        )
        self._write_project_entry(
            "exportCRS",
            self.dock.export_crs_combo.currentData() or "native",
        )
        resolution_value = self.dock.export_resolution_combo.currentData()
        self._write_project_entry(
            "exportResolution",
            "native" if resolution_value is None else resolution_value,
        )
        self._write_project_entry(
            "addExportToProject",
            "1" if self.dock.add_export_to_project_checkbox.isChecked() else "0",
        )

    def restore_project_settings(self):
        if not self.dock:
            return

        self._restoring_project_settings = True

        try:
            self.refresh_project_label()

            terrain_name = self._read_project_entry(
                "terrainName",
                "",
            )
            raster_dataset = self._read_project_entry(
                "rasterDataset",
                "lidar_dtm",
            )
            terrain_name = self._read_project_entry(
                f"terrainName_{raster_dataset}",
                terrain_name,
            )
            output_mode = self._read_project_entry(
                "outputMode",
                "project",
            )
            self.custom_output_folder = self._read_project_entry(
                "customOutputFolder",
                "",
            ) or ""
            cache_mode = self._read_project_entry(
                "cacheMode",
                "global",
            )
            self.custom_cache_folder = self._read_project_entry(
                "customCacheFolder",
                "",
            ) or ""
            buffer_m = self._read_project_entry(
                "bufferM",
                "0",
            )
            buffer_unit = self._read_project_entry(
                "bufferUnit",
                "m",
            ) or "m"
            aoi_mode = self._read_project_entry(
                "aoiMode",
                "",
            )
            aoi_polygon_source_type = self._read_project_entry(
                "aoiPolygonSourceType",
                "",
            )
            aoi_project_layer_id = self._read_project_entry(
                "aoiProjectLayerId",
                "",
            )
            aoi_file_uri = self._read_project_entry(
                "aoiFileUri",
                "",
            )
            aoi_file_display = self._read_project_entry(
                "aoiFileDisplay",
                "",
            )
            backup_vrt = self._read_project_entry(
                "backupVRT",
                "1",
            )
            aoi_wkt = self._read_project_entry(
                "drawnAOI3857Wkt",
                "",
            )
            export_name = self._read_project_entry(
                "exportName",
                "",
            )
            export_clip = self._read_project_entry(
                "exportClip",
                "buffer",
            )
            export_clip_project_layer_id = self._read_project_entry(
                "exportClipProjectLayerId",
                "",
            )
            export_clip_file_uri = self._read_project_entry(
                "exportClipFileUri",
                "",
            )
            export_clip_file_display = self._read_project_entry(
                "exportClipFileDisplay",
                "",
            )
            export_format = self._read_project_entry(
                "exportFormat",
                "GTiff",
            )
            export_crs = self._read_project_entry(
                "exportCRS",
                "native",
            )
            export_resolution = self._read_project_entry(
                "exportResolution",
                "native",
            )
            add_export = self._read_project_entry(
                "addExportToProject",
                "1",
            )

            self.dock.terrain_name_edit.setText(
                self._sanitize_terrain_name(
                    terrain_name
                    if terrain_name
                    else self._default_terrain_name()
                )
            )

            dataset_index = self.dock.raster_dataset_combo.findData(
                raster_dataset
            )
            if dataset_index >= 0:
                self.dock.raster_dataset_combo.setCurrentIndex(dataset_index)

            self.dock.project_output_checkbox.setChecked(
                str(output_mode).lower() != "custom"
            )
            self.dock.global_cache_checkbox.setChecked(
                str(cache_mode).lower() != "custom"
            )

            try:
                restored_buffer_m = float(buffer_m)
            except Exception:
                restored_buffer_m = 0.0

            if buffer_unit not in BUFFER_UNIT_FACTORS:
                buffer_unit = "m"

            self._set_buffer_from_meters(
                restored_buffer_m,
                buffer_unit,
            )

            self.dock.backup_vrt_checkbox.setChecked(
                str(backup_vrt).strip().lower() not in ("0", "false", "no")
            )

            self.aoi_polygon_source_type = str(
                aoi_polygon_source_type or ""
            )
            self.aoi_polygon_project_layer_id = str(
                aoi_project_layer_id or ""
            )
            self.aoi_polygon_file_uri = str(
                aoi_file_uri or ""
            )
            self.aoi_polygon_file_display = str(
                aoi_file_display or ""
            )

            if aoi_wkt:
                restored = QgsGeometry.fromWkt(
                    str(aoi_wkt)
                )
                if (
                    not restored.isNull()
                    and not restored.isEmpty()
                ):
                    self.drawn_aoi_3857 = restored
                else:
                    self.drawn_aoi_3857 = None
            else:
                self.drawn_aoi_3857 = None

            if aoi_mode == "polygon" and self.aoi_polygon_source_type in ("project", "file"):
                self.dock.selected_polygon_radio.setChecked(True)
            elif aoi_mode == "draw" and self.drawn_aoi_3857 is not None:
                self.dock.draw_polygon_radio.setChecked(True)
            elif not aoi_mode and self.drawn_aoi_3857 is not None:
                # Backward compatibility with v0.x projects which only stored
                # the drawn AOI WKT.
                self.dock.draw_polygon_radio.setChecked(True)
            else:
                self.dock.current_extent_radio.setChecked(True)

            default_export_name = (
                self._sanitize_terrain_name(export_name)
                if export_name
                else self._sanitize_terrain_name(
                    self.dock.terrain_name_edit.text() + "_Clipped"
                )
            )
            self.dock.export_name_edit.setText(default_export_name)

            for combo, value in (
                (self.dock.export_clip_combo, export_clip),
                (self.dock.export_format_combo, export_format),
                (self.dock.export_crs_combo, export_crs),
            ):
                index = combo.findData(value)
                if index >= 0:
                    combo.setCurrentIndex(index)

            self.export_clip_file_uri = str(export_clip_file_uri or "")
            self.export_clip_file_display = str(export_clip_file_display or "")
            self.dock.export_file_polygon_edit.setText(
                self.export_clip_file_display
            )

            if export_clip_project_layer_id:
                project_clip_index = self.dock.export_project_polygon_combo.findData(
                    export_clip_project_layer_id
                )
                if project_clip_index >= 0:
                    self.dock.export_project_polygon_combo.setCurrentIndex(
                        project_clip_index
                    )

            if str(export_resolution).lower() == "native":
                self.dock.export_resolution_combo.setCurrentIndex(0)
            else:
                try:
                    resolution_float = float(export_resolution)
                except Exception:
                    resolution_float = None
                index = self.dock.export_resolution_combo.findData(resolution_float)
                self.dock.export_resolution_combo.setCurrentIndex(
                    index if index >= 0 else 0
                )

            self.dock.add_export_to_project_checkbox.setChecked(
                str(add_export).strip().lower() not in ("0", "false", "no")
            )

            self.refresh_cache_controls()
            self.refresh_output_controls()
            self.update_vrt_path()
            self.update_export_defaults(force_name=not bool(export_name))

        finally:
            self._restoring_project_settings = False

        self.raster_dataset_changed()
        self.update_export_clip_controls()
        self.update_aoi_preview()
        self.update_aoi_source_controls()

    def _terrain_layer_name(self):
        if not self.dock:
            return TERRAIN_LAYER_NAME
        return self._sanitize_terrain_name(
            self.dock.terrain_name_edit.text()
        )

    # AOI RESOLUTION
    # --------------------------------------------------------
    def _raw_aoi_geometry_3857(self):
        service_crs = QgsCoordinateReferenceSystem(
            "EPSG:3857"
        )
        project = QgsProject.instance()

        if self.dock.current_extent_radio.isChecked():
            canvas = self.iface.mapCanvas()
            source_crs = canvas.mapSettings().destinationCrs()

            geometry = QgsGeometry.fromRect(
                canvas.extent()
            )

            if source_crs != service_crs:
                transform = QgsCoordinateTransform(
                    source_crs,
                    service_crs,
                    project,
                )
                geometry.transform(
                    transform
                )

        elif self.dock.selected_polygon_radio.isChecked():
            geometry = self._aoi_polygon_source_geometry()

        else:
            if self.drawn_aoi_3857 is None:
                raise RuntimeError(
                    "Click 'Draw / Redraw...' and capture an AOI polygon first."
                )

            geometry = QgsGeometry(
                self.drawn_aoi_3857
            )

        if (
            geometry.isNull()
            or geometry.isEmpty()
        ):
            raise RuntimeError(
                "AOI geometry is empty."
            )

        return geometry

    def _aoi_geometry_3857(self):
        geometry = self._raw_aoi_geometry_3857()
        buffer_m = self._buffer_distance_meters()

        if buffer_m > 0:
            geometry = self._buffer_geometry_meters(
                geometry,
                buffer_m,
            )

        return geometry

    # --------------------------------------------------------
    # TILE QUERY
    # --------------------------------------------------------
    def find_tiles(self):
        try:
            dataset = self._selected_raster_dataset()
            short_name = dataset["short_name"]
            self.dock.status_label.setText(
                f"Querying Ontario {short_name} tile index..."
            )
            self.dock.find_tiles_button.setEnabled(False)
            QApplication.processEvents()

            aoi_geometry = self._aoi_geometry_3857()
            query_rect = aoi_geometry.boundingBox()

            if dataset.get("tile_index_mode") == "feature_service":
                records, exceeded_limit = query_tiles_by_envelope(
                    query_rect,
                    self._selected_dataset_key(),
                )
            else:
                records, exceeded_limit = self._query_local_dataset_tiles(
                    aoi_geometry
                )

            if exceeded_limit:
                raise RuntimeError(
                    "Ontario's feature-service transfer limit was reached. "
                    "Use a smaller AOI."
                )

            self._create_tile_layer(
                records,
                aoi_geometry,
            )

            count = self.tile_layer.featureCount() if self.tile_layer else 0

            if count and self.dock.auto_select_checkbox.isChecked():
                self.tile_layer.selectAll()

            self.refresh_cache_status()
            self.update_counts()

            self.dock.status_label.setText(
                f"Found {count} intersecting Ontario {short_name} tile(s)."
            )

            if dataset.get("tile_index_mode") == "zip_shapefile":
                index_path = self._local_dataset_index_path()
                if index_path:
                    self.dock.dataset_index_status.setText(
                        "Index: Cached"
                    )
                    self.dock.dataset_index_status.setToolTip(
                        "Official Ontario tile index cached at:\n"
                        + os.path.normpath(index_path)
                    )

            if count:
                self.dock.selection_section.set_expanded(True)
                self.iface.setActiveLayer(self.tile_layer)
                self.zoom_to_tiles()

        except Exception as exc:
            self.dock.status_label.setText("Tile query failed.")
            QMessageBox.critical(
                self.iface.mainWindow(),
                PLUGIN_NAME,
                str(exc),
            )

        finally:
            self.dock.find_tiles_button.setEnabled(True)

    def _create_tile_layer(
        self,
        records,
        aoi_geometry,
    ):
        self._remove_all_available_tile_layers()
        tile_layer_name = self._dataset_tile_layer_name()

        layer = QgsVectorLayer(
            "Polygon?crs=EPSG:3857",
            tile_layer_name,
            "memory",
        )
        provider = layer.dataProvider()

        provider.addAttributes(
            [
                QgsField(
                    "TileName",
                    QVariant.String,
                ),
                QgsField(
                    "FileName",
                    QVariant.String,
                ),
                QgsField(
                    "Project",
                    QVariant.String,
                ),
                QgsField(
                    "Package",
                    QVariant.String,
                ),
                QgsField(
                    "Cached",
                    QVariant.Int,
                ),
            ]
        )
        layer.updateFields()

        features = []

        for item in records:
            attributes = item.get(
                "attributes",
                {},
            )
            if item.get("qgs_geometry") is not None:
                geometry = QgsGeometry(item["qgs_geometry"])
            else:
                rings = item.get(
                    "geometry",
                    {},
                ).get(
                    "rings",
                    [],
                )

                if not rings:
                    continue

                points = [
                    QgsPointXY(
                        float(x),
                        float(y),
                    )
                    for x, y in rings[0]
                ]

                geometry = QgsGeometry.fromPolygonXY(
                    [points]
                )

            if not geometry.intersects(
                aoi_geometry
            ):
                continue

            feature = QgsFeature(
                layer.fields()
            )
            feature.setGeometry(
                geometry
            )
            feature.setAttributes(
                [
                    attributes.get(
                        "TileName"
                    ),
                    attributes.get(
                        "FileName"
                    ),
                    attributes.get(
                        "Project"
                    ),
                    attributes.get(
                        "Package"
                    ),
                    0,
                ]
            )
            features.append(
                feature
            )

        provider.addFeatures(
            features
        )
        layer.updateExtents()

        self._apply_tile_renderer(
            layer
        )

        label_settings = QgsPalLayerSettings()
        label_settings.fieldName = "TileName"
        label_settings.enabled = True

        text_format = QgsTextFormat()
        text_format.setSize(
            7
        )
        text_format.setColor(
            QColor("black")
        )

        buffer_settings = QgsTextBufferSettings()
        buffer_settings.setEnabled(
            True
        )
        buffer_settings.setSize(
            1
        )
        buffer_settings.setColor(
            QColor("white")
        )

        text_format.setBuffer(
            buffer_settings
        )
        label_settings.setFormat(
            text_format
        )

        layer.setLabeling(
            QgsVectorLayerSimpleLabeling(
                label_settings
            )
        )
        layer.setLabelsEnabled(
            self.dock.labels_checkbox.isChecked()
        )

        QgsProject.instance().addMapLayer(
            layer
        )
        layer.selectionChanged.connect(
            self.update_counts
        )

        self.tile_layer = layer

    def _apply_tile_renderer(self, layer):
        cached_symbol = QgsFillSymbol.createSimple(
            {
                "color": "70,170,90,45",
                "outline_color": "30,130,60",
                "outline_width": "0.7",
            }
        )

        missing_symbol = QgsFillSymbol.createSimple(
            {
                "color": "255,165,0,35",
                "outline_color": "255,120,0",
                "outline_width": "0.7",
            }
        )

        categories = [
            QgsRendererCategory(
                1,
                cached_symbol,
                "Cached",
            ),
            QgsRendererCategory(
                0,
                missing_symbol,
                "Needs download",
            ),
        ]

        renderer = QgsCategorizedSymbolRenderer(
            "Cached",
            categories,
        )
        layer.setRenderer(
            renderer
        )

    def toggle_tile_labels(self, checked):
        layer = self._current_tile_layer()

        if layer:
            layer.setLabelsEnabled(
                bool(checked)
            )
            layer.triggerRepaint()

    # --------------------------------------------------------
    # TERRAIN EXPORT / DELIVERY
    # --------------------------------------------------------
    def _export_output_path(self):
        if not self.dock:
            return ""

        output_folder = self.dock.output_edit.text().strip()
        export_name = self._sanitize_terrain_name(
            self.dock.export_name_edit.text()
        )

        if not output_folder or not export_name:
            return ""

        return os.path.normpath(
            os.path.join(
                output_folder,
                export_name + ".tif",
            )
        )

    def update_export_defaults(self, force_name=False):
        if not self.dock:
            return

        current_name = self.dock.export_name_edit.text().strip()
        clip_mode = self.dock.export_clip_combo.currentData() or "buffer"

        if force_name or not current_name:
            suffix = "_Export" if clip_mode == "none" else "_Clipped"
            default_name = self._sanitize_terrain_name(
                self._terrain_layer_name() + suffix
            )
            self.dock.export_name_edit.setText(default_name)
        else:
            self.dock.export_name_edit.setText(
                self._sanitize_terrain_name(current_name)
            )

        output_path = self._export_output_path()
        self.dock.export_path_edit.setText(output_path)
        self.dock.open_export_button.setEnabled(
            bool(output_path and os.path.isdir(os.path.dirname(output_path)))
        )
        self.refresh_export_status()

    def export_options_changed(self, *args):
        if not self.dock or self._restoring_project_settings:
            return

        terrain_name = self._terrain_layer_name()
        current_name = self.dock.export_name_edit.text().strip()
        auto_names = {
            self._sanitize_terrain_name(terrain_name + "_Clipped"),
            self._sanitize_terrain_name(terrain_name + "_Export"),
        }
        self.update_export_defaults(
            force_name=current_name in auto_names
        )
        self.save_project_settings()

    def open_export_folder(self):
        path = self._export_output_path()
        folder = os.path.dirname(path) if path else ""

        if not folder or not os.path.isdir(folder):
            QMessageBox.information(
                self.iface.mainWindow(),
                PLUGIN_NAME,
                "The terrain export folder does not exist yet.",
            )
            return

        QDesktopServices.openUrl(
            QUrl.fromLocalFile(folder)
        )

    def _current_export_option_signature(self):
        resolution = self.dock.export_resolution_combo.currentData()
        clip_mode = self.dock.export_clip_combo.currentData() or "buffer"

        clip_signature = "none"
        if clip_mode != "none":
            try:
                geometry = self._export_clip_geometry(clip_mode)
                # A compact WKT signature makes AOI/buffer edits mark an old
                # deliverable as needing regeneration even if the VRT itself
                # has not yet changed.
                clip_signature = geometry.asWkt(5)
            except Exception:
                clip_signature = "unavailable"

        return {
            "clip_mode": clip_mode,
            "clip_signature": clip_signature,
            "buffer_m": (
                self._buffer_distance_meters()
                if clip_mode == "buffer"
                else 0
            ),
            "format": self.dock.export_format_combo.currentData() or "GTiff",
            "crs_mode": self.dock.export_crs_combo.currentData() or "native",
            "resolution": "native" if resolution is None else float(resolution),
        }

    def refresh_export_status(self, *args):
        if not self.dock:
            return

        vrt_path = self.dock.vrt_edit.text().strip()
        output_path = self._export_output_path()
        self.dock.export_path_edit.setText(output_path)

        vrt_exists = bool(
            vrt_path
            and os.path.exists(vrt_path)
        )
        self.dock.export_button.setEnabled(vrt_exists)
        self.dock.refresh_export_qa_button.setEnabled(vrt_exists)

        if not vrt_exists:
            self.dock.export_state_label.setText(
                "Export status: Working terrain VRT not found"
            )
            self.dock.export_status_label.setText(
                "Build the working terrain VRT before exporting a deliverable."
            )
            return

        current, reason = export_is_current(
            output_path,
            vrt_path,
        )

        if current:
            manifest = load_export_manifest(output_path) or {}
            saved_options = manifest.get("options", {})
            if saved_options != self._current_export_option_signature():
                current = False
                reason = "Existing export uses different export options"

        if current:
            self.dock.export_state_label.setText(
                "Export status: Current"
            )
        else:
            self.dock.export_state_label.setText(
                "Export status: " + reason
            )

        self.dock.export_status_label.setText(
            "Working VRT is ready for optional export."
        )

    def _tile_metadata_for_vrt_sources(self, source_img_paths):
        layer = self._current_tile_layer()
        metadata = {
            "projects": set(),
            "packages": set(),
            "pairs": set(),
            "unmatched": 0,
        }

        if not layer:
            metadata["unmatched"] = len(source_img_paths)
            return metadata

        lookup = {}
        for feature in layer.getFeatures():
            filename = str(feature["FileName"] or "")
            if filename:
                lookup[filename.lower()] = feature

        for path in source_img_paths:
            filename = os.path.basename(path).lower()
            feature = lookup.get(filename)
            if feature is None:
                metadata["unmatched"] += 1
                continue

            project_name = str(feature["Project"] or "")
            package_name = str(feature["Package"] or "")

            if project_name:
                metadata["projects"].add(project_name)
            if package_name:
                metadata["packages"].add(package_name)
            if project_name and package_name:
                metadata["pairs"].add(
                    (project_name, package_name)
                )

        return metadata

    def _package_metadata(self, project_name, package_name):
        dataset_key = self._selected_dataset_key()
        key = (dataset_key, project_name, package_name)
        if key not in self._package_metadata_cache:
            self._package_metadata_cache[key] = get_package_metadata(
                project_name,
                package_name,
                dataset_key,
            )
        return self._package_metadata_cache.get(key)

    def refresh_export_qa(self, *args):
        if not self.dock:
            return

        vrt_path = self.dock.vrt_edit.text().strip()
        if not vrt_path or not os.path.exists(vrt_path):
            self.dock.export_qa_label.setText(
                "Terrain QA unavailable — working VRT not found."
            )
            self.refresh_export_status()
            return

        try:
            self.dock.export_qa_label.setText(
                "Analyzing working terrain..."
            )
            QApplication.processEvents()

            info = inspect_raster(
                vrt_path,
                approximate_range=True,
            )

            temp_layer = QgsRasterLayer(
                vrt_path,
                f"Ontario {self._dataset_short_name()} QA",
                "gdal",
            )
            crs_text = "Unknown"
            if temp_layer.isValid():
                crs_text = (
                    temp_layer.crs().authid()
                    or temp_layer.crs().description()
                    or "Unknown"
                )
            temp_layer = None

            tile_metadata = self._tile_metadata_for_vrt_sources(
                info["source_imgs"]
            )

            vintages = set()
            vertical_datums = set()
            listed_resolutions = set()

            package_metadata_failures = 0
            for pair in sorted(tile_metadata["pairs"]):
                try:
                    package_info = self._package_metadata(*pair)
                except Exception:
                    package_metadata_failures += 1
                    continue
                if not package_info:
                    package_metadata_failures += 1
                    continue

                vintage = package_info.get("Vintage")
                vertical = package_info.get("VerticalDatum")
                resolution = package_info.get("Resolution")

                if vintage:
                    vintages.add(str(vintage))
                if vertical:
                    vertical_datums.add(str(vertical))
                if resolution not in (None, ""):
                    listed_resolutions.add(str(resolution))

            def joined(values):
                return "; ".join(sorted(values)) if values else "--"

            if (
                info["minimum"] is not None
                and info["maximum"] is not None
            ):
                elevation_text = (
                    f"{info['minimum']:.2f} to {info['maximum']:.2f} m (approx.)"
                )
            else:
                elevation_text = "--"

            source_note = ""
            if tile_metadata["unmatched"]:
                source_note += (
                    f"\nTile-index metadata unmatched: {tile_metadata['unmatched']} "
                    f"source tile(s) — re-run Find Available {self._dataset_short_name()} Tiles around the terrain "
                    "if full provenance is required."
                )
            if package_metadata_failures:
                source_note += (
                    f"\nOntario package metadata unavailable for "
                    f"{package_metadata_failures} package(s) during this QA refresh."
                )

            qa_text = (
                f"Working terrain tiles: {info['tile_count']}\n"
                f"Pixel size: {info['pixel_x']:g} × {info['pixel_y']:g} map units\n"
                f"CRS: {crs_text}\n"
                f"NoData: {info['nodata']}\n"
                f"Elevation range: {elevation_text}\n"
                f"LiDAR project(s): {joined(tile_metadata['projects'])}\n"
                f"Package(s): {joined(tile_metadata['packages'])}\n"
                f"Ontario listed resolution(s): {joined(listed_resolutions)}\n"
                f"Acquisition vintage(s): {joined(vintages)}\n"
                f"Vertical datum(s): {joined(vertical_datums)}"
                f"{source_note}"
            )

            self.dock.export_qa_label.setText(qa_text)
            self.refresh_export_status()

        except Exception as exc:
            self.dock.export_qa_label.setText(
                "Terrain QA failed: " + str(exc)
            )

    def _export_clip_geometry(self, mode):
        if mode == "none":
            return None
        if mode == "aoi":
            return self._raw_aoi_geometry_3857()
        if mode == "project_polygon":
            return self._project_export_clip_geometry()
        if mode == "file_polygon":
            return self._file_export_clip_geometry()
        return self._aoi_geometry_3857()

    def export_terrain(self):
        try:
            if self.export_task is not None:
                with suppress(Exception):
                    if self.export_task.status() in (
                        Qgis.TaskStatus.Queued,
                        Qgis.TaskStatus.Running,
                    ):
                        QMessageBox.information(
                            self.iface.mainWindow(),
                            PLUGIN_NAME,
                            "A terrain export is already running.",
                        )
                        return

            vrt_path = self.dock.vrt_edit.text().strip()
            if not vrt_path or not os.path.exists(vrt_path):
                raise RuntimeError(
                    "Build the working terrain VRT before exporting."
                )

            output_path = self._export_output_path()
            if not output_path:
                raise RuntimeError(
                    "No project terrain output folder is configured."
                )

            output_format = self.dock.export_format_combo.currentData() or "GTiff"
            if output_format == "COG" and gdal.GetDriverByName("COG") is None:
                raise RuntimeError(
                    "The GDAL installation used by this QGIS build does not provide the COG driver."
                )

            crs_mode = self.dock.export_crs_combo.currentData() or "native"
            dst_srs_wkt = None
            target_crs = None

            if crs_mode == "project":
                target_crs = QgsProject.instance().crs()
                if not target_crs.isValid():
                    raise RuntimeError(
                        "The current QGIS project CRS is not valid."
                    )
                dst_srs_wkt = target_crs.toWkt()
            else:
                temp_layer = QgsRasterLayer(
                    vrt_path,
                    f"Ontario {self._dataset_short_name()} Export CRS",
                    "gdal",
                )
                if not temp_layer.isValid():
                    raise RuntimeError(
                        "The working VRT could not be opened to determine its native CRS."
                    )
                target_crs = temp_layer.crs()
                temp_layer = None

            resolution = self.dock.export_resolution_combo.currentData()
            if resolution is not None:
                resolution = float(resolution)
                if target_crs is not None and target_crs.isGeographic():
                    raise RuntimeError(
                        "A metre-based output resolution cannot be applied to a geographic "
                        "latitude/longitude target CRS. Use Native resolution or a projected CRS."
                    )

            clip_mode = self.dock.export_clip_combo.currentData() or "buffer"
            clip_geometry = self._export_clip_geometry(clip_mode)
            clip_wkt = clip_geometry.asWkt() if clip_geometry is not None else None
            clip_srs_wkt = (
                QgsCoordinateReferenceSystem("EPSG:3857").toWkt()
                if clip_geometry is not None
                else None
            )

            if os.path.exists(output_path):
                answer = QMessageBox.question(
                    self.iface.mainWindow(),
                    "Replace Existing Terrain Export?",
                    (
                        "The terrain deliverable already exists:\n\n"
                        f"{output_path}\n\nReplace it?"
                    ),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return

            output_folder = os.path.dirname(output_path)
            os.makedirs(output_folder, exist_ok=True)

            info = inspect_raster(
                vrt_path,
                approximate_range=False,
            )
            target_crs_text = (
                target_crs.authid()
                or target_crs.description()
                or "Native"
            ) if target_crs is not None else "Native"

            options = self._current_export_option_signature()
            manifest_data = {
                "plugin_version": "1.0.0",
                "dataset_key": self._selected_dataset_key(),
                "dataset": self._dataset_short_name(),
                "terrain_name": self._terrain_layer_name(),
                "tile_count": info["tile_count"],
                "options": options,
                "target_crs": target_crs_text,
                "buffer_m": self._buffer_distance_meters(),
            }

            resolution_text = (
                "Native"
                if resolution is None
                else f"{resolution:g} m"
            )
            clip_text = self.dock.export_clip_combo.currentText()
            format_text = self.dock.export_format_combo.currentText()

            summary = (
                "Export terrain deliverable\n\n"
                f"Source tiles: {info['tile_count']}\n"
                f"Clip: {clip_text}\n"
                f"Format: {format_text}\n"
                f"Output CRS: {target_crs_text}\n"
                f"Resolution: {resolution_text}\n"
                "Resampling: bilinear when warping/resampling is required\n\n"
                f"Output:\n{output_path}\n\n"
                "The working VRT and cached Ontario source tiles will not be modified.\n\n"
                "Start export?"
            )

            answer = QMessageBox.question(
                self.iface.mainWindow(),
                f"Ontario {self._dataset_short_name()} Terrain Export",
                summary,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

            task = TerrainExportTask(
                source_vrt_path=vrt_path,
                output_path=output_path,
                output_format=output_format,
                dst_srs_wkt=dst_srs_wkt,
                resolution=resolution,
                clip_wkt=clip_wkt,
                clip_srs_wkt=clip_srs_wkt,
                manifest_data=manifest_data,
                completion_callback=self._export_task_finished,
            )
            task.progressChanged.connect(
                lambda value: self.dock.export_progress.setValue(
                    int(value)
                )
            )

            self.export_task = task
            self.dock.export_progress.setValue(0)
            self.dock.export_button.setEnabled(False)
            self.dock.cancel_export_button.setEnabled(True)
            self.dock.export_status_label.setText(
                "Exporting terrain in background..."
            )
            QgsApplication.taskManager().addTask(task)

        except Exception as exc:
            self.dock.export_button.setEnabled(True)
            self.dock.cancel_export_button.setEnabled(False)
            self.dock.export_status_label.setText(
                "Terrain export failed to start."
            )
            QMessageBox.critical(
                self.iface.mainWindow(),
                PLUGIN_NAME,
                str(exc),
            )

    def cancel_export_task(self):
        if self.export_task is None:
            return
        try:
            self.export_task.cancel()
            self.dock.cancel_export_button.setEnabled(False)
            self.dock.export_status_label.setText(
                "Cancel requested..."
            )
        except Exception as exc:
            QMessageBox.warning(
                self.iface.mainWindow(),
                PLUGIN_NAME,
                f"Could not cancel export:\n{exc}",
            )

    def _export_task_finished(self, task, result):
        self.dock.export_button.setEnabled(True)
        self.dock.cancel_export_button.setEnabled(False)

        if task.isCanceled():
            self.dock.export_status_label.setText(
                "Export cancelled. Existing deliverable, if any, was left unchanged."
            )
            self.export_task = None
            self.refresh_export_status()
            return

        if not result:
            self.dock.export_status_label.setText(
                "Terrain export failed. Existing deliverable, if any, was left unchanged."
            )
            QMessageBox.critical(
                self.iface.mainWindow(),
                PLUGIN_NAME,
                task.error_message or "Unknown terrain export failure.",
            )
            self.export_task = None
            self.refresh_export_status()
            return

        self.dock.export_progress.setValue(100)
        output_path = task.output_path

        try:
            # Remove any QGIS layer holding a Windows file lock on the prior
            # deliverable, then atomically replace it with the validated export.
            output_norm = os.path.normcase(os.path.abspath(output_path))
            for layer in list(QgsProject.instance().mapLayers().values()):
                if isinstance(layer, QgsRasterLayer):
                    source = layer.source()
                    if (
                        source
                        and os.path.normcase(os.path.abspath(source)) == output_norm
                    ):
                        QgsProject.instance().removeMapLayer(layer.id())

            task.commit_output()

        except Exception as exc:
            task.cleanup_after_failure()
            self.dock.export_status_label.setText(
                "New terrain export was created but could not replace the final deliverable."
            )
            QMessageBox.critical(
                self.iface.mainWindow(),
                PLUGIN_NAME,
                str(exc),
            )
            self.export_task = None
            self.refresh_export_status()
            return

        if self.dock.add_export_to_project_checkbox.isChecked():
            layer_name = self._sanitize_terrain_name(
                self.dock.export_name_edit.text()
            )

            raster_layer = QgsRasterLayer(
                output_path,
                layer_name,
                "gdal",
            )
            if raster_layer.isValid():
                root = QgsProject.instance().layerTreeRoot()
                group = root.findGroup(GROUP_NAME)
                if group is None:
                    group = root.insertGroup(0, GROUP_NAME)
                QgsProject.instance().addMapLayer(
                    raster_layer,
                    False,
                )
                group.addLayer(raster_layer)

        self.dock.export_status_label.setText(
            "Terrain deliverable exported successfully."
        )
        self.iface.messageBar().pushMessage(
            PLUGIN_NAME,
            "Terrain deliverable exported successfully.",
            level=Qgis.MessageLevel.Success,
            duration=10,
        )
        self.export_task = None
        self.refresh_export_status()

    # --------------------------------------------------------
    # CACHE / DATASET MANAGEMENT
    # --------------------------------------------------------
    def _format_bytes(self, value):
        value = float(value or 0)

        units = (
            "B",
            "KiB",
            "MiB",
            "GiB",
            "TiB",
        )

        for unit in units:
            if abs(value) < 1024.0 or unit == units[-1]:
                if unit == "B":
                    return f"{int(value)} {unit}"
                return f"{value:.2f} {unit}"
            value /= 1024.0

        return f"{value:.2f} TiB"

    def _cache_inventory(self):
        cache_root = (
            self._effective_cache_folder()
            if self.dock
            else ""
        )

        inventory = {
            "cache_root": cache_root,
            "exists": False,
            "tile_paths": [],
            "tile_names": set(),
            "tile_count": 0,
            "tile_size": 0,
            "sidecar_paths": [],
            "sidecar_count": 0,
            "sidecar_size": 0,
            "zip_paths": [],
            "zip_count": 0,
            "zip_size": 0,
            "partial_paths": [],
            "partial_count": 0,
            "partial_size": 0,
            "extracting_paths": [],
            "extracting_count": 0,
            "extracting_size": 0,
            "terrain_exists": False,
            "current_terrain_names": set(),
            "selected_names": set(),
            "protected_names": set(),
            "unused_tile_names": set(),
            "unused_tile_paths": [],
            "unused_tile_count": 0,
            "unused_tile_size": 0,
            "total_size": 0,
            "free_bytes": None,
        }

        if not cache_root:
            return inventory

        cache_root = os.path.normpath(
            cache_root
        )
        inventory["cache_root"] = cache_root
        inventory["exists"] = os.path.isdir(
            cache_root
        )

        packages_dir = os.path.join(
            cache_root,
            "packages",
        )
        tiles_dir = os.path.join(
            cache_root,
            "tiles",
        )
        raster_extensions = tuple(
            ext.lower()
            for ext in self._selected_raster_dataset().get(
                "extensions", (".img", ".tif", ".tiff")
            )
        )

        def scan_file(path):
            try:
                return os.path.getsize(
                    path
                )
            except OSError:
                return 0

        if os.path.isdir(packages_dir):
            try:
                entries = list(
                    os.scandir(packages_dir)
                )
            except OSError:
                entries = []

            for entry in entries:
                if not entry.is_file():
                    continue

                path = entry.path
                name_lower = entry.name.lower()
                size = scan_file(path)
                inventory["total_size"] += size

                if name_lower.endswith(
                    ".zip.part"
                ) or name_lower.endswith(
                    ".part"
                ):
                    inventory["partial_paths"].append(
                        path
                    )
                    inventory["partial_size"] += size

                elif name_lower.endswith(
                    ".zip"
                ):
                    inventory["zip_paths"].append(
                        path
                    )
                    inventory["zip_size"] += size

        if os.path.isdir(tiles_dir):
            try:
                entries = list(
                    os.scandir(tiles_dir)
                )
            except OSError:
                entries = []

            for entry in entries:
                if not entry.is_file():
                    continue

                path = entry.path
                name_lower = entry.name.lower()
                size = scan_file(path)
                inventory["total_size"] += size

                if name_lower.endswith(
                    ".extracting"
                ):
                    inventory["extracting_paths"].append(
                        path
                    )
                    inventory["extracting_size"] += size
                    continue

                if name_lower.endswith(raster_extensions):
                    inventory["tile_paths"].append(
                        path
                    )
                    inventory["tile_names"].add(
                        entry.name.lower()
                    )
                    inventory["tile_size"] += size
                else:
                    inventory["sidecar_paths"].append(
                        path
                    )
                    inventory["sidecar_size"] += size

        inventory["tile_count"] = len(
            inventory["tile_paths"]
        )
        inventory["sidecar_count"] = len(
            inventory["sidecar_paths"]
        )
        inventory["zip_count"] = len(
            inventory["zip_paths"]
        )
        inventory["partial_count"] = len(
            inventory["partial_paths"]
        )
        inventory["extracting_count"] = len(
            inventory["extracting_paths"]
        )

        vrt_path = (
            self.dock.vrt_edit.text().strip()
            if self.dock
            else ""
        )

        inventory["terrain_exists"] = bool(
            vrt_path
            and os.path.exists(vrt_path)
        )

        if inventory["terrain_exists"]:
            try:
                current_paths = self._existing_vrt_source_files(
                    vrt_path
                )
                inventory["current_terrain_names"] = {
                    os.path.basename(
                        path
                    ).lower()
                    for path in current_paths
                }
            except Exception:
                inventory["current_terrain_names"] = set()

        layer = self._current_tile_layer()

        if layer:
            inventory["selected_names"] = {
                str(
                    feature["FileName"]
                ).lower()
                for feature in layer.selectedFeatures()
            }

        inventory["protected_names"] = (
            inventory["current_terrain_names"]
            | inventory["selected_names"]
        )

        if inventory["terrain_exists"]:
            inventory["unused_tile_names"] = (
                inventory["tile_names"]
                - inventory["protected_names"]
            )

            unused_roots = {
                os.path.splitext(
                    name
                )[0]
                for name in inventory["unused_tile_names"]
            }

            unused_paths = []

            for path in (
                inventory["tile_paths"]
                + inventory["sidecar_paths"]
            ):
                base = os.path.basename(
                    path
                ).lower()

                # Ontario tile names do not contain a period before the
                # raster extension, so the first token identifies the tile
                # for IMG/TIF rasters and their sidecar/support files.
                file_root = base.split(
                    ".",
                    1,
                )[0]

                if file_root in unused_roots:
                    unused_paths.append(
                        path
                    )

            inventory["unused_tile_paths"] = list(
                dict.fromkeys(
                    unused_paths
                )
            )
            inventory["unused_tile_count"] = len(
                inventory["unused_tile_names"]
            )
            inventory["unused_tile_size"] = sum(
                scan_file(path)
                for path in inventory["unused_tile_paths"]
            )

        try:
            disk_target = (
                cache_root
                if os.path.exists(cache_root)
                else os.path.dirname(cache_root)
            )
            if disk_target:
                inventory["free_bytes"] = shutil.disk_usage(
                    disk_target
                ).free
        except Exception:
            inventory["free_bytes"] = None

        # Human-readable forms used by both the dock and cleanup dialog.
        inventory["tile_size_text"] = self._format_bytes(
            inventory["tile_size"]
        )
        inventory["sidecar_size_text"] = self._format_bytes(
            inventory["sidecar_size"]
        )
        inventory["zip_size_text"] = self._format_bytes(
            inventory["zip_size"]
        )
        inventory["partial_size_text"] = self._format_bytes(
            inventory["partial_size"]
        )
        inventory["extracting_size_text"] = self._format_bytes(
            inventory["extracting_size"]
        )
        inventory["unused_tile_size_text"] = self._format_bytes(
            inventory["unused_tile_size"]
        )
        inventory["total_size_text"] = self._format_bytes(
            inventory["total_size"]
        )
        inventory["free_size_text"] = (
            self._format_bytes(
                inventory["free_bytes"]
            )
            if inventory["free_bytes"] is not None
            else "--"
        )
        inventory["format_bytes"] = self._format_bytes

        return inventory

    def refresh_cache_manager(self, *args):
        if not self.dock:
            return

        def set_stats(
            tiles="--",
            sidecars="--",
            zips="--",
            partials="--",
            extracting="--",
            current="--",
            unused="--",
            total="--",
            free="--",
        ):
            self.dock.cache_stat_tiles.setText(
                "Raster tiles: " + str(tiles)
            )
            self.dock.cache_stat_sidecars.setText(
                "Sidecar/support files: " + str(sidecars)
            )
            self.dock.cache_stat_zips.setText(
                "Completed package ZIPs: " + str(zips)
            )
            self.dock.cache_stat_partials.setText(
                "Partial downloads: " + str(partials)
            )
            self.dock.cache_stat_extracting.setText(
                "Temporary extracts: " + str(extracting)
            )
            self.dock.cache_stat_current.setText(
                "Current terrain cached tiles: " + str(current)
            )
            self.dock.cache_stat_unused.setText(
                "Not used by current terrain/selection: " + str(unused)
            )
            self.dock.cache_stat_total.setText(
                "Total cache size: " + str(total)
            )
            self.dock.cache_stat_free.setText(
                "Free disk space: " + str(free)
            )

        try:
            inventory = self._cache_inventory()
            self.last_cache_inventory = inventory

            scope = (
                "Global/shared across projects"
                if self.dock.global_cache_checkbox.isChecked()
                else "Custom for this QGIS project"
            )
            self.dock.cache_scope_label.setText(
                "Cache scope: "
                + scope
                + " | Active dataset: "
                + self._dataset_short_name()
            )
            self.dock.cache_scope_label.setToolTip(
                "Active cache folder:\n"
                + (
                    os.path.normpath(inventory["cache_root"])
                    if inventory["cache_root"]
                    else "No cache folder selected"
                )
            )

            if not inventory["cache_root"]:
                set_stats()
                self.dock.cache_summary_label.setText(
                    f"No {self._dataset_short_name()} cache folder selected."
                )
                self.dock.open_cache_button.setEnabled(False)
                self.dock.cleanup_cache_button.setEnabled(False)
                return

            self.dock.open_cache_button.setEnabled(True)
            self.dock.cleanup_cache_button.setEnabled(True)

            current_cached = len(
                inventory["current_terrain_names"]
                & inventory["tile_names"]
            )

            if inventory["terrain_exists"]:
                unused_text = (
                    f"{inventory['unused_tile_count']} tile(s) "
                    f"({inventory['unused_tile_size_text']})"
                )
            else:
                unused_text = "-- (no configured terrain VRT)"

            set_stats(
                tiles=(
                    f"{inventory['tile_count']} "
                    f"({inventory['tile_size_text']})"
                ),
                sidecars=(
                    f"{inventory['sidecar_count']} "
                    f"({inventory['sidecar_size_text']})"
                ),
                zips=(
                    f"{inventory['zip_count']} "
                    f"({inventory['zip_size_text']})"
                ),
                partials=(
                    f"{inventory['partial_count']} "
                    f"({inventory['partial_size_text']})"
                ),
                extracting=(
                    f"{inventory['extracting_count']} "
                    f"({inventory['extracting_size_text']})"
                ),
                current=current_cached,
                unused=unused_text,
                total=inventory["total_size_text"],
                free=inventory["free_size_text"],
            )

            # Compatibility/debug text remains available as a hidden object.
            self.dock.cache_summary_label.setText(
                "\n".join(
                    [
                        self.dock.cache_stat_tiles.text(),
                        self.dock.cache_stat_sidecars.text(),
                        self.dock.cache_stat_zips.text(),
                        self.dock.cache_stat_partials.text(),
                        self.dock.cache_stat_extracting.text(),
                        self.dock.cache_stat_current.text(),
                        self.dock.cache_stat_unused.text(),
                        self.dock.cache_stat_total.text(),
                        self.dock.cache_stat_free.text(),
                    ]
                )
            )

        except Exception as exc:
            self.last_cache_inventory = None
            set_stats()
            self.dock.cache_scope_label.setText(
                "Cache scan failed"
            )
            self.dock.cache_scope_label.setToolTip(
                str(exc)
            )
            self.dock.cache_summary_label.setText(
                "Cache scan failed: " + str(exc)
            )

    def open_cache_folder(self):
        cache_root = self._effective_cache_folder()

        if not cache_root:
            QMessageBox.information(
                self.iface.mainWindow(),
                PLUGIN_NAME,
                "Choose an elevation cache root first.",
            )
            return

        try:
            os.makedirs(
                cache_root,
                exist_ok=True,
            )
        except Exception as exc:
            QMessageBox.critical(
                self.iface.mainWindow(),
                PLUGIN_NAME,
                "Could not create/open the cache folder:\n"
                + str(exc),
            )
            return

        opened = QDesktopServices.openUrl(
            QUrl.fromLocalFile(
                os.path.normpath(cache_root)
            )
        )

        if not opened:
            QMessageBox.warning(
                self.iface.mainWindow(),
                PLUGIN_NAME,
                "QGIS could not open the cache folder in the system file browser.",
            )

    def _task_is_running(self):
        if self.active_task is None:
            return False

        try:
            return self.active_task.status() in (
                Qgis.TaskStatus.Queued,
                Qgis.TaskStatus.Running,
            )
        except Exception:
            return False

    def manage_cache_cleanup(self):
        if self._task_is_running():
            QMessageBox.information(
                self.iface.mainWindow(),
                PLUGIN_NAME,
                "Wait for the active Ontario elevation download/build task to finish "
                "or cancel it before cleaning the cache.",
            )
            return

        inventory = self._cache_inventory()
        self.last_cache_inventory = inventory

        if not inventory["cache_root"]:
            QMessageBox.information(
                self.iface.mainWindow(),
                PLUGIN_NAME,
                "Choose an elevation cache root first.",
            )
            return

        dialog = CacheCleanupDialog(
            self.iface.mainWindow(),
            inventory,
            self.dock.global_cache_checkbox.isChecked(),
        )

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        options = dialog.cleanup_options()

        files_to_delete = []

        if options["zips"]:
            files_to_delete.extend(
                inventory["zip_paths"]
            )

        if options["partials"]:
            files_to_delete.extend(
                inventory["partial_paths"]
            )

        if options["extracting"]:
            files_to_delete.extend(
                inventory["extracting_paths"]
            )

        if options["unused_tiles"]:
            files_to_delete.extend(
                inventory["unused_tile_paths"]
            )

        files_to_delete = list(
            dict.fromkeys(
                files_to_delete
            )
        )

        if not files_to_delete:
            QMessageBox.information(
                self.iface.mainWindow(),
                PLUGIN_NAME,
                "No cache items were selected for deletion.",
            )
            return

        total_size = 0
        for path in files_to_delete:
            with suppress(OSError):
                total_size += os.path.getsize(
                    path
                )

        warning_lines = [
            f"Files to delete: {len(files_to_delete)}",
            f"Approximate space to recover: {self._format_bytes(total_size)}",
            "",
            "This cannot be undone.",
        ]

        if options["partials"]:
            warning_lines.extend(
                [
                    "",
                    "Partial downloads selected for deletion will no longer be resumable.",
                ]
            )

        if (
            options["unused_tiles"]
            and self.dock.global_cache_checkbox.isChecked()
        ):
            warning_lines.extend(
                [
                    "",
                    "CAUTION: this is a GLOBAL/SHARED cache.",
                    "Some pruned raster tiles may be useful to other QGIS projects "
                    "even though the current terrain does not reference them.",
                ]
            )

        answer = QMessageBox.question(
            self.iface.mainWindow(),
            f"Confirm Ontario {self._dataset_short_name()} Cache Cleanup",
            "\n".join(warning_lines),
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if answer != QMessageBox.StandardButton.Yes:
            return

        deleted = 0
        recovered = 0
        errors = []

        for path in files_to_delete:
            if not os.path.exists(path):
                continue

            try:
                size = os.path.getsize(
                    path
                )
            except OSError:
                size = 0

            try:
                os.remove(
                    path
                )
                deleted += 1
                recovered += size
            except Exception as exc:
                errors.append(
                    f"{path}: {exc}"
                )

        self.refresh_cache_status()
        self.update_counts()
        self.refresh_cache_manager()

        message = (
            f"Deleted {deleted} cache file(s) and recovered "
            f"approximately {self._format_bytes(recovered)}."
        )

        if errors:
            QMessageBox.warning(
                self.iface.mainWindow(),
                PLUGIN_NAME,
                message
                + "\n\nSome files could not be deleted:\n"
                + "\n".join(errors[:10]),
            )
        else:
            self.iface.messageBar().pushMessage(
                PLUGIN_NAME,
                message,
                level=Qgis.MessageLevel.Success,
                duration=10,
            )

    def _remaining_package_download_bytes(
        self,
        job,
        packages_dir,
    ):
        remote_size = int(
            job.get("remote_size") or 0
        )

        final_path = os.path.join(
            packages_dir,
            job["archive_name"],
        )
        partial_path = final_path + ".part"

        if (
            os.path.exists(final_path)
            and zipfile.is_zipfile(final_path)
        ):
            return 0, "local ZIP ready"

        if os.path.exists(partial_path):
            try:
                partial_size = os.path.getsize(
                    partial_path
                )
            except OSError:
                partial_size = 0

            if remote_size > 0:
                remaining = max(
                    0,
                    remote_size - partial_size,
                )
                return (
                    remaining,
                    "resume "
                    f"{self._format_bytes(partial_size)}"
                    f" / {self._format_bytes(remote_size)}",
                )

            return (
                remote_size,
                "partial download present "
                f"({self._format_bytes(partial_size)})",
            )

        return (
            remote_size,
            "download required",
        )

    # --------------------------------------------------------
    # CACHE STATUS
    # --------------------------------------------------------
    def refresh_cache_status(self):
        layer = self._current_tile_layer()

        if not layer:
            return

        cache_root = self._effective_cache_folder()
        tiles_dir = (
            os.path.join(
                cache_root,
                "tiles",
            )
            if cache_root
            else ""
        )

        cached_index = layer.fields().indexOf(
            "Cached"
        )

        if cached_index < 0:
            return

        changes = {}

        for feature in layer.getFeatures():
            filename = str(
                feature["FileName"]
            )

            is_cached = int(
                bool(tiles_dir)
                and os.path.exists(
                    os.path.join(
                        tiles_dir,
                        filename,
                    )
                )
            )

            if int(feature["Cached"] or 0) != is_cached:
                changes[feature.id()] = {
                    cached_index: is_cached
                }

        if changes:
            layer.dataProvider().changeAttributeValues(
                changes
            )
            layer.triggerRepaint()

    # --------------------------------------------------------
    # TILE SELECTION HELPERS
    # --------------------------------------------------------
    def _current_tile_layer(self):
        layers = QgsProject.instance().mapLayersByName(
            self._dataset_tile_layer_name()
        )

        if not layers:
            return None

        layer = layers[0]
        self.tile_layer = layer
        return layer

    def select_all_tiles(self):
        layer = self._current_tile_layer()

        if layer:
            layer.selectAll()
            self.update_counts()

    def clear_tile_selection(self):
        layer = self._current_tile_layer()

        if layer:
            layer.removeSelection()
            self.update_counts()

    def zoom_to_tiles(self):
        layer = self._current_tile_layer()

        if layer and layer.featureCount():
            self.iface.mapCanvas().setExtent(
                layer.extent()
            )
            self.iface.mapCanvas().refresh()

    def update_counts(self, *args):
        if not self.dock:
            return

        layer = self._current_tile_layer()
        available = (
            layer.featureCount()
            if layer
            else 0
        )
        selected = (
            layer.selectedFeatures()
            if layer
            else []
        )

        cache_root = self._effective_cache_folder()
        tiles_dir = (
            os.path.join(
                cache_root,
                "tiles",
            )
            if cache_root
            else ""
        )

        cached = 0

        if tiles_dir:
            for feature in selected:
                filename = str(
                    feature["FileName"]
                )

                if os.path.exists(
                    os.path.join(
                        tiles_dir,
                        filename,
                    )
                ):
                    cached += 1

        self.dock.available_label.setText(
            f"Available tiles: {available}"
        )
        self.dock.selected_label.setText(
            f"Selected tiles: {len(selected)}"
        )
        self.dock.cached_label.setText(
            f"Already cached: {cached}"
        )
        self.dock.missing_label.setText(
            f"Need download: "
            f"{max(0, len(selected) - cached)}"
        )

        self.refresh_terrain_lifecycle()

    def _selected_tile_records(self):
        layer = self._current_tile_layer()

        if not layer:
            raise RuntimeError(
                f"No Ontario {self._dataset_short_name()} tile layer exists. "
                f"Run 'Find Available {self._dataset_short_name()} Tiles' first."
            )

        selected = layer.selectedFeatures()

        if not selected:
            raise RuntimeError(
                f"Select one or more {self._dataset_short_name()} tiles first."
            )

        return [
            {
                "Project": str(
                    feature["Project"]
                ),
                "Package": str(
                    feature["Package"]
                ),
                "FileName": str(
                    feature["FileName"]
                ),
            }
            for feature in selected
        ]

    # --------------------------------------------------------
    # TERRAIN LIFECYCLE / UPDATE ANALYSIS
    # --------------------------------------------------------
    def _existing_vrt_source_files(self, vrt_path):
        """Return IMG sources referenced by an existing VRT."""
        if not vrt_path or not os.path.exists(vrt_path):
            return []

        gdal.UseExceptions()

        try:
            dataset = gdal.Open(
                vrt_path,
                gdal.GA_ReadOnly,
            )
        except Exception as exc:
            raise RuntimeError(
                "The existing terrain VRT could not be opened:\n"
                f"{vrt_path}\n\n{exc}"
            )

        if dataset is None:
            raise RuntimeError(
                "The existing terrain VRT could not be opened:\n"
                + vrt_path
            )

        file_list = dataset.GetFileList() or []
        dataset = None

        raster_extensions = tuple(
            ext.lower()
            for ext in self._selected_raster_dataset().get(
                "extensions", (".img", ".tif", ".tiff")
            )
        )
        return [
            os.path.normpath(path)
            for path in file_list
            if str(path).lower().endswith(raster_extensions)
        ]

    def _terrain_change_analysis(self, selected_filenames=None):
        vrt_path = (
            self.dock.vrt_edit.text().strip()
            if self.dock
            else ""
        )

        exists = bool(
            vrt_path
            and os.path.exists(vrt_path)
        )

        current_paths = (
            self._existing_vrt_source_files(vrt_path)
            if exists
            else []
        )

        current_by_key = {
            os.path.basename(path).lower(): os.path.basename(path)
            for path in current_paths
        }

        if selected_filenames is None:
            layer = self._current_tile_layer()
            selected_filenames = (
                [
                    str(feature["FileName"])
                    for feature in layer.selectedFeatures()
                ]
                if layer
                else []
            )

        selected_by_key = {
            str(name).lower(): str(name)
            for name in selected_filenames
        }

        current_keys = set(current_by_key)
        selected_keys = set(selected_by_key)

        keep_keys = current_keys & selected_keys
        add_keys = selected_keys - current_keys
        remove_keys = current_keys - selected_keys

        return {
            "vrt_path": vrt_path,
            "exists": exists,
            "current_paths": current_paths,
            "current_names": [
                current_by_key[key]
                for key in sorted(current_keys)
            ],
            "selected_names": [
                selected_by_key[key]
                for key in sorted(selected_keys)
            ],
            "keep_names": [
                selected_by_key.get(key, current_by_key[key])
                for key in sorted(keep_keys)
            ],
            "add_names": [
                selected_by_key[key]
                for key in sorted(add_keys)
            ],
            "remove_names": [
                current_by_key[key]
                for key in sorted(remove_keys)
            ],
        }

    def refresh_terrain_lifecycle(self, *args):
        if not self.dock:
            return

        try:
            analysis = self._terrain_change_analysis()
        except Exception as exc:
            self.dock.terrain_state_label.setText(
                "Terrain status: Existing VRT could not be read"
            )
            self.dock.terrain_current_label.setText(
                "Current terrain tiles: ?"
            )
            self.dock.terrain_keep_label.setText(
                "Keep unchanged: --"
            )
            self.dock.terrain_add_label.setText(
                "Add to terrain: --"
            )
            self.dock.terrain_remove_label.setText(
                "Remove from terrain: --"
            )
            self.dock.build_button.setText(
                "Download / Build Terrain"
            )
            self.dock.status_label.setToolTip(str(exc))
            return

        selected_count = len(
            analysis["selected_names"]
        )
        current_count = len(
            analysis["current_names"]
        )

        if analysis["exists"]:
            self.dock.terrain_state_label.setText(
                "Terrain status: Existing VRT found"
            )
            self.dock.build_button.setText(
                "Update Existing Terrain"
            )
            self.dock.select_existing_button.setEnabled(
                current_count > 0
            )
        else:
            self.dock.terrain_state_label.setText(
                "Terrain status: New terrain"
            )
            self.dock.build_button.setText(
                "Download / Build Terrain"
            )
            self.dock.select_existing_button.setEnabled(
                False
            )

        self.dock.terrain_current_label.setText(
            f"Current terrain tiles: {current_count}"
        )

        if selected_count:
            self.dock.terrain_keep_label.setText(
                f"Keep unchanged: {len(analysis['keep_names'])}"
            )
            self.dock.terrain_add_label.setText(
                f"Add to terrain: {len(analysis['add_names'])}"
            )
            self.dock.terrain_remove_label.setText(
                f"Remove from terrain: {len(analysis['remove_names'])}"
            )
        else:
            self.dock.terrain_keep_label.setText(
                "Keep unchanged: --"
            )
            self.dock.terrain_add_label.setText(
                "Add to terrain: --"
            )
            self.dock.terrain_remove_label.setText(
                "Remove from terrain: --"
            )

    def select_existing_terrain_tiles(self):
        layer = self._current_tile_layer()

        if not layer:
            QMessageBox.information(
                self.iface.mainWindow(),
                PLUGIN_NAME,
                f"Find available {self._dataset_short_name()} tiles first.",
            )
            return

        try:
            analysis = self._terrain_change_analysis([])
        except Exception as exc:
            QMessageBox.critical(
                self.iface.mainWindow(),
                PLUGIN_NAME,
                str(exc),
            )
            return

        current_keys = {
            name.lower()
            for name in analysis["current_names"]
        }

        ids = [
            feature.id()
            for feature in layer.getFeatures()
            if str(feature["FileName"]).lower()
            in current_keys
        ]

        layer.removeSelection()

        if ids:
            layer.select(ids)
            self.dock.status_label.setText(
                f"Selected {len(ids)} current-terrain tile(s) visible "
                "in the available-tile layer."
            )
        else:
            self.dock.status_label.setText(
                "No current-terrain tiles are present in the current "
                "available-tile query."
            )

        self.update_counts()

    def _working_vrt_path(self, final_vrt_path):
        stem, extension = os.path.splitext(
            final_vrt_path
        )
        extension = extension or ".vrt"
        return stem + ".updating" + extension

    def _cleanup_working_vrt(self, path):
        if not path:
            return

        for candidate in (
            path,
            path + ".aux.xml",
        ):
            if os.path.exists(candidate):
                with suppress(OSError):
                    os.remove(candidate)

    def _backup_vrt(self, vrt_path):
        if not os.path.exists(vrt_path):
            return None

        stamp = datetime.now().strftime(
            "%Y%m%d_%H%M%S"
        )
        stem, extension = os.path.splitext(
            vrt_path
        )
        backup_path = (
            f"{stem}_backup_{stamp}"
            f"{extension or '.vrt'}"
        )

        shutil.copy2(
            vrt_path,
            backup_path,
        )
        return backup_path

    def _commit_working_vrt(
        self,
        working_vrt_path,
        final_vrt_path,
        make_backup,
    ):
        """Atomically swap a successfully built working VRT into place.

        The existing terrain remains intact throughout downloading and VRT
        construction. It is removed from QGIS only immediately before the
        final file replacement.
        """
        if not os.path.exists(
            working_vrt_path
        ):
            raise RuntimeError(
                "The temporary terrain VRT was not created."
            )

        # Validate the new VRT before touching the current one.
        dataset = gdal.Open(
            working_vrt_path,
            gdal.GA_ReadOnly,
        )
        if dataset is None:
            raise RuntimeError(
                "The newly built terrain VRT is invalid. "
                "The existing terrain was left unchanged."
            )
        dataset = None

        backup_path = None
        if (
            make_backup
            and os.path.exists(final_vrt_path)
        ):
            backup_path = self._backup_vrt(
                final_vrt_path
            )

        # Remove the loaded final VRT only at commit time, which avoids
        # Windows file-lock issues while preserving the old terrain during
        # long downloads.
        self._remove_existing_terrain_layer(
            final_vrt_path
        )

        try:
            for stale in (
                final_vrt_path + ".aux.xml",
            ):
                if os.path.exists(stale):
                    with suppress(OSError):
                        os.remove(stale)

            os.replace(
                working_vrt_path,
                final_vrt_path,
            )
        except Exception:
            # If replacement fails, the original file normally remains.
            # Try to reload it so the user's project is not left blank.
            if os.path.exists(final_vrt_path):
                with suppress(Exception):
                    self._load_finished_vrt(
                        final_vrt_path,
                        len(self._existing_vrt_source_files(final_vrt_path)),
                    )
            raise

        self._cleanup_working_vrt(
            working_vrt_path
        )
        return backup_path

    # --------------------------------------------------------
    # TERRAIN BUILD
    # --------------------------------------------------------
    def _remove_existing_terrain_layer(
        self,
        target_vrt=None,
    ):
        names_to_remove = {
            self._terrain_layer_name(),
        }
        if self._selected_dataset_key() == "lidar_dtm":
            names_to_remove.add(TERRAIN_LAYER_NAME)

        for layer_name in names_to_remove:
            for layer in QgsProject.instance().mapLayersByName(
                layer_name
            ):
                QgsProject.instance().removeMapLayer(
                    layer.id()
                )

        if target_vrt:
            target_norm = os.path.normcase(
                os.path.abspath(
                    target_vrt
                )
            )

            for layer in list(
                QgsProject.instance().mapLayers().values()
            ):
                if isinstance(
                    layer,
                    QgsRasterLayer,
                ):
                    source = layer.source()

                    if (
                        source
                        and os.path.normcase(
                            os.path.abspath(source)
                        )
                        == target_norm
                    ):
                        QgsProject.instance().removeMapLayer(
                            layer.id()
                        )

    def download_and_build(self):
        try:
            if self.active_task is not None:
                with suppress(Exception):
                    if self.active_task.status() in (
                        Qgis.TaskStatus.Queued,
                        Qgis.TaskStatus.Running,
                    ):
                        QMessageBox.information(
                            self.iface.mainWindow(),
                            PLUGIN_NAME,
                            "An Ontario elevation download/build task is already running.",
                        )
                        return

            selected_tiles = self._selected_tile_records()
            self.project_output_changed()

            cache_root = self._effective_cache_folder()

            if not cache_root:
                self.browse_cache()
                cache_root = self._effective_cache_folder()

                if not cache_root:
                    return

            final_vrt_path = self.dock.vrt_edit.text().strip()

            if not final_vrt_path:
                self._set_default_vrt_if_blank()
                final_vrt_path = self.dock.vrt_edit.text().strip()

            if not final_vrt_path:
                if self.dock.project_output_checkbox.isChecked():
                    raise RuntimeError(
                        "The QGIS project is unsaved, so an automatic project "
                        "output folder is not available. Save the QGIS project "
                        "or uncheck 'Use QGIS project folder automatically' and "
                        "choose a custom output folder."
                    )

                raise RuntimeError(
                    "Choose a custom terrain output folder."
                )

            if not final_vrt_path.lower().endswith(".vrt"):
                final_vrt_path += ".vrt"
                self.dock.vrt_edit.setText(final_vrt_path)

            working_vrt_path = self._working_vrt_path(
                final_vrt_path
            )
            self._cleanup_working_vrt(
                working_vrt_path
            )

            packages_dir = os.path.join(
                cache_root,
                "packages",
            )
            tiles_dir = os.path.join(
                cache_root,
                "tiles",
            )

            os.makedirs(
                packages_dir,
                exist_ok=True,
            )
            os.makedirs(
                tiles_dir,
                exist_ok=True,
            )

            selected_filenames = [
                tile["FileName"]
                for tile in selected_tiles
            ]

            analysis = self._terrain_change_analysis(
                selected_filenames
            )

            missing_tiles = [
                tile
                for tile in selected_tiles
                if not os.path.exists(
                    os.path.join(
                        tiles_dir,
                        tile["FileName"],
                    )
                )
            ]

            existing_count = len(
                analysis["current_names"]
            )
            keep_count = len(
                analysis["keep_names"]
            )
            add_count = len(
                analysis["add_names"]
            )
            remove_count = len(
                analysis["remove_names"]
            )

            update_mode = analysis["exists"]
            make_backup = bool(
                update_mode
                and self.dock.backup_vrt_checkbox.isChecked()
            )

            # If an existing terrain has exactly the same tile membership,
            # an explicit run is still allowed because it can repair/rebuild
            # the VRT from the cache. Make the confirmation clear.
            change_summary = (
                f"Current terrain tiles: {existing_count}\n"
                f"Keep unchanged: {keep_count}\n"
                f"Add to terrain: {add_count}\n"
                f"Remove from terrain: {remove_count}\n"
            )

            # All selected source tiles are already cached: no network work.
            if not missing_tiles:
                action_word = "Update" if update_mode else "Create"
                summary = (
                    f"{action_word} terrain from cached Ontario {self._dataset_short_name()} tiles?\n\n"
                    f"{change_summary}\n"
                    f"Selected tiles: {len(selected_tiles)}\n"
                    "Tiles requiring download: 0\n\n"
                    + (
                        "A timestamped backup of the existing VRT will be kept.\n"
                        if make_backup
                        else ""
                    )
                    + (
                        f"Tiles removed from the terrain will remain in the {self._dataset_short_name()} cache.\n\n"
                        if remove_count
                        else ""
                    )
                    + f"VRT:\n{final_vrt_path}"
                )

                answer = QMessageBox.question(
                    self.iface.mainWindow(),
                    f"Update Ontario {self._dataset_short_name()} Terrain" if update_mode else f"Build Ontario {self._dataset_short_name()} Terrain",
                    summary,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )

                if answer != QMessageBox.StandardButton.Yes:
                    return

                raster_paths = [
                    os.path.join(
                        tiles_dir,
                        filename,
                    )
                    for filename in selected_filenames
                ]

                self.dock.status_label.setText(
                    "Building updated VRT from cached tiles..."
                    if update_mode
                    else "Building VRT from cached tiles..."
                )
                QApplication.processEvents()

                build_vrt(
                    working_vrt_path,
                    raster_paths,
                )

                backup_path = self._commit_working_vrt(
                    working_vrt_path,
                    final_vrt_path,
                    make_backup,
                )

                self.dock.progress.setValue(100)
                self._load_finished_vrt(
                    final_vrt_path,
                    len(selected_filenames),
                )

                if backup_path:
                    self.dock.status_label.setToolTip(
                        "Previous terrain backup: "
                        + os.path.normpath(backup_path)
                    )

                self.refresh_cache_status()
                self.update_counts()
                self.refresh_cache_manager()
                return

            self.dock.status_label.setText(
                "Resolving Ontario download packages..."
            )
            QApplication.processEvents()

            jobs = resolve_package_jobs(
                missing_tiles,
                self._selected_dataset_key(),
            )

            package_status = []
            remaining_bytes = 0
            unknown_remaining = False
            listed_remaining_gb = 0.0

            for job in jobs:
                remaining, state = self._remaining_package_download_bytes(
                    job,
                    packages_dir,
                )
                package_status.append(
                    (
                        job,
                        remaining,
                        state,
                    )
                )

                if job.get("remote_size"):
                    remaining_bytes += int(
                        remaining or 0
                    )
                elif state != "local ZIP ready":
                    unknown_remaining = True
                    with suppress(TypeError, ValueError):
                        listed_remaining_gb += float(
                            job.get("listed_size_gb") or 0
                        )

            if unknown_remaining:
                known_text = self._format_bytes(
                    remaining_bytes
                )
                if listed_remaining_gb > 0:
                    download_size = (
                        f"{known_text} known remaining + approximately "
                        f"{listed_remaining_gb:.2f} GB where server size is unavailable"
                    )
                else:
                    download_size = (
                        f"{known_text} known remaining + unknown amount"
                    )
            else:
                download_size = self._format_bytes(
                    remaining_bytes
                )

            free_bytes = shutil.disk_usage(
                cache_root
            ).free

            if (
                remaining_bytes
                and free_bytes < remaining_bytes * 1.05
            ):
                raise RuntimeError(
                    "The cache drive may not have enough free "
                    f"space for the remaining {download_size} download."
                )

            package_lines = "\n".join(
                f"  • {job['package']} "
                f"({len(job['files'])} tile(s)) — {state}"
                for job, remaining, state in package_status
            )

            summary = (
                ("Update existing terrain\n\n" if update_mode else "Create new terrain\n\n")
                + change_summary
                + f"\nSelected tiles: {len(selected_tiles)}\n"
                + f"Already cached: {len(selected_tiles) - len(missing_tiles)}\n"
                + f"Tiles to download/extract: {len(missing_tiles)}\n"
                + f"Packages required: {len(jobs)}\n"
                + f"Download size: {download_size}\n\n"
                + f"Packages:\n{package_lines}\n\n"
                + (
                    "A timestamped backup of the existing VRT will be kept.\n"
                    if make_backup
                    else ""
                )
                + (
                    f"Tiles removed from the terrain will remain in the {self._dataset_short_name()} cache.\n"
                    if remove_count
                    else ""
                )
                + "The current terrain remains untouched until the new VRT "
                  "has been built successfully.\n\n"
                + f"Cache:\n{cache_root}\n\n"
                + f"VRT:\n{final_vrt_path}\n\n"
                + "Start?"
            )

            answer = QMessageBox.question(
                self.iface.mainWindow(),
                f"Update Ontario {self._dataset_short_name()} Terrain" if update_mode else f"Ontario {self._dataset_short_name()} Download",
                summary,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )

            if answer != QMessageBox.StandardButton.Yes:
                self.dock.status_label.setText("Ready")
                return

            keep_zips = not (
                self.dock.delete_zip_checkbox.isChecked()
            )

            task = OntarioDTMDownloadTask(
                jobs=jobs,
                packages_directory=packages_dir,
                tiles_directory=tiles_dir,
                selected_filenames=selected_filenames,
                vrt_path=working_vrt_path,
                keep_zips=keep_zips,
                completion_callback=self._task_finished,
                dataset_short_name=self._dataset_short_name(),
            )

            # Main-thread commit information. The background task builds only
            # the temporary VRT, so the current terrain stays operational until
            # success is confirmed.
            task.final_vrt_path = final_vrt_path
            task.make_backup = make_backup
            task.update_mode = update_mode
            task.previous_tile_count = existing_count
            task.keep_count = keep_count
            task.add_count = add_count
            task.remove_count = remove_count

            task.progressChanged.connect(
                lambda value: self.dock.progress.setValue(
                    int(value)
                )
            )

            self.active_task = task
            self.dock.progress.setValue(0)
            self.dock.build_button.setEnabled(False)
            self.dock.cancel_button.setEnabled(True)
            self.dock.status_label.setText(
                "Downloading required tiles in background; current terrain remains unchanged..."
                if update_mode
                else "Downloading and building terrain in background..."
            )

            QgsApplication.taskManager().addTask(task)

        except Exception as exc:
            self.dock.build_button.setEnabled(True)
            self.dock.cancel_button.setEnabled(False)
            self.dock.status_label.setText(
                "Operation failed."
            )

            QMessageBox.critical(
                self.iface.mainWindow(),
                PLUGIN_NAME,
                str(exc),
            )

    def cancel_active_task(self):
        if self.active_task is None:
            return

        try:
            self.active_task.cancel()
            self.dock.status_label.setText(
                "Cancel requested..."
            )
            self.dock.cancel_button.setEnabled(
                False
            )
        except Exception as exc:
            QMessageBox.warning(
                self.iface.mainWindow(),
                PLUGIN_NAME,
                f"Could not cancel task:\n{exc}",
            )

    def _task_finished(
        self,
        task,
        result,
    ):
        self.dock.build_button.setEnabled(True)
        self.dock.cancel_button.setEnabled(False)

        working_vrt_path = task.vrt_path
        final_vrt_path = getattr(
            task,
            "final_vrt_path",
            task.vrt_path,
        )

        if task.isCanceled():
            self._cleanup_working_vrt(
                working_vrt_path
            )
            self.dock.status_label.setText(
                "Cancelled. Current terrain unchanged; partial .part file retained for resume."
            )
            self.iface.messageBar().pushMessage(
                PLUGIN_NAME,
                (
                    "Download cancelled. Existing terrain was not changed; "
                    "partial package download was retained for resume."
                ),
                level=Qgis.MessageLevel.Warning,
                duration=12,
            )
            self.active_task = None
            self.refresh_cache_manager()
            return

        if not result:
            self._cleanup_working_vrt(
                working_vrt_path
            )
            self.dock.status_label.setText(
                "Download/build failed. Current terrain unchanged."
            )

            QMessageBox.critical(
                self.iface.mainWindow(),
                PLUGIN_NAME,
                (task.error_message or "Unknown background-task failure.")
                + "\n\nThe existing terrain VRT was not replaced.",
            )

            self.active_task = None
            self.refresh_cache_manager()
            return

        try:
            backup_path = self._commit_working_vrt(
                working_vrt_path,
                final_vrt_path,
                bool(getattr(task, "make_backup", False)),
            )

            self.dock.progress.setValue(100)
            self._load_finished_vrt(
                final_vrt_path,
                len(task.selected_filenames),
            )

            if backup_path:
                self.dock.status_label.setToolTip(
                    "Previous terrain backup: "
                    + os.path.normpath(backup_path)
                )

            self.iface.messageBar().pushMessage(
                PLUGIN_NAME,
                (
                    f"Terrain updated: {getattr(task, 'keep_count', 0)} kept, "
                    f"{getattr(task, 'add_count', 0)} added, "
                    f"{getattr(task, 'remove_count', 0)} removed."
                    if getattr(task, "update_mode", False)
                    else f"Ontario {self._dataset_short_name()} terrain created successfully."
                ),
                level=Qgis.MessageLevel.Success,
                duration=10,
            )

        except Exception as exc:
            self._cleanup_working_vrt(
                working_vrt_path
            )
            self.dock.status_label.setText(
                "New VRT built, but final terrain replacement failed."
            )
            QMessageBox.critical(
                self.iface.mainWindow(),
                PLUGIN_NAME,
                str(exc),
            )

        finally:
            self.active_task = None
            self.refresh_cache_status()
            self.update_counts()
            self.refresh_cache_manager()

    def _load_finished_vrt(
        self,
        vrt_path,
        source_count,
    ):
        if not (
            self.dock.add_to_project_checkbox.isChecked()
        ):
            self.dock.status_label.setText(
                f"VRT created from {source_count} tile(s): {vrt_path}"
            )
            self.iface.messageBar().pushMessage(
                PLUGIN_NAME,
                f"Ontario {self._dataset_short_name()} VRT created successfully.",
                level=Qgis.MessageLevel.Success,
                duration=8,
            )
            self.refresh_export_status()
            return

        self._remove_existing_terrain_layer(
            vrt_path
        )

        raster_layer = QgsRasterLayer(
            vrt_path,
            self._terrain_layer_name(),
            "gdal",
        )

        if not raster_layer.isValid():
            raise RuntimeError(
                "The VRT was created, but QGIS could not load it."
            )

        root = QgsProject.instance().layerTreeRoot()
        group_name = self._dataset_terrain_group_name()
        group = root.findGroup(
            group_name
        )

        if group is None:
            group = root.insertGroup(
                0,
                group_name,
            )

        QgsProject.instance().addMapLayer(
            raster_layer,
            False,
        )
        group.addLayer(
            raster_layer
        )

        self.iface.setActiveLayer(
            raster_layer
        )
        raster_layer.triggerRepaint()

        crs_text = (
            raster_layer.crs().authid()
            or raster_layer.crs().description()
        )

        self.dock.status_label.setText(
            f"Ready: {source_count} tile(s), "
            f"{raster_layer.rasterUnitsPerPixelX():g} m pixels, "
            f"CRS {crs_text}, NoData {OUTPUT_NODATA:g}."
        )

        self.iface.messageBar().pushMessage(
            PLUGIN_NAME,
            (
                f"Project terrain created from "
                f"{source_count} {self._dataset_short_name()} tile(s)."
            ),
            level=Qgis.MessageLevel.Success,
            duration=10,
        )
        self.refresh_export_status()
