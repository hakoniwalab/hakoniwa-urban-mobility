# デモ ③-1〜③-5 の World の作り方

デモ ③-1〜③-5 の Composition は、World を City Asset の **ID** で指します。このフォルダは、その 3 つの World を、どの Workspace（Windows でも）でも同じ ID で作り直すための材料と手順です。Mac で作ったデータは持ち運びません。Windows ポータブル版は、Windows で作った World を同梱します（[docs/windows-portable.md](../docs/windows-portable.md)）。

| City Asset の ID | 内容 | 使うデモ | 作り方 |
|---|---|---|---|
| `tokyo-13104-multi-lat35_689-lon139_691` | 都庁（PLATEAU の City World） | ③-1〜③-4、③-5 の `demo35-koen-*` | Environment Studio の画面（下の値）。スクリプトでも作れる |
| `sapporo-rotary-snow` | 雪の日の札幌駅南口ロータリー（Environment Studio の Recipe） | ③-5（`koen` 以外） | スクリプト |
| `tocho-bridge-a-hull` | 都庁の歩道橋 A の当たり判定を 1 つの凸包にした比較用 | ③-3 の `demo33-a-hull` | スクリプト（都庁から作る） |

| ファイル | 内容 |
|---|---|
| [demo-worlds.json](demo-worlds.json) | 3 つの World の ID・名前と、下の材料の場所 |
| [worlds/tocho-city-world.json](worlds/tocho-city-world.json) | 都庁の City World の生成条件（Environment Studio の `POST /api/city-worlds/build` の本文、`job.json` の `request` と同じ） |
| [worlds/sapporo-city-world.json](worlds/sapporo-city-world.json) | `sapporo-351-exact` の元になる札幌駅南口の City World の生成条件 |
| [worlds/sapporo-rotary-snow.yaml](worlds/sapporo-rotary-snow.yaml) | デモの Recipe（`sapporo-351-exact` の部品 53 個 + 停止線・横断歩道・屋台・広場・停留所 48 個） |
| [urban/](urban/) | デモの Composition（`compositions/demo31〜35-*`）、ルートと飛行計画（`scenarios/`）、人のシーン（`scenes/demo35-festival-stalls.yaml`）。work の `urban/` にそのままコピーする |
| [../tools/urban_demo_worlds.py](../tools/urban_demo_worlds.py) | 全部を順に作る入口（`build`）と確認（`check`） |
| [../tools/make_hull_comparison_world.py](../tools/make_hull_comparison_world.py) | 比較用の World を作る道具 |

## まとめて作る

`(hako)` シェル（`python tools\workspace.py enter`）で、`hakoniwa-business-pack` から実行します。Windows でも bash は要りません。都庁と札幌は PLATEAU からダウンロードするので、インターネット接続が必要です（合わせて数十分）。

```
python ..\hakoniwa-urban-mobility\tools\urban_demo_worlds.py build --all
python ..\hakoniwa-urban-mobility\tools\urban_demo_worlds.py check
```

`build --all` は `--demos`（デモのファイルを work にコピー）、`--tocho`、`--sapporo`、`--hull` の順に実行します。1 つずつ（いくつでも）指定できます。もうあるもの（登録済みの City、作ってある City World・Recipe、内容の違う Composition）はそのまま使い、作り直すときは `--force` を付けます。`--no-precompile` は登録時の高さモデルの compile を省きます（最初の Configure のときに作られます）。

`check` は、3 つの City Asset がデモの ID で登録されていて receipt があること、デモの Composition が work にあってその World が見つかることを確かめ、そろっていれば 0 で終わります。

前提：Environment Studio のレシピを configure 済み（`python ..\hakoniwa-urban-mobility\tools\urban_city_authoring.py configure`）。hakoniwa-environment-studio と hakoniwa-envsim がこのリポジトリの隣にあること（`HAKONIWA_ENVIRONMENT_STUDIO_ROOT` で場所を変えられます）。

## ① 都庁：Environment Studio の画面で作る

画面で作るのが本来の手順です（Windows で Environment Studio を試すことも兼ねます）。`build --tocho` は同じことを画面なしで行う代わりの手段で、確認にも使えます。

1. Urban Studio の「City」タブで「Environment Studio で作る」を押す（地図のページ `http://127.0.0.1:28097/map.html` が開く。Urban の受け取りフォルダ `work\urban\studio-cities` に書き出す設定で起動します）。
2. 「作る」タブで次のとおり入力する。

   | 欄 | 値 |
   |---|---|
   | 1. 対象範囲：緯度 | `35.689245` |
   | 経度 | `139.690808` |
   | 南北の半分 (m) | `217` |
   | 東西の半分 (m) | `218.3` |
   | データの種類 | PLATEAU |
   | 生成条件：建物の当たり判定の細かさ | `3 — P3 → P2 → P1 → P0` |
   | 地形（DEM）が無い所 | 止める（厳密・既定） |
   | 同じ平面で隣り合う凸面をまとめる | **オン**（下をオンにすると自動でオンになる） |
   | 凹んだ面の三角形の当たり判定も、少数の凸形にまとめ直す（実験的） | **オン** |
   | 壁面を最大 5 cm の近似でまとめる | オフ |
   | 橋の下の地形（DEM）を、周りの低い地面まで下げる | **オン** |
   | 橋の床の縁と地形（DEM）の段差を、なめらかにつなぐ | **オン** |

   地図をクリックすると中心が動くので、値は最後に入力し直して確かめてください。
3. 「2. Capabilityを診断」→「生成できます」と、新宿区・渋谷区・中野区（2025）が表示されることを確かめる。
4. 「3. City Worldを生成」。数十分かかります。できると「生成しました — tokyo-13104-multi-lat35_689-lon139_691」と表示され、Urban の受け取りフォルダに自動で書き出されます。
5. Urban Studio の「City」タブで、`新宿区・渋谷区 付近（437 × 434 m）` が登録されたこと（高さモデルの compile が終わること）を確かめる。

### ID の決まり方

Environment Studio の画面は、PLATEAU の City World の ID を次のように付けます（`web/cityworlds.js` の `generatedJobId`。サーバー `env_cityworld.py` は受け取った ID をそのまま使う）。

```
<都道府県>-<市区町村コード>[-multi]-lat<緯度 小数 3 桁>-lon<経度 小数 3 桁>    （"." は "_"）
```

市区町村コードは、診断（`POST /api/plateau/inspect`）が選んだ PLATEAU のファイル（建物・道路・地形・路面標示・橋）の市区町村のうち、いちばん小さいもの。2 つ以上あると `-multi` が付きます。都庁の範囲は 13104（新宿区）・13113（渋谷区）・13114（中野区）なので `tokyo-13104-multi-lat35_689-lon139_691`。Urban への書き出しは、この ID をフォルダ名にし（`env_urban.export_city_world`）、Urban Studio はフォルダ名を City Asset の ID にして登録します（`urban_assets.city_id_from_receipt`）。つまり、**同じ中心を入力すれば、Windows で作った都庁も同じ ID になります**。範囲の大きさや生成条件は ID に入りません。

PLATEAU の公開データが変わって市区町村の組み合わせが変わると、ID が変わります（例：`-multi` が外れる）。そのときは

```
python ..\hakoniwa-urban-mobility\tools\urban_demo_worlds.py check --adopt
```

が、同じ中心と大きさの City を探して、デモの ID でも登録します（同じ receipt を指す 2 つ目の City Asset）。`build --tocho` は、診断の結果から画面で作った場合の ID と名前を表示し、デモの ID で作ります。

### スクリプトで作る

```
python ..\hakoniwa-urban-mobility\tools\urban_demo_worlds.py build --tocho
```

[worlds/tocho-city-world.json](worlds/tocho-city-world.json) の条件で、Environment Studio と同じ処理（`env_cityworld` の Envsim ビルドと viewer ファイル、`env_urban.export_city_world` で Urban の受け取りフォルダへ書き出し）を行い、`urban_assets.py register-city` で登録します。Environment Studio のサーバーは使いません（動いていても構いませんが、生成は同時に 1 つだけにしてください）。

## ② 札幌駅南口ロータリー（雪）

もとは次の順で作られました。

1. Business Pack の City World Web UI で、札幌駅南口の City World `hokkaido-01100-lat43.067-lon141.351` を生成（中心 43.06738, 141.350709、南北の半分 91.3 m・東西の半分 172.7 m、建物の当たり判定 3、`convex-decompose`、DEM が無い所は止める。PLATEAU 札幌市 2020）。Environment Studio の画面で作ると ID は `hokkaido-01100-lat43_067-lon141_351` です。
2. Environment Studio の「できたもの」→「部品として取り込む」（`env_citygml.convert_build`、地面は `city-dem`、LOD2 の見た目あり、Envsim の出力をそのまま使う）で Recipe `sapporo-351-exact`（「札幌 351（envsim 原本）」）。建物がはみ出さないように範囲が広がり、`bbox_deg` は south 43.066188183・west 141.347904942・north 43.068571749・east 141.353513166、原点 43.06738/141.350709、456.8 × 264.8 m になります。建物ごとの見た目と当たり判定、地形は `sapporo-351-exact.assets/` に置かれます。
3. `sapporo-351-exact` を変えずに部品を加えた `sapporo-rotary`（屋台・広場・停留所）、さらに停止線と横断歩道を加えて屋台を並べ直した `sapporo-rotary-snow`。`sapporo-351-exact.assets/` を共有します。
4. `env_urban.py export sapporo-rotary-snow.yaml`（`work\recipes\environment-studio\urban\sapporo-rotary-snow`）を `urban_assets.py register-city --title "札幌駅南口ロータリー（雪の日・停止線と横断歩道つき、③-5）"` で登録。

リポジトリには 3 の `sapporo-rotary-snow.yaml` だけを置きます（`sapporo-rotary` は途中の段階で、要りません）。`geo.query` は Recipe の由来の記録（変換したファイルと sha256）で、生成には使われないので（Environment Studio は文字列であることだけを確かめる）、絶対パスを City World の `build/source/` からの相対（`01100-2020/…`）にしました。`catalog:` は置くときにその Workspace の Environment Studio を指すように書き換えます。

```
python ..\hakoniwa-urban-mobility\tools\urban_demo_worlds.py build --sapporo
```

は、[a] 札幌駅南口の City World を PLATEAU から生成（Environment Studio と同じ処理、[worlds/sapporo-city-world.json](worlds/sapporo-city-world.json)）、[b] `env_citygml.py --envsim-build … --terrain city-dem --name "札幌 351（envsim 原本）" --out …\recipes\sapporo-351-exact.yaml` で部品の Recipe と `sapporo-351-exact.assets/`、[c] デモの Recipe を `work\recipes\environment-studio\recipes\sapporo-rotary-snow.yaml` に置き、参照するファイルがすべてあるかを確かめ、[d] `env_urban.py export` で World を書き出し、登録します。PLATEAU のデータ（建物の ID）が変わっていて参照するファイルが足りないときは、[c] で止まります。`sapporo-351-exact` の部品がデモを作ったときと違えば注意を表示します（デモの Recipe の値を使います）。

画面で行う場合も同じです。1 を Environment Studio の「作る」タブ（上の値、橋の 2 つはオフ）、2 を「できたもの」で `hokkaido-01100-lat43_067-lon141_351` を選んで「部品として取り込む」（ID `sapporo-351-exact`、地面 `city-dem`）、3 は `worlds/sapporo-rotary-snow.yaml` を `work\recipes\environment-studio\recipes\` に置く（`catalog:` を直す）ことになるので、スクリプトを使ってください。

## ③ 歩道橋 A の凸包（比較用）

```
python ..\hakoniwa-urban-mobility\tools\urban_demo_worlds.py build --hull
```

は、登録済みの都庁から [make_hull_comparison_world.py](../tools/make_hull_comparison_world.py) で `work\urban\comparison-worlds\tocho-bridge-a-hull` を作り、`tocho-bridge-a-hull` として登録します。都庁の World の MJCF から橋 `brid_75151184…`（歩道橋 A、PLATEAU 新宿区 2025）の部品（`bridge_piece_*`、Envsim の `components/bridges/debug/bridge-surfaces.json` で橋ごとに分かる）を外し、その頂点をすべて持つメッシュ 1 つ（MuJoCo は凸包で当たる）と geom 1 つを加えます。見た目（GLB）、地形、ほかの当たり判定は都庁のままです。地形の hfield はコピーして MJCF からの相対パスのままにし、当たり判定の表示（`viewer/city-world-colliders.glb`）は都庁のものをコピーします（シンボリックリンクは使いません）。都庁の部品 76 個 → 頂点 456 個です。

道具だけを使う場合：

```
python ..\hakoniwa-urban-mobility\tools\make_hull_comparison_world.py --source-city tokyo-13104-multi-lat35_689-lon139_691 --bridge brid_75151184 --id tocho-bridge-a-hull --title "都庁（比較用：歩道橋Aの当たり判定を1メッシュ＝凸包にした版）"
```

## 確かめたこと（macOS、ダウンロードなし）

一時的な work（`HAKONIWA_WORK_DIR`）で、作ってある City World を `--tocho-build` / `--sapporo-build` で渡して `build --all` 相当を実行し、`check` が通ることを確かめました。

- 札幌：`sapporo-351-exact` の部品はデモの Recipe と一致し、書き出した World の MJCF・地形の hfield・当たり判定の表示は Mac の `sapporo-rotary-snow` と同じバイト列。見た目の GLB は同じ大きさで数値の一部が違います（見た目だけ）。
- 歩道橋 A：MJCF は Mac の `tocho-bridge-a-hull` と、hfield のパス（絶対 → 相対）以外は同じ。MuJoCo で compile できます。
