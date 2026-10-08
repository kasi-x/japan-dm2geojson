"""メッシュのshapely/GeoJSONヘルパー

上流 japanese_grids/algorithms/utils（grid_square.py / legacy_grid.py /
gridsquare_to_box.py）の上に、以下を追加する:

    - bbox → shapely Polygon 変換
    - (code, kind, polygon) イテレータ
    - GeoJSON Feature / FeatureCollection 化ヘルパー

座標の扱い:
    - grid_square系（primary/secondary/standard/half/quarter/eighth/
      double/quintuple/m100）は WGS84 経緯度（度）。
    - legacy系（lv50000/lv5000/lv2500/lv1000/lv500）は平面直角座標系の
      メートル値（系番号つき。上流 create_legacy_grid.py と同様、
      平面直角I系〜XIX系を全系走査し、最初にヒットした系を使う）。
      WGS84 extent が渡された場合は全19系に変換して走査する。
"""

from __future__ import annotations

from typing import Iterator, Optional

from pyproj import Transformer
from shapely.geometry import Polygon, mapping

from . import grid_square, legacy_grid
from .gridsquare_to_box import grid_square_code_to_bbox

# grid_square系メッシュ種別
_GRID_SQUARE_KINDS: tuple[str, ...] = (
    "primary",
    "secondary",
    "standard",
    "half",
    "quarter",
    "eighth",
    "double",
    "quintuple",
    "m100",
)

# legacy系メッシュ種別
_LEGACY_KINDS: tuple[str, ...] = (
    "lv50000",
    "lv5000",
    "lv2500",
    "lv1000",
    "lv500",
)

KNOWN_KINDS: tuple[str, ...] = _GRID_SQUARE_KINDS + _LEGACY_KINDS

# 標準地域メッシュのコード長 → kindラベル
_CODE_LENGTH_KINDS: dict[int, str] = {
    4: "primary",
    6: "secondary",
    8: "standard",
    9: "half",
    10: "quarter",
    11: "eighth",
}

# 平面直角座標系番号 → JGD2011 EPSG（1系=6669 … 19系=6687）
_PLANE_EPSG_JGD2011 = {n: 6668 + n for n in range(1, 20)}

_transformers: dict[tuple[int, int], Transformer] = {}


def _transformer(from_epsg: int, to_epsg: int) -> Transformer:
    key = (from_epsg, to_epsg)
    t = _transformers.get(key)
    if t is None:
        t = Transformer.from_crs(from_epsg, to_epsg, always_xy=True)
        _transformers[key] = t
    return t


def _wgs84_extent_to_plane(
    extent: tuple[float, float, float, float], plane_no: int
) -> tuple[float, float, float, float]:
    """WGS84のextentを平面直角plane_no系のbboxに変換する。"""
    t = _transformer(4326, _PLANE_EPSG_JGD2011[plane_no])
    xmin, ymin = t.transform(extent[0], extent[1])
    xmax, ymax = t.transform(extent[2], extent[3])
    return (min(xmin, xmax), min(ymin, ymax), max(xmin, xmax), max(ymin, ymax))


def _plane_bbox_to_wgs84(
    bbox: tuple[float, float, float, float], plane_no: int
) -> tuple[float, float, float, float]:
    """平面直角plane_no系のbboxをWGS84に変換する。"""
    t = _transformer(_PLANE_EPSG_JGD2011[plane_no], 4326)
    xmin, ymin = t.transform(bbox[0], bbox[1])
    xmax, ymax = t.transform(bbox[2], bbox[3])
    return (min(xmin, xmax), min(ymin, ymax), max(xmin, xmax), max(ymin, ymax))


def _plane_code_prefix(plane_no: int) -> str:
    """平面直角系番号 → コードprefix（2桁ゼロ埋め）。"""
    return f"{plane_no:02d}"


def bbox_to_polygon(bbox: tuple[float, float, float, float]) -> Polygon:
    """bbox (xmin, ymin, xmax, ymax) → shapely Polygon。"""
    xmin, ymin, xmax, ymax = bbox
    return Polygon(
        [
            (xmin, ymin),
            (xmin, ymax),
            (xmax, ymax),
            (xmax, ymin),
            (xmin, ymin),
        ]
    )


def mesh_patches(
    kind: str, extent: tuple[float, float, float, float] | None = None
) -> Iterator[tuple[str, str, tuple[float, float, float, float]]]:
    """指定メッシュ種別の (kind_label, code, bbox_lnglat) を列挙する。

    Args:
        kind: KNOWN_KINDS のいずれか。
        extent: WGS84の (xmin, ymin, xmax, ymax)。Noneで全域。

    Yields:
        (kind_label, code, bbox) のタプル。bboxはWGS84経緯度。

    Raises:
        ValueError: 未知の種別の場合。
    """
    if kind in _GRID_SQUARE_KINDS:
        yield from grid_square.iter_patch(extent=extent, **{kind: True})
        return
    if kind in _LEGACY_KINDS:
        if extent is None:
            # 全域走査はlv1000/lv500で爆発するため、他種別と同様に
            # 上流にならい全系を対象とする。呼び出し元でextent指定推奨。
            for plane_no in range(1, 20):
                prefix = _plane_code_prefix(plane_no)
                for label, code, bbox in legacy_grid.iter_patch(
                    extent=None, **{kind: True}
                ):
                    yield (
                        label,
                        prefix + code,
                        _plane_bbox_to_wgs84(bbox, plane_no),
                    )
            return
        for plane_no in range(1, 20):
            plane_extent = _wgs84_extent_to_plane(extent, plane_no)
            prefix = _plane_code_prefix(plane_no)
            for label, code, bbox in legacy_grid.iter_patch(
                extent=plane_extent, **{kind: True}
            ):
                wgs = _plane_bbox_to_wgs84(bbox, plane_no)
                # WGS84 extentとの交差を再確認（座標変換の歪み対策）
                if not (
                    wgs[2] < extent[0]
                    or wgs[0] > extent[2]
                    or wgs[3] < extent[1]
                    or wgs[1] > extent[3]
                ):
                    yield (label, prefix + code, wgs)
        return
    raise ValueError(f"未知のメッシュ種別です: {kind}（対応: {KNOWN_KINDS}）")


def infer_mesh_kind(code: str) -> str | None:
    """標準地域メッシュコード長からkindを推定する。推定不能時はNone。"""
    digits = "".join(c for c in str(code) if c.isdigit())
    return _CODE_LENGTH_KINDS.get(len(digits))


def mesh_polygon(code: str, kind: str | None = None) -> Polygon | None:
    """メッシュコード → shapely Polygon。

    Args:
        code: メッシュコード。標準地域メッシュ（数字4/6/8/9/10/11桁）は
            gridsquare_to_boxで解釈する。legacy系は先頭2桁を
            平面直角系番号prefixとして解釈する（例: "0953AB..."）。
        kind: kindラベル。Noneの場合はコード長から推定する。

    Raises:
        ValueError: 未知のkindが指定された場合。

    Returns:
        Polygon（WGS84経緯度）。解釈不能時はNone。
    """
    if kind is not None and kind not in KNOWN_KINDS:
        raise ValueError(f"未知のメッシュ種別です: {kind}（対応: {KNOWN_KINDS}）")

    digits = "".join(c for c in str(code) if c.isdigit())
    if not digits:
        return None

    if kind is None:
        kind = infer_mesh_kind(code)
        if kind is None:
            return None

    if kind in _GRID_SQUARE_KINDS:
        bbox = grid_square_code_to_bbox(digits)
        if bbox is None:
            return None
        return bbox_to_polygon(bbox)

    # legacy系: 先頭2桁=平面直角系番号、それ以降が上流コード
    if len(digits) < 2:
        return None
    plane_no = int(digits[:2]) if digits[:2].isdigit() else 0
    if plane_no not in _PLANE_EPSG_JGD2011:
        return None
    raw_code = str(code)[2:]
    if not raw_code:
        return None
    bbox = _legacy_code_to_plane_bbox(raw_code, kind)
    if bbox is None:
        return None
    return bbox_to_polygon(_plane_bbox_to_wgs84(bbox, plane_no))


def _legacy_code_to_plane_bbox(
    raw_code: str, kind: str
) -> tuple[float, float, float, float] | None:
    """legacy生コード（prefix除去済み）→ 平面直角bbox。"""
    if kind == "lv50000":
        if len(raw_code) != 2 or not raw_code.isalpha():
            return None
        xi = ord(raw_code[0]) - ord("A")
        yi = ord(raw_code[1]) - ord("A")
        if not (0 <= xi < 20 and 0 <= yi < 8):
            return None
        x = 300000 - 30000 * xi
        y = -160000 + 40000 * yi
        return (y, x - 30000, y + 40000, x)
    if kind in ("lv5000", "lv500"):
        # lv5000: 親lv50000(2文字) + 2桁数字 / lv500: lv5000(4文字) + 2桁数字
        parent_len = 2 if kind == "lv5000" else 4
        if len(raw_code) != parent_len + 2:
            return None
        parent = raw_code[:parent_len]
        tail = raw_code[parent_len:]
        if not tail.isdigit():
            return None
        if kind == "lv5000":
            parent_bbox = _legacy_code_to_plane_bbox(parent, "lv50000")
            if parent_bbox is None:
                return None
            parent_y0, parent_x0, _, _ = parent_bbox
            xi, yi = int(tail[0]), int(tail[1])
            x = -3000 * xi + parent_x0 + 30000
            y = 4000 * yi + parent_y0
            return (y, x - 3000, y + 4000, x)
        parent_bbox = _legacy_code_to_plane_bbox(parent, "lv5000")
        if parent_bbox is None:
            return None
        parent_y0, parent_x0, _, _ = parent_bbox
        xi, yi = int(tail[0]), int(tail[1])
        x = -300 * xi + parent_x0 + 3000
        y = 400 * yi + parent_y0
        return (y, x - 300, y + 400, x)
    if kind == "lv2500":
        if len(raw_code) != 5 or raw_code[-1] not in "1234":
            return None
        parent_bbox = _legacy_code_to_plane_bbox(raw_code[:4], "lv5000")
        if parent_bbox is None:
            return None
        y0, x0, y1, x1 = parent_bbox
        xh = (x0 + x1) / 2
        yh = (y0 + y1) / 2
        quad = raw_code[-1]
        if quad == "3":
            return (y0, x0, yh, xh)
        if quad == "4":
            return (yh, x0, y1, xh)
        if quad == "1":
            return (y0, xh, yh, x1)
        return (yh, xh, y1, x1)
    if kind == "lv1000":
        if len(raw_code) != 6 or not raw_code[4].isdigit() or not raw_code[5].isalpha():
            return None
        parent_bbox = _legacy_code_to_plane_bbox(raw_code[:4], "lv5000")
        if parent_bbox is None:
            return None
        parent_y0, parent_x0, _, _ = parent_bbox
        xi = int(raw_code[4])
        yi = ord(raw_code[5]) - ord("A")
        if not (0 <= xi < 5 and 0 <= yi < 5):
            return None
        x = -600 * xi + parent_x0 + 3000
        y = 800 * yi + parent_y0
        return (y, x - 600, y + 800, x)
    return None


def mesh_feature(
    code: str,
    kind_label: str,
    bbox_or_polygon: tuple[float, float, float, float] | Polygon,
) -> dict:
    """メッシュ → GeoJSON Feature dict。

    properties は {"mesh_code", "mesh_kind"}。
    bbox（タプル）または shapely Polygon を受け付ける。
    """
    if isinstance(bbox_or_polygon, Polygon):
        polygon = bbox_or_polygon
    else:
        polygon = bbox_to_polygon(bbox_or_polygon)
    return {
        "type": "Feature",
        "geometry": mapping(polygon),
        "properties": {"mesh_code": code, "mesh_kind": kind_label},
    }


def mesh_features_to_collection(features: list[dict]) -> dict:
    """GeoJSON Feature リスト → FeatureCollection dict。"""
    return {"type": "FeatureCollection", "features": features}


# 互換エイリアス（上流 gridsquare_to_box の lnglat 変換も公開）
def lnglat_to_mesh_codes(
    lng: float, lat: float
) -> Optional[dict]:
    """経緯度 → 各粒度の標準地域メッシュコードdict。"""
    from .gridsquare_to_box import lnglat_to_grid_square_code

    return lnglat_to_grid_square_code(lng, lat)
