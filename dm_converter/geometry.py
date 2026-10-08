"""ジオメトリ変換（shapely版）

解釈したデータをshapelyジオメトリに変換する。
DM形式の相対座標を絶対座標に変換する。

座標系の違い:
    DM形式: X=北方向, Y=東方向
    GIS: X=東方向(経度方向), Y=北方向(緯度方向)
    → 座標を入れ替える

上流 writer/geometry.py と同等精度:
    円(E3):   円周上の3点から外心・半径を求め、64角形ポリゴン近似
    円弧(E4): 3点円弧を32分割程度で補間したLineString
    面(E1):   中庭線（内輪, zukei_kubun=31）対応
    方向(E6): 方向角（direction_angle）を維持
"""

from __future__ import annotations

import math

from shapely.geometry import LineString, Point, Polygon

from .parser.models import Coordinate, MapSheetInfo, ParsedElement

# 座標値の単位コード → メートルへの除数
_UNIT_DIVISORS = {
    1: 1000.0,  # mm → m
    10: 100.0,  # cm → m
    999: 1.0,  # m → m
}

# 円の多角形近似の分割数（上流 QgsCircle.toPolygon(64) 相当）
CIRCLE_SEGMENTS = 64
# 円弧の補間分割数
ARC_SEGMENTS = 32


def _to_abs_point(coord: Coordinate, map_sheet: MapSheetInfo) -> tuple[float, float]:
    """DM相対座標を絶対GIS座標に変換する。

    DM: X=北, Y=東 → GIS: X=東, Y=北 に入れ替え。
    上流 _to_abs_point と同式（QgsPointXY → タプルに置換）。
    """
    if map_sheet.coord_unit not in _UNIT_DIVISORS:
        raise ValueError(
            f"未対応の座標単位コードです: {map_sheet.coord_unit}（対応: {list(_UNIT_DIVISORS.keys())}）"
        )
    divisor = _UNIT_DIVISORS[map_sheet.coord_unit]
    gis_x = map_sheet.origin_y + coord.y / divisor
    gis_y = map_sheet.origin_x + coord.x / divisor
    return (gis_x, gis_y)


def _circumcenter(
    p1: tuple[float, float], p2: tuple[float, float], p3: tuple[float, float]
) -> tuple[tuple[float, float], float] | None:
    """3点から外心と半径を求める。3点が同一直線上の場合はNone。"""
    (x1, y1), (x2, y2), (x3, y3) = p1, p2, p3
    d = 2.0 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if d == 0.0:
        return None
    sq1 = x1 * x1 + y1 * y1
    sq2 = x2 * x2 + y2 * y2
    sq3 = x3 * x3 + y3 * y3
    cx = (sq1 * (y2 - y3) + sq2 * (y3 - y1) + sq3 * (y1 - y2)) / d
    cy = (sq1 * (x3 - x2) + sq2 * (x1 - x3) + sq3 * (x2 - x1)) / d
    r = math.hypot(x1 - cx, y1 - cy)
    if r == 0.0:
        return None
    return ((cx, cy), r)


def to_polygon_geometry(
    element: ParsedElement, map_sheet: MapSheetInfo
) -> Polygon | None:
    """E1要素からPolygonを生成する。"""
    points = [_to_abs_point(c, map_sheet) for c in element.coordinates]
    if len(points) < 3:
        return None
    try:
        poly = Polygon(points)
    except Exception:
        return None
    if poly.is_empty:
        return None
    return poly


def to_ring_polygon_geometry(
    outer: ParsedElement,
    outer_ms: MapSheetInfo,
    inner_rings: list[tuple[ParsedElement, MapSheetInfo]],
) -> Polygon | None:
    """E1外輪要素と内輪要素リスト（中庭線）からリングポリゴンを生成する。

    外輪リング + 内輪リング（複数可）でPolygonを構築する。
    各要素はそれぞれの MapSheetInfo で座標変換する。
    """
    outer_points = [_to_abs_point(c, outer_ms) for c in outer.coordinates]
    if len(outer_points) < 3:
        return None
    holes = []
    for inner_elem, inner_ms in inner_rings:
        inner_points = [_to_abs_point(c, inner_ms) for c in inner_elem.coordinates]
        if len(inner_points) >= 3:
            holes.append(inner_points)
    try:
        poly = Polygon(outer_points, holes or None)
    except Exception:
        return None
    if poly.is_empty:
        return None
    return poly


def group_ring_polygons(
    elem_pairs: list[tuple[ParsedElement, MapSheetInfo]],
) -> list[tuple[ParsedElement, MapSheetInfo, list[tuple[ParsedElement, MapSheetInfo]]]]:
    """面要素リストを (外輪要素, 外輪ms, [(内輪要素, 内輪ms), ...]) にグループ化する。

    内輪（中庭線, zukei_kubun=31）と外輪を空間包含で対応付ける。
    上流 group_ring_polygons と同等動作（shapely containsで判定）。
    内輪が存在しない場合は空リストを返す（呼び出し元で通常処理にフォールバック）。
    外輪に収まらなかった内輪は (inner_elem, inner_ms, []) として返す（単独ポリゴン扱い）。
    """
    inner_pairs = [(e, ms) for e, ms in elem_pairs if e.zukei_kubun == 31]
    if not inner_pairs:
        return []

    outer_pairs = [(e, ms) for e, ms in elem_pairs if e.zukei_kubun != 31]

    # 内輪のジオメトリを生成（空間包含判定用）
    inner_geoms: list[tuple[ParsedElement, MapSheetInfo, Polygon | None]] = []
    for e, ms in inner_pairs:
        if not e.coordinates:
            continue
        inner_geoms.append((e, ms, to_polygon_geometry(e, ms)))

    result: list[
        tuple[ParsedElement, MapSheetInfo, list[tuple[ParsedElement, MapSheetInfo]]]
    ] = []
    used_indices: set[int] = set()

    for outer_elem, outer_ms in outer_pairs:
        if not outer_elem.coordinates:
            continue
        outer_geom = to_polygon_geometry(outer_elem, outer_ms)
        if outer_geom is None or outer_geom.is_empty:
            result.append((outer_elem, outer_ms, []))
            continue
        matched_indices = [
            idx
            for idx, (_, _, ig) in enumerate(inner_geoms)
            if ig is not None and not ig.is_empty and outer_geom.contains(ig)
        ]
        matched_inners = [(inner_geoms[i][0], inner_geoms[i][1]) for i in matched_indices]
        used_indices.update(matched_indices)
        result.append((outer_elem, outer_ms, matched_inners))

    # 外輪に収まらなかった内輪は単独ポリゴンとして出力
    for idx, (inner_elem, inner_ms, _) in enumerate(inner_geoms):
        if idx not in used_indices:
            result.append((inner_elem, inner_ms, []))

    return result


def to_circle_geometry(
    element: ParsedElement, map_sheet: MapSheetInfo
) -> Polygon | None:
    """E3要素（円）からPolygonを生成する。

    円周上の3点から外心・半径を求め、64角形ポリゴンに近似する
    （上流 QgsCircle.toPolygon(64) 相当）。
    3点未満・3点が同一直線上の場合はNone。
    """
    if len(element.coordinates) < 3:
        return None
    pts = [_to_abs_point(c, map_sheet) for c in element.coordinates[:3]]
    center = _circumcenter(pts[0], pts[1], pts[2])
    if center is None:
        return None
    (cx, cy), r = center
    ring = [
        (
            cx + r * math.cos(2.0 * math.pi * i / CIRCLE_SEGMENTS),
            cy + r * math.sin(2.0 * math.pi * i / CIRCLE_SEGMENTS),
        )
        for i in range(CIRCLE_SEGMENTS)
    ]
    try:
        poly = Polygon(ring)
    except Exception:
        return None
    if poly.is_empty:
        return None
    return poly


def to_arc_geometry(
    element: ParsedElement, map_sheet: MapSheetInfo
) -> LineString | None:
    """E4要素（円弧）からLineStringを生成する。

    円弧上の始点・中間点・終点の3点から円を定め、始点→終点を
    中間点側に32分割で補間したLineStringに近似する
    （上流 QgsCircularString.segmentize() 相当）。
    3点未満・3点が同一直線上の場合はNone。
    """
    if len(element.coordinates) < 3:
        return None
    pts = [_to_abs_point(c, map_sheet) for c in element.coordinates[:3]]
    center = _circumcenter(pts[0], pts[1], pts[2])
    if center is None:
        return None
    (cx, cy), _r = center

    def _angle(p: tuple[float, float]) -> float:
        return math.atan2(p[1] - cy, p[0] - cx)

    a0, a1, a2 = _angle(pts[0]), _angle(pts[1]), _angle(pts[2])

    def _norm(a: float) -> float:
        while a <= -math.pi:
            a += 2.0 * math.pi
        while a > math.pi:
            a -= 2.0 * math.pi
        return a

    # 始点→終点の2方向のうち、中間点を含む側を選ぶ
    ccw = _norm(a2 - a0)  # 反時計回りの符号付き角
    mid_ccw = _norm(a1 - a0)
    if ccw >= 0:
        use_ccw = 0.0 <= mid_ccw <= ccw
    else:
        use_ccw = ccw <= mid_ccw <= 0.0
    sweep = ccw if use_ccw else (-(2.0 * math.pi - ccw) if ccw >= 0 else (2.0 * math.pi + ccw))

    line = []
    for i in range(ARC_SEGMENTS + 1):
        a = a0 + sweep * i / ARC_SEGMENTS
        # 各点の半径は始点・中間点・終点の実半径を線形補間で求める
        # （3点が同一円上にない場合の歪みを抑える）
        t = i / ARC_SEGMENTS
        r0 = math.hypot(pts[0][0] - cx, pts[0][1] - cy)
        r1 = math.hypot(pts[1][0] - cx, pts[1][1] - cy)
        r2 = math.hypot(pts[2][0] - cx, pts[2][1] - cy)
        if t <= 0.5:
            r = r0 + (r1 - r0) * (t * 2.0)
        else:
            r = r1 + (r2 - r1) * ((t - 0.5) * 2.0)
        line.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    try:
        geom = LineString(line)
    except Exception:
        return None
    if geom.is_empty:
        return None
    return geom


def to_line_geometry(
    element: ParsedElement, map_sheet: MapSheetInfo
) -> LineString | None:
    """E2要素からLineStringを生成する。"""
    points = [_to_abs_point(c, map_sheet) for c in element.coordinates]
    if len(points) < 2:
        return None
    try:
        geom = LineString(points)
    except Exception:
        return None
    if geom.is_empty:
        return None
    return geom


def to_point_geometry(
    element: ParsedElement, map_sheet: MapSheetInfo
) -> Point | None:
    """E5・E6・E7要素（点・方向・注記）からPointを生成する。"""
    if not element.coordinates:
        return None
    x, y = _to_abs_point(element.coordinates[0], map_sheet)
    return Point(x, y)


def direction_angle(element: ParsedElement) -> float | None:
    """E6要素から方向角（度）を計算する。

    DM座標系（X=北、Y=東）でY軸（東方向）を0°基準として、
    記号位置→方向点の方位角を度数で返す（第49条第4項）。
    座標が2点未満の場合はNoneを返す。
    上流 direction_angle と同式。

    戻り値の例:
        +Y方向（東）: 0°
        +X方向（北）: 90°
        -Y方向（西）: ±180°
        -X方向（南）: -90°
    """
    if len(element.coordinates) < 2:
        return None
    p1 = element.coordinates[0]
    p2 = element.coordinates[1]
    dx = p2.x - p1.x  # 北成分
    dy = p2.y - p1.y  # 東成分
    return math.degrees(math.atan2(dx, dy))
