"""dm-convert コマンドラインインターフェース (click)。"""

from __future__ import annotations

import glob
import json
import os
from collections import Counter

import click

from .api import (
    KNOWN_MESH_KINDS,
    convert_dm,
    convert_files,
    mesh_code_geojson,
    mesh_geojson,
    parse_dm_file,
    write_geojson,
)
from .crs import get_epsg


@click.group()
def main() -> None:
    """DM(数値地形図データ)→GeoJSON変換ツール。"""


@main.command()
@click.argument("input", type=click.Path(exists=True))
@click.option(
    "-o",
    "--out-dir",
    required=True,
    type=click.Path(),
    help="GeoJSON出力先ディレクトリ (1レイヤ=1ファイル)。",
)
@click.option(
    "--to-epsg",
    default=None,
    type=int,
    help="指定EPSGに再投影して出力する (例: --to-epsg 4326 で緯度経度化)。",
)
def convert(input: str, out_dir: str, to_epsg: int | None) -> None:
    """DMファイルまたはフォルダをGeoJSON群に変換する。"""
    if os.path.isdir(input):
        dm_files = sorted(glob.glob(os.path.join(input, "*.dm")))
        if not dm_files:
            raise click.ClickException(
                f"フォルダ内にDMファイルが見つかりません: {input}"
            )
        try:
            result = convert_files(dm_files, to_epsg=to_epsg)
        except ValueError as exc:
            raise click.ClickException(str(exc))
    else:
        try:
            result = convert_dm(input, to_epsg=to_epsg)
        except ValueError as exc:
            raise click.ClickException(str(exc))
    written = write_geojson(result, out_dir)
    click.echo(result.summary)
    click.echo(f"出力: {len(written)}ファイル → {out_dir}")


@main.command()
@click.option(
    "--kind",
    required=True,
    type=click.Choice(sorted(KNOWN_MESH_KINDS)),
    help="メッシュ種別。",
)
@click.option(
    "--extent",
    required=True,
    help="範囲 xmin,ymin,xmax,ymax (WGS84経緯度)。",
)
@click.option("-o", "--output", required=True, type=click.Path(), help="出力GeoJSONパス。")
def mesh(kind: str, extent: str, output: str) -> None:
    """指定範囲の地域メッシュをGeoJSONで出力する。"""
    try:
        xmin, ymin, xmax, ymax = (float(v) for v in extent.split(","))
    except ValueError:
        raise click.ClickException(
            f"--extent は xmin,ymin,xmax,ymax の4数値で指定してください: {extent!r}"
        )
    if not (xmin < xmax and ymin < ymax):
        raise click.ClickException(f"--extent の大小関係が不正です: {extent!r}")
    result = mesh_geojson(kind, (xmin, ymin, xmax, ymax))
    _write_fc(result.feature_collection, output)
    click.echo(f"{kind}: {result.count}メッシュ → {output}")


@main.command(name="mesh-code")
@click.option("--code", required=True, help="メッシュコード (例: 53393599)。")
@click.option(
    "--kind",
    default=None,
    type=click.Choice(sorted(KNOWN_MESH_KINDS)),
    help="メッシュ種別 (省略時はコード長から推定)。",
)
@click.option("-o", "--output", required=True, type=click.Path(), help="出力GeoJSONパス。")
def mesh_code(code: str, kind: str | None, output: str) -> None:
    """メッシュコード単体のポリゴンをGeoJSONで出力する。"""
    try:
        result = mesh_code_geojson(code, kind)
    except ValueError as exc:
        raise click.ClickException(str(exc))
    _write_fc(result.feature_collection, output)
    click.echo(f"{code}: 1メッシュ → {output}")


@main.command()
@click.argument("input", type=click.Path(exists=True, dir_okay=False))
def info(input: str) -> None:
    """DMファイルの図郭・座標系・要素統計を表示する。"""
    parsed = parse_dm_file(input)
    mesh = parsed.mesh_info
    sheet = parsed.map_sheet
    cs = mesh.coordinate_system
    epsg_str = f"EPSG:{get_epsg(cs)}" if cs is not None else "不明"
    click.echo(f"ファイル: {input}")
    click.echo(f"図郭名称: {mesh.map_name}")
    click.echo(f"地図情報レベル: {mesh.scale}")
    click.echo(f"座標系番号: {cs} ({epsg_str})")
    click.echo(
        f"図郭範囲: X {sheet.origin_x}..{sheet.upper_x}, "
        f"Y {sheet.origin_y}..{sheet.upper_y} (単位コード {sheet.coord_unit})"
    )
    type_counter: Counter[str] = Counter()
    code_counter: Counter[tuple[str, str]] = Counter()
    no_coords: Counter[tuple[str, str]] = Counter()
    n_elements = 0
    has_3d = False
    for group in parsed.groups:
        for elem in group.elements:
            n_elements += 1
            type_counter[elem.element_type] += 1
            code_counter[(elem.element_type, elem.dm_code)] += 1
            if not elem.coordinates:
                no_coords[(elem.element_type, elem.dm_code)] += 1
            if elem.data_kubun in (3, 6):
                has_3d = True
    click.echo(f"グループ数: {len(parsed.groups)}, 要素数: {n_elements}")
    click.echo(f"3D要素あり: {'はい' if has_3d else 'いいえ'}")
    for etype in sorted(type_counter):
        click.echo(f"  {etype}: {type_counter[etype]}件")
    if parsed.parse_warnings:
        click.echo(f"パース警告: {len(parsed.parse_warnings)}件")
        for w in parsed.parse_warnings[:10]:
            click.echo(f"  - {w}")


def _write_fc(feature_collection: dict, output: str) -> None:
    parent = os.path.dirname(os.path.abspath(output))
    os.makedirs(parent, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(feature_collection, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
