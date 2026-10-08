"""dm_converter: QGIS不要の純Python DM→GeoJSON変換パッケージ。"""

from __future__ import annotations

from dm_converter.api import (
    KNOWN_MESH_KINDS,
    ConversionResult,
    MeshResult,
    convert_dm,
    convert_files,
    convert_folder,
    mesh_code_geojson,
    mesh_geojson,
    parse_dm_bytes,
    parse_dm_file,
    write_geojson,
)
from dm_converter.reproject import WGS84_EPSG, get_transformer, reproject_layer

__version__ = "0.1.0"

__all__ = [
    "KNOWN_MESH_KINDS",
    "ConversionResult",
    "MeshResult",
    "WGS84_EPSG",
    "convert_dm",
    "convert_files",
    "convert_folder",
    "get_transformer",
    "mesh_code_geojson",
    "mesh_geojson",
    "parse_dm_bytes",
    "parse_dm_file",
    "reproject_layer",
    "write_geojson",
]
