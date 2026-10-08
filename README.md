# dm-converter

QGIS不要の純Pythonパッケージ。公共測量の数値地形図データ（DM）をGeoJSONに変換する。
MIERUNE製QGISプラグイン [dmconverter](https://github.com/MIERUNE/dmconverter) の
パーサー核を移植し、QGIS依存（PyQGIS）をshapely＋pyprojに置き換えた。
地域メッシュ生成は [qgis-japan-mesh](https://github.com/MIERUNE/qgis-japan-mesh)
の純Pythonロジックを統合している。

## できること

- DMファイル（単体・フォルダ一括）→ GeoJSON（1レイヤ=1ファイル）
- E1面・E2線・E3円・E4円弧・E5点・E6方向・E7注記に対応（円=64角形近似、円弧=補間、中庭線対応）
- JGD2011平面直角座標系（系番号1–19→EPSG:6669–6687）を自動判定
- `--to-epsg 4326` で緯度経度化（デフォルトは平面直角系メートル値のまま）
- 標準地域メッシュ8種＋国土基本図図郭5種＋100mメッシュのGeoJSON生成
- 文字コード自動判定（UTF-8 BOMあり/なし・cp932・旧型式7bit JIS）

## インストール

```bash
pip install dm-converter
```

開発版:

```bash
git clone <this-repo>
cd dm_converter_py
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

## 使い方

### CLI

```bash
# 単体変換（1レイヤ=1 GeoJSON）
dm-convert convert 入力.dm -o out_dir/

# フォルダ一括（同系・同レベルのみ結合、混在はエラー）
dm-convert convert dm_folder/ -o out_dir/

# 緯度経度（WGS84）で出す
dm-convert convert 入力.dm -o out_dir/ --to-epsg 4326

# ファイルの中身を確認
dm-convert info 入力.dm

# 地域メッシュ（範囲指定、WGS84経緯度）
dm-convert mesh --kind standard --extent 139.5,35.5,139.7,35.7 -o mesh.geojson

# メッシュコード単体
dm-convert mesh-code --code 53393599 -o one.geojson
```

メッシュ種別（14種）: `primary` `secondary` `standard` `half` `quarter` `eighth`
`quintuple` `double` `m100` `lv50000` `lv5000` `lv2500` `lv1000` `lv500`

### ライブラリ

```python
from dm_converter import convert_dm, write_geojson

r = convert_dm("入力.dm")          # r.layers / r.epsg / r.warnings / r.summary
write_geojson(r, "out_dir/")

# 緯度経度化
r = convert_dm("入力.dm", to_epsg=4326)

# フォルダ一括
from dm_converter import convert_folder
r = convert_folder("dm_folder/")

# メッシュ
from dm_converter import mesh_geojson, mesh_code_geojson
m = mesh_geojson("standard", (139.5, 35.5, 139.7, 35.7))
m = mesh_code_geojson("53393599")
```

## 出力仕様

- 座標系: デフォルトは元CRS（平面直角系、単位m）のまま。`crs_hint: {"epsg": N}`
  をFeatureCollectionのforeign memberに付与（EPSG不明時は省略）。
  GeoJSON慣例のCRSオブジェクトではない点に注意。
- レイヤ名: 分類コード→名称（例 `道路縁(街区線)_線`）。名称衝突時は親分類名を
  prefix、同親内衝突時は4桁コードをsuffix。
- 属性: 日本語名を維持（分類コード・地域分類・図形区分・HCODE2=コード＋図形区分
  ほか）。E7注記は `注記内容/字の大きさ/文字列の方向/縦横区分`、
  E6方向は `方向角` を表示ヒントとして残す。
- QGIS互換の範囲: QML適用・ラベル描画・QLR出力はQGIS外では実行不可のため対象外。
  出力GeoJSONをQGISに読ませれば注記・方向ヒントから即スタイル設定できる。

## 開発

```bash
.venv/bin/python -m pytest tests/ -q
```

テストは上流の合成DM（`/tmp/dmconverter_upstream/tests/data/*.dm` 参照）を使う。
手元にない場合は `tests/test_dm_convert.py` の `DATA_DIR` を書き換える。

## 謝辞・ライセンス

パーサー核は [MIERUNE/dmconverter](https://github.com/MIERUNE/dmconverter)、
メッシュロジックは [MIERUNE/qgis-japan-mesh](https://github.com/MIERUNE/qgis-japan-mesh)
（原著者 [@ciscorn](https://github.com/ciscorn)）を元にしている。
いずれもGPLv2のため本パッケージは **GPL-2.0-or-later**（`LICENSE` 参照）。
