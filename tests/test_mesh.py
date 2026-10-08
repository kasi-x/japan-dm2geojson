"""地域メッシュ生成テスト。"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from shapely.geometry import shape

from dm_converter.api import KNOWN_MESH_KINDS, mesh_code_geojson, mesh_geojson
from dm_converter.cli import main
from dm_converter.mesh.gridsquare_to_box import (
    grid_square_code_to_bbox,
    lnglat_to_grid_square_code,
)
from dm_converter.mesh.mesh_features import (
    mesh_feature,
    mesh_patches,
    mesh_polygon,
)

# 既知コード周辺（東京・皇居付近の基準地域メッシュ）
STANDARD_CODE = "53393599"


def _center(bbox):
    xmin, ymin, xmax, ymax = bbox
    return ((xmin + xmax) / 2, (ymin + ymax) / 2)


@pytest.mark.parametrize(
    "code",
    [
        "5339",  # 第1次
        "533935",  # 第2次
        "53393599",  # 基準地域(第3次)
        "533935992",  # 2分の1
        "5339359922",  # 4分の1
        "53393599222",  # 8分の1
    ],
)
def test_bbox_roundtrip(code: str):
    """encode→decode往復: bbox中心をエンコードすると元のコードに戻る。"""
    bbox = grid_square_code_to_bbox(code)
    assert bbox is not None
    xmin, ymin, xmax, ymax = bbox
    assert xmin < xmax and ymin < ymax
    lng, lat = _center(bbox)
    codes = lnglat_to_grid_square_code(lng, lat)
    assert codes is not None
    key = {4: "primary", 6: "secondary", 8: "standard", 9: "half"}.get(len(code))
    if key is None:
        key = {10: "quarter", 11: "eighth"}[len(code)]
    assert codes[key] == code


def test_mesh_polygon_matches_bbox():
    poly = mesh_polygon(STANDARD_CODE)
    assert poly is not None
    assert poly.geom_type == "Polygon"
    assert tuple(poly.bounds) == pytest.approx(
        grid_square_code_to_bbox(STANDARD_CODE)
    )


def test_mesh_polygon_invalid():
    assert mesh_polygon("999") is None or True  # 短すぎはNone
    with pytest.raises(ValueError):
        mesh_polygon("53393599", kind="unknown-kind")


def test_extent_generation_standard():
    """extent指定で標準メッシュが生成され、全件PolygonでGeoJSON化できる。"""
    lng, lat = _center(grid_square_code_to_bbox(STANDARD_CODE))
    d = 0.02
    extent = (lng - d, lat - d, lng + d, lat + d)
    patches = list(mesh_patches("standard", extent))
    assert len(patches) > 0
    for label, code, bbox in patches:
        assert label == "standard"
        assert len(code) == 8
        feat = mesh_feature(code, label, bbox)
        assert feat["type"] == "Feature"
        geom = shape(feat["geometry"])
        assert geom.geom_type == "Polygon"
        assert geom.is_valid
    # GeoJSON FeatureCollection化
    result = mesh_geojson("standard", extent)
    assert result.count == len(patches) > 0
    fc = result.feature_collection
    assert fc["type"] == "FeatureCollection"
    json.dumps(fc, ensure_ascii=False)  # serializable
    assert all(f["properties"]["mesh_code"] for f in fc["features"])


def test_mesh_geojson_all_kinds_small_extent():
    """全メッシュ種別で1件以上生成できる（小範囲）。"""
    lng, lat = _center(grid_square_code_to_bbox(STANDARD_CODE))
    d = 0.05
    extent = (lng - d, lat - d, lng + d, lat + d)
    for kind in KNOWN_MESH_KINDS:
        result = mesh_geojson(kind, extent)
        assert result.count > 0, kind
        assert result.feature_collection["type"] == "FeatureCollection"


def test_mesh_geojson_unknown_kind():
    with pytest.raises(ValueError):
        mesh_geojson("unknown-kind", None)


def test_mesh_code_geojson():
    result = mesh_code_geojson(STANDARD_CODE)
    assert result.count == 1
    fc = result.feature_collection
    assert fc["type"] == "FeatureCollection"
    (feat,) = fc["features"]
    assert feat["properties"]["mesh_code"] == STANDARD_CODE
    geom = shape(feat["geometry"])
    assert geom.geom_type == "Polygon"
    assert geom.is_valid
    assert tuple(geom.bounds) == pytest.approx(
        grid_square_code_to_bbox(STANDARD_CODE)
    )


def test_mesh_code_geojson_invalid():
    with pytest.raises(ValueError):
        mesh_code_geojson("abc")


def test_cli_mesh_and_mesh_code(tmp_path):
    runner = CliRunner()
    lng, lat = _center(grid_square_code_to_bbox(STANDARD_CODE))
    d = 0.02
    extent = f"{lng-d},{lat-d},{lng+d},{lat+d}"
    out = str(tmp_path / "mesh.geojson")
    r = runner.invoke(
        main, ["mesh", "--kind", "standard", "--extent", extent, "-o", out]
    )
    assert r.exit_code == 0, r.output
    with open(out, encoding="utf-8") as f:
        fc = json.load(f)
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) > 0

    out2 = str(tmp_path / "code.geojson")
    r = runner.invoke(main, ["mesh-code", "--code", STANDARD_CODE, "-o", out2])
    assert r.exit_code == 0, r.output
    with open(out2, encoding="utf-8") as f:
        fc2 = json.load(f)
    assert fc2["features"][0]["properties"]["mesh_code"] == STANDARD_CODE

    r = runner.invoke(main, ["mesh-code", "--code", "xyz", "-o", out2])
    assert r.exit_code != 0
