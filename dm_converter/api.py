"""DM→GeoJSON変換の高水準API。

QGIS不要の純Python実装。ファイル/バイト列のDMデータを読み込み、
パース→レイヤ分け→GeoJSON FeatureCollection化までを一括で行う。
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field
from typing import Iterator, Sequence
from .crs import get_epsg
from .features import LayerFeatures, MergedLayers, create_merged_layers
from .geojson_writer import write_layer_geojson
from .reproject import reproject_layer
from .mesh.mesh_features import (
    KNOWN_KINDS,
    mesh_feature,
    mesh_patches,
    mesh_polygon,
)
from .parser.classifier import classify
from .parser.models import ParsedDM
from .parser.parser import parse
from .parser.reader import (
    detect_encoding,
    read_dmi_coordinate_system,
    read_records,
)

__all__ = [
    "ConversionResult",
    "MeshResult",
    "convert_dm",
    "convert_folder",
    "convert_files",
    "mesh_geojson",
    "mesh_code_geojson",
    "write_geojson",
    "parse_dm_file",
    "parse_dm_bytes",
    "KNOWN_MESH_KINDS",
]

KNOWN_MESH_KINDS: tuple[str, ...] = KNOWN_KINDS


@dataclass
class ConversionResult:
    """convert_dm / convert_folder の戻り値。"""

    layers: list = field(default_factory=list)  # list[LayerFeatures]
    warnings: list[str] = field(default_factory=list)
    epsg: int | None = None
    source_files: tuple[str, ...] = ()

    @property
    def summary(self) -> str:
        """人間可読の変換サマリ。"""
        lines = [
            f"EPSG:{self.epsg}" if self.epsg else "EPSG:不明",
            f"入力ファイル: {len(self.source_files)}件",
        ]
        total = 0
        for layer in self.layers:
            n = len(layer.features)
            total += n
            lines.append(f"  {layer.name} ({layer.geom_kind}): {n}件")
        lines.append(f"合計フィーチャ: {total}件")
        if self.warnings:
            lines.append(f"警告: {len(self.warnings)}件")
            lines.extend(f"  - {w}" for w in self.warnings[:20])
            if len(self.warnings) > 20:
                lines.append(f"  ... 他 {len(self.warnings) - 20}件")
        return "\n".join(lines)


@dataclass
class MeshResult:
    """mesh_geojson / mesh_code_geojson の戻り値。"""

    kind: str
    feature_collection: dict = field(default_factory=dict)
    count: int = 0


def _parse_record_bytes(
    records: Sequence[bytes], encoding: str, dmi_coord_system: int | None = None
) -> ParsedDM:
    return parse(classify(iter(records), encoding), dmi_coord_system=dmi_coord_system)


def parse_dm_bytes(data: bytes, dmi_coord_system: int | None = None) -> ParsedDM:
    """DMバイト列をパースして ParsedDM を返す (convert_dm(path|bytes) の bytes 経路)。"""
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".dm", delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        encoding = detect_encoding(tmp_path)
        return _parse_record_bytes(
            list(read_records(tmp_path)), encoding, dmi_coord_system
        )
    finally:
        os.unlink(tmp_path)


def parse_dm_file(path: str, dmi_coord_system: int | None = None) -> ParsedDM:
    """単一DMファイルをパースして ParsedDM を返す。"""
    encoding = detect_encoding(path)
    return _parse_record_bytes(
        list(read_records(path)), encoding, dmi_coord_system
    )


def _merged_to_result(
    merged: MergedLayers, source_files: Sequence[str], epsg: int | None
) -> ConversionResult:
    return ConversionResult(
        layers=list(merged.layers),
        warnings=list(merged.errors),
        epsg=epsg,
        source_files=tuple(source_files),
    )


def convert_dm(
    source,
    dmi_coord_system: int | None = None,
    to_epsg: int | None = None,
) -> ConversionResult:
    """DMファイル(パス)またはバイト列を変換する。

    Args:
        source: DMファイルパス(str/Path) または DMバイト列(bytes)。
        dmi_coord_system: .dmi由来の座標系番号(1-19)。ファイルパスの場合は
            同フォルダの.dmiから自動取得を試み、引数指定があればそれを優先する。
        to_epsg: 指定時は出力レイヤをこのEPSGに再投影する（例: 4326で緯度経度化）。
            EPSG不明時はValueError。

    Returns:
        ConversionResult(layers, warnings, epsg, source_files)。
    """
    if isinstance(source, (bytes, bytearray)):
        parsed = parse_dm_bytes(bytes(source), dmi_coord_system)
        files: list[str] = ["<bytes>"]
        dmi_name: str | None = None
    else:
        path = os.fspath(source)
        folder = os.path.dirname(os.path.abspath(path))
        dmi_info = read_dmi_coordinate_system(folder)
        if dmi_coord_system is None and dmi_info is not None:
            dmi_coord_system = dmi_info[0]
        parsed = parse_dm_file(path, dmi_coord_system)
        files = [path]
        dmi_name = dmi_info[1] if dmi_info else None

    epsg: int | None = None
    if parsed.mesh_info.coordinate_system is not None:
        epsg = get_epsg(parsed.mesh_info.coordinate_system)
    merged = create_merged_layers([parsed])
    warnings = list(parsed.parse_warnings) + list(merged.errors)
    if not isinstance(source, (bytes, bytearray)) and dmi_name:
        warnings.append(f".dmiから座標系を取得: {dmi_name}")
    layers: list[LayerFeatures] = list(merged.layers)
    out_epsg = epsg
    if to_epsg is not None:
        if epsg is None:
            raise ValueError("座標系不明のため再投影できません")
        layers = [reproject_layer(layer, to_epsg) for layer in layers]
        out_epsg = to_epsg
    return ConversionResult(
        layers=layers,
        warnings=warnings,
        epsg=out_epsg,
        source_files=tuple(files),
    )


def convert_files(
    paths: Sequence[str], to_epsg: int | None = None
) -> ConversionResult:
    """複数DMファイルをマージ変換する(同一座標系前提、.dmi優先)。

    上流アルゴリズムと同様、先頭ファイルのフォルダの.dmiを最優先し、
    座標系が混在する場合は ValueError を送出する。

    Args:
        to_epsg: 指定時は出力レイヤをこのEPSGに再投影する（EPSG不明時はValueError）。
    """
    paths = [os.fspath(p) for p in paths]
    if not paths:
        return ConversionResult()
    folder = os.path.dirname(os.path.abspath(paths[0]))
    dmi_info = read_dmi_coordinate_system(folder)
    dmi_coord_system = dmi_info[0] if dmi_info else None

    parsed_list: list[ParsedDM] = []
    base_cs: int | None = dmi_coord_system
    for path in paths:
        parsed = parse_dm_file(path, dmi_coord_system)
        if dmi_coord_system is None:
            cs = parsed.mesh_info.coordinate_system
            if base_cs is None:
                base_cs = cs
            elif cs != base_cs:
                raise ValueError(
                    f"座標系が混在しています: {paths[0]}系={base_cs}, {path}系={cs}"
                )
        parsed_list.append(parsed)

    epsg = get_epsg(base_cs) if base_cs is not None else None
    merged = create_merged_layers(parsed_list)
    warnings: list[str] = []
    for parsed in parsed_list:
        warnings.extend(parsed.parse_warnings)
    warnings.extend(merged.errors)
    if dmi_info:
        warnings.append(f".dmiから座標系を取得: {dmi_info[1]}")
    layers = list(merged.layers)
    out_epsg = epsg
    if to_epsg is not None:
        if epsg is None:
            raise ValueError("座標系不明のため再投影できません")
        layers = [reproject_layer(layer, to_epsg) for layer in layers]
        out_epsg = to_epsg
    result = _merged_to_result(
        MergedLayers(
            layers=layers,
            geom_fail_counter=merged.geom_fail_counter,
            errors=merged.errors,
            layer_parent_codes=merged.layer_parent_codes,
            layer_element_types=merged.layer_element_types,
        ),
        paths,
        out_epsg,
    )
    result.warnings = warnings
    return result


def convert_folder(folder: str, to_epsg: int | None = None) -> ConversionResult:
    """フォルダ内の *.dm を一括マージ変換する。"""
    dm_files = sorted(glob.glob(os.path.join(folder, "*.dm")))
    if not dm_files:
        raise FileNotFoundError(f"フォルダ内にDMファイルが見つかりません: {folder}")
    return convert_files(dm_files, to_epsg=to_epsg)


def write_geojson(result: ConversionResult, out_dir: str) -> list[str]:
    """ConversionResultの各レイヤを「1レイヤ=1 GeoJSON」で書き出す。

    Returns:
        書き出したファイルパスのリスト。
    """
    os.makedirs(out_dir, exist_ok=True)
    written: list[str] = []
    used: set[str] = set()
    for layer in result.layers:
        safe = "".join(c if c not in '/\\:*?"<>|' else "_" for c in layer.name).strip()
        if not safe:
            safe = "layer"
        stem, suffix = safe, ".geojson"
        i = 1
        while f"{stem}{suffix}" in used:
            i += 1
            stem = f"{safe}_{i}"
        used.add(f"{stem}{suffix}")
        written.append(write_layer_geojson(layer, os.path.join(out_dir, f"{stem}{suffix}")))
    return written


def mesh_geojson(
    kind: str,
    extent: tuple[float, float, float, float] | None = None,
) -> MeshResult:
    """指定メッシュ種別・範囲のメッシュをGeoJSON FeatureCollection化する。

    Args:
        kind: KNOWN_MESH_KINDS のいずれか。
        extent: WGS84の(xmin, ymin, xmax, ymax)。Noneで全域。
    """
    if kind not in KNOWN_KINDS:
        raise ValueError(f"未知のメッシュ種別です: {kind}（対応: {KNOWN_KINDS}）")
    features = [
        mesh_feature(code, label, bbox)
        for label, code, bbox in mesh_patches(kind, extent)
    ]
    return MeshResult(
        kind=kind,
        feature_collection={"type": "FeatureCollection", "features": features},
        count=len(features),
    )


def mesh_code_geojson(code: str, kind: str | None = None) -> MeshResult:
    """メッシュコード単体のポリゴンをGeoJSON FeatureCollection化する。"""
    polygon = mesh_polygon(code, kind)
    if polygon is None:
        raise ValueError(f"メッシュコードを解釈できません: {code!r}")
    label = kind or f"len{len(code)}"
    fc = {
        "type": "FeatureCollection",
        "features": [mesh_feature(code, label, polygon)],
    }
    return MeshResult(kind=label, feature_collection=fc, count=1)


def iter_dm_files(path: str) -> Iterator[str]:
    """単体DMパスまたはフォルダからDMファイル列挙(フォルダは*.dmソート順)。"""
    if os.path.isdir(path):
        files = sorted(glob.glob(os.path.join(path, "*.dm")))
        if not files:
            raise FileNotFoundError(f"フォルダ内にDMファイルが見つかりません: {path}")
        yield from files
    else:
        yield path
