"""GeoJSON書き出し

LayerFeatures → GeoJSON FeatureCollection dict / ファイル書き出し。
出力単位は「1レイヤ = 1 GeoJSON FeatureCollection」。

座標系: GeoJSON出力は元CRSのまま + .crs副作用なし。
crsヒントは FeatureCollection の foreign member
"crs_hint": {"epsg": N} で付与する（EPSG不明時は付与しない）。
"""

from __future__ import annotations

import json
import os

from shapely.geometry import mapping

from .features import FeatureItem, LayerFeatures


def _properties_jsonable(properties: dict) -> dict:
    """properties を JSON 化可能な形に正規化する（None は null のまま残す）。"""
    out = {}
    for key, value in properties.items():
        if value is None or isinstance(value, (bool, int, float, str)):
            out[key] = value
        else:
            out[key] = str(value)
    return out


def feature_to_geojson_dict(item: FeatureItem) -> dict:
    """FeatureItem → GeoJSON Feature dict。"""
    return {
        "type": "Feature",
        "geometry": mapping(item.geometry),
        "properties": _properties_jsonable(item.properties),
    }


def layer_to_feature_collection(layer: LayerFeatures) -> dict:
    """LayerFeatures → GeoJSON FeatureCollection dict。

    foreign member "crs_hint": {"epsg": N} を付与する。
    layer.epsg が None の場合は crs_hint を付与しない。
    """
    fc: dict = {
        "type": "FeatureCollection",
        "features": [feature_to_geojson_dict(item) for item in layer.features],
    }
    if layer.epsg is not None:
        fc["crs_hint"] = {"epsg": layer.epsg}
    return fc


def write_layer_geojson(layer: LayerFeatures, path: str) -> str:
    """LayerFeatures を UTF-8 の GeoJSON ファイルに書き出す。

    Returns:
        書き出したファイルパス（str）。
    """
    fc = layer_to_feature_collection(layer)
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(fc, f, ensure_ascii=False)
    return path
