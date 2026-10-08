"""CRS変換（平面直角系 → WGS84等）

pyproj Transformer + shapely.ops.transform でレイヤを再投影する。
Transformerは (from, to) ごとにキャッシュする。
"""

from __future__ import annotations

from pyproj import Transformer
from shapely.ops import transform as shapely_transform

from .features import FeatureItem, LayerFeatures

WGS84_EPSG = 4326

_transformers: dict[tuple[int, int], Transformer] = {}


def get_transformer(from_epsg: int, to_epsg: int) -> Transformer:
    """(from_epsg → to_epsg) のTransformerを返す（キャッシュ付き）。"""
    key = (from_epsg, to_epsg)
    transformer = _transformers.get(key)
    if transformer is None:
        transformer = Transformer.from_crs(from_epsg, to_epsg, always_xy=True)
        _transformers[key] = transformer
    return transformer


def reproject_layer(layer: LayerFeatures, to_epsg: int) -> LayerFeatures:
    """LayerFeaturesをto_epsgに再投影したコピーを返す。

    属性はそのまま引き継ぐ。同一EPSGの場合は元のオブジェクトを返す。

    Raises:
        ValueError: layer.epsgがNone（座標系不明）の場合。
    """
    if layer.epsg is None:
        raise ValueError(f"座標系不明のため変換できません: {layer.name}")
    if layer.epsg == to_epsg:
        return layer
    transformer = get_transformer(layer.epsg, to_epsg)
    return LayerFeatures(
        name=layer.name,
        element_type=layer.element_type,
        dm_code=layer.dm_code,
        geom_kind=layer.geom_kind,
        epsg=to_epsg,
        features=[
            FeatureItem(
                geometry=shapely_transform(transformer.transform, item.geometry),
                properties=dict(item.properties),
            )
            for item in layer.features
        ],
    )
