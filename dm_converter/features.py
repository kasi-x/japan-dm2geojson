"""レイヤ分け（ParsedDM → LayerFeatures）

上流 writer/writer.py の create_merged_layers 相当をQGIS非依存化したもの。
QgsVectorLayer の代わりに LayerFeatures dataclass を返す。

レイヤ分け命名規則（上流維持）:
    - 4桁分類コード × ジオメトリタイプ（面/線/円/円弧/点/方向/注記）で分割
    - レイヤ名: データ名（例: "道路縁(街区線)_線"）、データ名==タイプ名ならそのまま
    - 名前衝突時: 親グループ名をprefix（例: "応用測量整飾_方位_線"）、
      同一親内でも衝突する場合は4桁コードをsuffix
    - HCODE2 = 分類コード4桁 + 図形区分2桁

QGIS互換再現の落とし所:
    QML適用・注記ラベル・QLR出力はQGIS外では適用不可のため、
    注記/方向の表示ヒント属性（字の大きさ=0.1mm単位、文字列の方向=度、
    縦横区分、方向角）をpropertiesに残し、QML/QLR用メタは出さない。
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from typing import Callable

from shapely.geometry.base import BaseGeometry

from .constants import CLASSIFICATIONS, get_classification_name
from .crs import get_epsg
from .geometry import (
    direction_angle,
    group_ring_polygons,
    to_arc_geometry,
    to_circle_geometry,
    to_line_geometry,
    to_point_geometry,
    to_polygon_geometry,
    to_ring_polygon_geometry,
)
from .parser.models import MapSheetInfo, ParsedDM, ParsedElement

# 要素タイプ → (ジオメトリタイプ名, ジオメトリ変換関数)
# 上流 schema.ELEMENT_TYPE_MAP からQGIS依存（WKBタイプ）を除いたもの
ELEMENT_TYPE_MAP: dict[str, tuple[str, Callable[[ParsedElement, MapSheetInfo], BaseGeometry | None]]] = {
    "E1": ("面", to_polygon_geometry),
    "E2": ("線", to_line_geometry),
    "E3": ("円", to_circle_geometry),
    "E4": ("円弧", to_arc_geometry),
    "E5": ("点", to_point_geometry),
    "E6": ("方向", to_point_geometry),
    "E7": ("注記", to_point_geometry),
}

# ParsedElement属性名 → 日本語属性名（上流 schema.FIELD_DEFS から型情報を除いたもの）
FIELD_DEFS: list[tuple[str, str]] = [
    ("element_type", "レコードタイプ"),
    ("dm_code", "分類コード"),
    ("chiiki_bunrui", "地域分類"),
    ("jouhou_bunrui", "情報分類"),
    ("element_id", "要素識別番号"),
    ("hierarchy", "階層レベル"),
    ("zukei_kubun", "図形区分"),
    ("data_kubun", "実データ区分"),
    ("seido_kubun", "精度区分"),
    ("chuki_kubun", "注記区分"),
    ("teni", "転位区分"),
    ("kandan", "間断区分"),
    ("attribute_value", "属性数値"),
    ("zokusei_kubun", "属性区分"),
    ("acquired_date", "取得年月"),
    ("updated_date", "更新取得年月"),
    ("deleted_date", "消去年月"),
]

# 注記固有フィールド（E7のみ。上流 schema.ANNOTATION_FIELD_DEFS相当）
ANNOTATION_FIELD_DEFS: list[tuple[str, str]] = [
    ("text", "注記内容"),
    ("orientation", "縦横区分"),
    ("size", "字の大きさ"),
    ("spacing", "字隔"),
    ("angle", "文字列の方向"),
    ("line_weight", "線号"),
]

# 方向固有フィールド（E6のみ。上流 schema.DIRECTION_FIELD_DEFS相当）
DIRECTION_FIELD_DEFS: list[str] = [
    "方向角",
]


@dataclass
class FeatureItem:
    """1フィーチャ（shapelyジオメトリ + 属性dict）。"""

    geometry: BaseGeometry
    properties: dict


@dataclass
class LayerFeatures:
    """1レイヤ分のフィーチャ群（= 1 GeoJSON FeatureCollection の出力単位）。"""

    name: str
    element_type: str  # "E1"-"E7"
    dm_code: str  # 4桁分類コード
    geom_kind: str  # 上流ジオメトリタイプ名（面/線/円/円弧/点/方向/注記）
    epsg: int | None  # 元CRSのEPSG（不明時はNone）
    features: list[FeatureItem] = field(default_factory=list)


@dataclass
class MergedLayers:
    """create_merged_layers の戻り値（上流 MergeResult 相当）。"""

    layers: list[LayerFeatures] = field(default_factory=list)
    geom_fail_counter: Counter = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)
    layer_parent_codes: dict[str, str] = field(default_factory=dict)
    layer_element_types: dict[str, str] = field(default_factory=dict)


def _get_layer_name(layer_code: str) -> str:
    """4桁分類コードからレイヤ名を返す（上流と同ロジック）。"""
    parent_code = layer_code[:2]
    data_code = layer_code[2:]
    group = CLASSIFICATIONS.get(parent_code)
    if group is None:
        return layer_code
    if data_code == "00":
        return group["name"]
    data_name = group.get(data_code)
    if data_name is None:
        return layer_code
    return data_name


def _build_properties(elem: ParsedElement, is_annotation: bool, is_direction: bool) -> dict:
    """要素から属性dictを構築する（日本語属性名を維持）。"""
    props: dict = {}
    for attr_name, field_name in FIELD_DEFS:
        props[field_name] = getattr(elem, attr_name)
    name = get_classification_name(elem.dm_code)
    props["分類名"] = None if name == elem.dm_code else name
    props["HCODE2"] = elem.dm_code + f"{elem.zukei_kubun:02d}"

    # 注記固有フィールド（E7のみ）
    if is_annotation and elem.annotation is not None:
        for attr_name, field_name in ANNOTATION_FIELD_DEFS:
            props[field_name] = getattr(elem.annotation, attr_name)

    # 方向固有フィールド（E6のみ）
    if is_direction:
        for field_name in DIRECTION_FIELD_DEFS:
            props[field_name] = direction_angle(elem)

    return props


def create_layers(dm: ParsedDM) -> list[LayerFeatures]:
    """ParsedDM から4桁コード単位のレイヤ群を作成する。"""
    return create_merged_layers([dm]).layers


def create_merged_layers(dm_list: list[ParsedDM]) -> MergedLayers:
    """複数ParsedDMからレイヤをマージして作成する。

    同じ分類コード4桁×ジオメトリタイプのフィーチャは1つのレイヤに統合される。
    CRSは最初のParsedDMの座標系を使用する。
    各要素のジオメトリ変換にはそれぞれのファイルのmap_sheetを使用する。
    """
    if not dm_list:
        return MergedLayers()

    first_dm = dm_list[0]
    if first_dm.mesh_info.coordinate_system is not None:
        epsg: int | None = get_epsg(first_dm.mesh_info.coordinate_system)
    else:
        epsg = None

    # (layer_code, geom_type_name) → [(element, map_sheet)]
    groups: dict[tuple[str, str], list[tuple[ParsedElement, MapSheetInfo]]] = defaultdict(list)

    for dm in dm_list:
        for group in dm.groups:
            for elem in group.elements:
                type_info = ELEMENT_TYPE_MAP.get(elem.element_type)
                if type_info is None:
                    continue
                geom_type_name = type_info[0]
                layer_code = elem.dm_code
                groups[(layer_code, geom_type_name)].append((elem, dm.map_sheet))

    layers: list[LayerFeatures] = []
    geom_fail_counter: Counter = Counter()
    errors: list[str] = []
    layer_parent_codes: dict[str, str] = {}
    layer_element_types: dict[str, str] = {}

    # 衝突するレイヤ名を事前検出
    _name_counts: Counter = Counter()
    for layer_code, geom_type_name in groups:
        g = _get_layer_name(layer_code)
        _name_counts[g if g == geom_type_name else f"{g}_{geom_type_name}"] += 1
    _conflicting_names: set[str] = {n for n, c in _name_counts.items() if c > 1}

    for (layer_code, geom_type_name), elem_pairs in groups.items():
        first_elem = elem_pairs[0][0]
        type_info = ELEMENT_TYPE_MAP[first_elem.element_type]
        geom_func = type_info[1]

        # 要素タイプ別フラグ
        is_annotation = first_elem.element_type == "E7"
        is_direction = first_elem.element_type == "E6"

        # レイヤ名: "道路_線", "建物_点", "基準点_注記" など
        data_name = _get_layer_name(layer_code)

        if data_name == geom_type_name:
            layer_name = data_name
        else:
            layer_name = f"{data_name}_{geom_type_name}"

        # 衝突する場合は親グループ名をプレフィックスに付けて一意化
        # 例: "方位_線" → "応用測量整飾_方位_線" / "測量記録等_方位_線"
        # 同一親グループ内で衝突する場合はさらに4桁コードをサフィックスに付ける
        # 例: "測量記録等_測点名称_注記" → "測量記録等_測点名称_注記_8221"
        if layer_name in _conflicting_names:
            parent_name = CLASSIFICATIONS.get(layer_code[:2], {}).get(
                "name", layer_code[:2]
            )
            layer_name = f"{parent_name}_{layer_name}"
        if layer_name in layer_parent_codes:
            layer_name = f"{layer_name}_{layer_code}"

        # フィーチャ追加
        # 面(E1)に中庭線（内輪, zukei_kubun=31）が含まれる場合はリングポリゴンに変換する
        features: list[FeatureItem] = []
        ring_groups = group_ring_polygons(elem_pairs) if geom_type_name == "面" else []

        # (elem, geom) ペアのリストを構築
        elem_geom_pairs: list[tuple[ParsedElement, BaseGeometry]] = []
        if ring_groups:
            for outer_elem, outer_ms, inner_elems in ring_groups:
                try:
                    geom = (
                        to_ring_polygon_geometry(outer_elem, outer_ms, inner_elems)
                        if inner_elems
                        else geom_func(outer_elem, outer_ms)
                    )
                except Exception as e:
                    geom_fail_counter[
                        (outer_elem.element_type, outer_elem.dm_code)
                    ] += 1
                    errors.append(
                        f"{outer_elem.element_type} {outer_elem.dm_code} "
                        f"要素ID={outer_elem.element_id}: {e}"
                    )
                    continue
                if geom is None or geom.is_empty:
                    geom_fail_counter[
                        (outer_elem.element_type, outer_elem.dm_code)
                    ] += 1
                    continue
                elem_geom_pairs.append((outer_elem, geom))
        else:
            for elem, map_sheet in elem_pairs:
                if not elem.coordinates:
                    continue
                if is_direction and len(elem.coordinates) > 2:
                    # E6で座標ペアが複数ある場合: ペアごとに複数フィーチャを生成
                    coords = elem.coordinates
                    if len(coords) % 2 != 0:
                        errors.append(
                            f"{elem.element_type} {elem.dm_code} "
                            f"要素ID={elem.element_id}: "
                            "座標数が奇数のため末尾1点を無視します"
                        )
                    for i in range(0, len(coords) - 1, 2):
                        pair_coords = coords[i : i + 2]
                        pair_index = (i // 2) + 1
                        pair_elem = replace(elem, coordinates=pair_coords)
                        try:
                            geom = geom_func(pair_elem, map_sheet)
                        except Exception as e:
                            geom_fail_counter[(elem.element_type, elem.dm_code)] += 1
                            errors.append(
                                f"{elem.element_type} {elem.dm_code} "
                                f"要素ID={elem.element_id} "
                                f"pair #{pair_index} 座標={pair_coords}: {e}"
                            )
                            continue
                        if geom is None or geom.is_empty:
                            geom_fail_counter[(elem.element_type, elem.dm_code)] += 1
                            continue
                        elem_geom_pairs.append((pair_elem, geom))
                else:
                    try:
                        geom = geom_func(elem, map_sheet)
                    except Exception as e:
                        geom_fail_counter[(elem.element_type, elem.dm_code)] += 1
                        errors.append(
                            f"{elem.element_type} {elem.dm_code} "
                            f"要素ID={elem.element_id}: {e}"
                        )
                        continue
                    if geom is None or geom.is_empty:
                        geom_fail_counter[(elem.element_type, elem.dm_code)] += 1
                        continue
                    elem_geom_pairs.append((elem, geom))

        for elem, geom in elem_geom_pairs:
            props = _build_properties(elem, is_annotation, is_direction)
            features.append(FeatureItem(geometry=geom, properties=props))

        layers.append(
            LayerFeatures(
                name=layer_name,
                element_type=first_elem.element_type,
                dm_code=layer_code,
                geom_kind=geom_type_name,
                epsg=epsg,
                features=features,
            )
        )
        layer_parent_codes[layer_name] = layer_code[:2]
        layer_element_types[layer_name] = first_elem.element_type

    return MergedLayers(
        layers=layers,
        geom_fail_counter=geom_fail_counter,
        errors=errors,
        layer_parent_codes=layer_parent_codes,
        layer_element_types=layer_element_types,
    )


# 要素タイプ名マッピング（上流 log_writer._TYPE_NAMES 相当）
_TYPE_NAMES = {
    "E1": "面",
    "E2": "線",
    "E3": "円",
    "E4": "円弧",
    "E5": "点",
    "E6": "方向",
    "E7": "注記",
    "E8": "属性",
    "G": "グリッド",
    "T": "不整三角網",
}

SUPPORTED_TYPES = frozenset(ELEMENT_TYPE_MAP.keys())


def summarize(merged: MergedLayers) -> str:
    """変換結果のサマリーをテキストで返す（上流 log_writer 相当の簡易版）。

    ファイル出力は行わず文字列を返す。ファイルへの書き出しは呼び出し元で行う。
    """
    lines = ["=== DM変換サマリー ==="]

    total = sum(len(layer.features) for layer in merged.layers)
    lines.append(f"出力レイヤ数: {len(merged.layers)}")
    lines.append(f"合計フィーチャ: {total}件")
    lines.append("")
    lines.append("--- レイヤ別 ---")
    for layer in merged.layers:
        type_name = _TYPE_NAMES.get(layer.element_type, layer.element_type)
        lines.append(
            f"  {layer.name} ({layer.element_type}/{type_name}/{layer.geom_kind}): "
            f"{len(layer.features)}件"
        )

    geom_fail_total = sum(merged.geom_fail_counter.values())
    lines.extend(
        [
            "",
            f"ジオメトリ変換失敗: {geom_fail_total}件",
        ]
    )
    if merged.geom_fail_counter:
        lines.append("--- ジオメトリ変換失敗詳細 ---")
        for (et, dm_code), count in sorted(merged.geom_fail_counter.items()):
            name = get_classification_name(dm_code)
            label = f"{name}" if name != dm_code else dm_code
            lines.append(f"  {et} {dm_code}({label}): {count}件")

    if merged.errors:
        lines.extend(["", "--- エラー ---"])
        for err in merged.errors:
            lines.append(f"  {err}")

    return "\n".join(lines) + "\n"
