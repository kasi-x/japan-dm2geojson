"""DM→GeoJSON変換テスト。

入力は上流の合成DM5件 (/tmp/dmconverter_upstream/tests/data/*.dm) を直接使う。
"""

from __future__ import annotations

import glob
import json
import os

import pytest
from click.testing import CliRunner

from dm_converter import api
from dm_converter.api import (
    ConversionResult,
    convert_dm,
    convert_folder,
    parse_dm_file,
    write_geojson,
)
from dm_converter.cli import main
from dm_converter.geojson_writer import layer_to_feature_collection

DATA_DIR = "/tmp/dmconverter_upstream/tests/data"
DM_FILES = sorted(glob.glob(os.path.join(DATA_DIR, "*.dm")))

EXPECTED_EPSG = {
    "synthetic_cs01_500_utf8_lf.dm": 6669,
    "synthetic_cs06_1000_cp932.dm": 6674,
    "synthetic_cs08_2500_oldjis.dm": 6676,
    "synthetic_cs09_1000_utf8_bom.dm": 6677,
    "synthetic_cs12_2500_cp932_rev3.dm": 6680,
}

# 各ファイルに含まれる要素タイプ（生成器由来）
EXPECTED_TYPES = {
    "synthetic_cs01_500_utf8_lf.dm": {"E1", "E2", "E5", "E7"},
    "synthetic_cs06_1000_cp932.dm": {"E1", "E2", "E5", "E6", "E7"},
    "synthetic_cs08_2500_oldjis.dm": {"E1", "E2", "E5", "E7"},
    "synthetic_cs09_1000_utf8_bom.dm": {"E1", "E2", "E5", "E7"},
    "synthetic_cs12_2500_cp932_rev3.dm": {"E1", "E2", "E3", "E4", "E5", "E7"},
}


def _name(path: str) -> str:
    return os.path.basename(path)


@pytest.mark.parametrize("path", DM_FILES, ids=[_name(p) for p in DM_FILES])
def test_convert_no_exception(path: str):
    """全件例外なく変換でき、レイヤ・フィーチャが得られる。"""
    result = convert_dm(path)
    assert isinstance(result, ConversionResult)
    assert result.epsg == EXPECTED_EPSG[_name(path)]
    assert result.layers, "レイヤが1件も生成されない"
    total = sum(len(layer.features) for layer in result.layers)
    assert total > 0, "フィーチャが1件も生成されない"


@pytest.mark.parametrize("path", DM_FILES, ids=[_name(p) for p in DM_FILES])
def test_feature_collections_json_serializable(path: str):
    """全レイヤがGeoJSON FeatureCollection化でき、crs_hintが一致する。"""
    result = convert_dm(path)
    for layer in result.layers:
        fc = layer_to_feature_collection(layer)
        assert fc["type"] == "FeatureCollection"
        assert isinstance(fc["features"], list)
        assert len(fc["features"]) == len(layer.features)
        # json として dump/load できる (tuple/list差異は正規化して比較)
        loaded = json.loads(json.dumps(fc, ensure_ascii=False))
        assert loaded["type"] == fc["type"]
        assert loaded.get("crs_hint") == {"epsg": result.epsg}
        assert len(loaded["features"]) == len(fc["features"])
        for feat in loaded["features"]:
            assert feat["type"] == "Feature"
            assert feat["geometry"]["type"] in (
                "Point",
                "LineString",
                "Polygon",
                "MultiPoint",
                "MultiLineString",
                "MultiPolygon",
            )
            assert "HCODE2" in feat["properties"]

def test_element_types_covered():
    """E1-E7全タイプが5件の合算で網羅される。"""
    covered: set[str] = set()
    for path in DM_FILES:
        parsed = parse_dm_file(path)
        for group in parsed.groups:
            for elem in group.elements:
                covered.add(elem.element_type)
        assert {e.element_type for g in parsed.groups for e in g.elements} == (
            EXPECTED_TYPES[_name(path)]
        )
    assert covered == {"E1", "E2", "E3", "E4", "E5", "E6", "E7"}


def test_circles_and_arcs():
    """rev3ファイルの円(E3)は多角形近似Polygon、円弧(E4)はLineStringになる。"""
    result = convert_dm(os.path.join(DATA_DIR, "synthetic_cs12_2500_cp932_rev3.dm"))
    by_kind = {}
    for layer in result.layers:
        by_kind.setdefault(layer.geom_kind, []).extend(layer.features)
    assert "円" in by_kind and len(by_kind["円"]) == 3
    for feat in by_kind["円"]:
        assert feat.geometry.geom_type == "Polygon"
        assert len(feat.geometry.exterior.coords) > 8
    assert "円弧" in by_kind and len(by_kind["円弧"]) == 2
    for feat in by_kind["円弧"]:
        assert feat.geometry.geom_type == "LineString"
        assert len(feat.geometry.coords) > 2


def test_courtyard_ring():
    """rev3ファイルの中庭線が内輪(inner ring)付きPolygonになる。"""
    result = convert_dm(os.path.join(DATA_DIR, "synthetic_cs12_2500_cp932_rev3.dm"))
    polys = [
        feat.geometry
        for layer in result.layers
        if layer.geom_kind == "面"
        for feat in layer.features
        if feat.geometry.geom_type == "Polygon"
    ]
    assert polys, "面ポリゴンが存在しない"
    assert any(len(p.interiors) >= 1 for p in polys), "内輪付きポリゴンが存在しない"


def test_3d_elements():
    """BOMファイルに3D要素(data区分3)が含まれる。"""
    parsed = parse_dm_file(os.path.join(DATA_DIR, "synthetic_cs09_1000_utf8_bom.dm"))
    elems = [e for g in parsed.groups for e in g.elements]
    assert any(e.data_kubun == 3 for e in elems)
    assert any(any(c.z != 0 for c in e.coordinates) for e in elems)


def test_old_jis_decoded():
    """old_jisファイルの図郭名称が復号される。"""
    parsed = parse_dm_file(os.path.join(DATA_DIR, "synthetic_cs08_2500_oldjis.dm"))
    assert parsed.mesh_info.map_name == "合成図郭Ｄ"
    assert parsed.mesh_info.coordinate_system == 8


def test_utf8_bom_and_rev3_and_meters():
    """BOM・rev3・m単位ファイルの座標系と図郭名称。"""
    bom = parse_dm_file(os.path.join(DATA_DIR, "synthetic_cs09_1000_utf8_bom.dm"))
    assert bom.mesh_info.coordinate_system == 9  # Iレコード由来
    assert bom.mesh_info.map_name == "合成図郭Ｂ"
    rev3 = parse_dm_file(os.path.join(DATA_DIR, "synthetic_cs12_2500_cp932_rev3.dm"))
    assert rev3.mesh_info.coordinate_system == 12
    meters = parse_dm_file(os.path.join(DATA_DIR, "synthetic_cs01_500_utf8_lf.dm"))
    assert meters.map_sheet.coord_unit == 999
    assert meters.mesh_info.coordinate_system == 1


def test_convert_folder_merges(tmp_path):
    """同一座標系フォルダはマージ変換される。"""
    import shutil

    src = os.path.join(DATA_DIR, "synthetic_cs01_500_utf8_lf.dm")
    for i in range(2):
        shutil.copy(src, tmp_path / f"copy{i}.dm")
    result = convert_folder(str(tmp_path))
    assert len(result.source_files) == 2
    single = convert_dm(src)
    assert sum(len(layer.features) for layer in result.layers) == 2 * sum(
        len(layer.features) for layer in single.layers
    )


def test_convert_folder_mixed_systems():
    """座標系混在フォルダはValueError。"""
    with pytest.raises(ValueError):
        convert_folder(DATA_DIR)


def test_annotation_and_direction_hints():
    """注記の表示ヒント属性と方向角がpropertiesに残る。"""
    result = convert_dm(os.path.join(DATA_DIR, "synthetic_cs06_1000_cp932.dm"))
    notes = [
        feat for layer in result.layers
        if layer.element_type == "E7" for feat in layer.features
    ]
    assert notes, "注記フィーチャが存在しない"
    assert any("注記内容" in f.properties and f.properties["注記内容"] for f in notes)
    sample = next(f for f in notes if f.properties.get("注記内容"))
    assert isinstance(sample.properties.get("字の大きさ"), (int, float))
    assert isinstance(sample.properties.get("文字列の方向"), (int, float))
    assert isinstance(sample.properties.get("縦横区分"), int)

    directions = [
        feat for layer in result.layers
        if layer.element_type == "E6" for feat in layer.features
    ]
    assert directions, "方向フィーチャが存在しない"
    assert all(
        f.properties.get("方向角") is None
        or isinstance(f.properties["方向角"], (int, float))
        for f in directions
    )


def test_hcode2_format():
    """HCODE2 = 分類コード4桁 + 図形区分2桁。"""
    result = convert_dm(os.path.join(DATA_DIR, "synthetic_cs06_1000_cp932.dm"))
    for layer in result.layers:
        for feat in layer.features:
            hcode2 = feat.properties.get("HCODE2")
            assert isinstance(hcode2, str) and len(hcode2) == 6
            assert hcode2[:4] == feat.properties.get("分類コード")


def test_write_geojson_roundtrip(tmp_path):
    """write_geojsonで1レイヤ1ファイル出力し、全件json.loadできる。"""
    result = convert_dm(os.path.join(DATA_DIR, "synthetic_cs06_1000_cp932.dm"))
    written = write_geojson(result, str(tmp_path))
    assert len(written) == len(result.layers) == len({*written})
    assert all(p.endswith(".geojson") for p in written)
    for path in written:
        with open(path, encoding="utf-8") as f:
            fc = json.load(f)
        assert fc["type"] == "FeatureCollection"
        assert fc.get("crs_hint") == {"epsg": result.epsg}


def test_convert_bytes():
    """バイト列入力でも変換できる。"""
    with open(os.path.join(DATA_DIR, "synthetic_cs01_500_utf8_lf.dm"), "rb") as f:
        data = f.read()
    result = convert_dm(data)
    assert result.epsg == 6669
    assert sum(len(layer.features) for layer in result.layers) > 0


def test_convert_folder_empty(tmp_path):
    with pytest.raises(FileNotFoundError):
        convert_folder(str(tmp_path))


def test_cli_convert_single_and_info(tmp_path):
    """CLI: convert(単体)とinfoが動作する。"""
    runner = CliRunner()
    dm = os.path.join(DATA_DIR, "synthetic_cs01_500_utf8_lf.dm")
    out = tmp_path / "out"
    r = runner.invoke(main, ["convert", dm, "-o", str(out)])
    assert r.exit_code == 0, r.output
    assert list(out.glob("*.geojson")), "GeoJSONが出力されない"
    r = runner.invoke(main, ["info", dm])
    assert r.exit_code == 0, r.output
    assert "EPSG:6669" in r.output
    assert "E1" in r.output


def test_cli_convert_folder(tmp_path):
    """CLI: convert(フォルダ)は座標系混在で失敗する(単一系フォルダのみ対応)。"""
    runner = CliRunner()
    r = runner.invoke(main, ["convert", DATA_DIR, "-o", str(tmp_path / "o")])
    assert r.exit_code != 0  # 5件は座標系が混在するため


def test_summary_mentions_layers():
    result = convert_dm(os.path.join(DATA_DIR, "synthetic_cs01_500_utf8_lf.dm"))
    assert "EPSG:6669" in result.summary
    assert "合計フィーチャ" in result.summary


def test_public_api_surface():
    for name in (
        "convert_dm",
        "convert_folder",
        "mesh_geojson",
        "mesh_code_geojson",
        "write_geojson",
        "parse_dm_file",
        "parse_dm_bytes",
    ):
        assert callable(getattr(api, name)), name


def test_to_epsg_4326_reprojects():
    """to_epsg=4326で緯度経度化され、crs_hintと属性が維持される。"""
    from pyproj import Transformer

    src = os.path.join(DATA_DIR, "synthetic_cs12_2500_cp932_rev3.dm")
    plain = convert_dm(src)
    assert plain.epsg == 6680
    reproj = convert_dm(src, to_epsg=4326)
    assert reproj.epsg == 4326
    assert len(reproj.layers) == len(plain.layers)

    transformer = Transformer.from_crs(6680, 4326, always_xy=True)
    for src_layer, dst_layer in zip(plain.layers, reproj.layers):
        assert dst_layer.epsg == 4326
        assert len(dst_layer.features) == len(src_layer.features)
        assert [f.properties for f in dst_layer.features] == [
            f.properties for f in src_layer.features
        ]
        for s, d in zip(src_layer.features, dst_layer.features):
            sx, sy = s.geometry.coords[0] if s.geometry.geom_type == "Point" else s.geometry.centroid.coords[0]
            dx, dy = d.geometry.coords[0] if d.geometry.geom_type == "Point" else d.geometry.centroid.coords[0]
            ex, ey = transformer.transform(sx, sy)
            assert dx == pytest.approx(ex) and dy == pytest.approx(ey)
            if d.geometry.geom_type == "Point":
                assert -180 <= dx <= 180 and -90 <= dy <= 90

    fc = layer_to_feature_collection(reproj.layers[0])
    assert fc["crs_hint"] == {"epsg": 4326}


def test_cli_convert_to_epsg(tmp_path):
    """CLI: convert --to-epsg 4326 で緯度経度GeoJSONが出る。"""
    import json

    runner = CliRunner()
    dm = os.path.join(DATA_DIR, "synthetic_cs06_1000_cp932.dm")
    out = str(tmp_path / "wgs")
    r = runner.invoke(main, ["convert", dm, "-o", out, "--to-epsg", "4326"])
    assert r.exit_code == 0, r.output
    assert "EPSG:4326" in r.output
    d = json.load(open(sorted(glob.glob(os.path.join(out, "*.geojson")))[0]))
    assert d["crs_hint"] == {"epsg": 4326}
