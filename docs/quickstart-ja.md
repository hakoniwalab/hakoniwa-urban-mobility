# Quick start（はじめての Urban Mobility）

Urban Mobility は、街（City）や平らな地面（World）に、ゴルフカートやドローンを置いてシミュレーションするための道具です。ブラウザの **Urban Studio** から、次の流れで使います。

```text
City を作る（Environment Studio）→ 車両と組み合わせる（Compose）→ 走らせる（Simulation）
```

City は、Urban Studio から起動する **Environment Studio** で作ります（地図で範囲を選び、PLATEAU か OpenStreetMap から作る。必要なら部品として編集する）。作った City は自動で Urban Mobility に登録されます。

## 0. 準備

1. [箱庭ビジネスパックの導入ガイド](https://github.com/hakoniwalab/hakoniwa-business-pack/blob/main/docs/getting-started-ja.md) の「4. 環境構築」に従って、Python 3.12、Ruby、CMake などを入れます。
2. 同じ親ディレクトリに、Business Pack と Urban Mobility を並べて clone します。ほかに必要なリポジトリ（Environment Studio、Drone Core など）は、あとで `configure` が隣に clone します。

   ```bash
   mkdir -p ~/Hakoniwa && cd ~/Hakoniwa
   git clone https://github.com/hakoniwalab/hakoniwa-business-pack.git
   git clone https://github.com/hakoniwalab/hakoniwa-urban-mobility.git
   cd hakoniwa-business-pack
   ```

3. Business Pack の Workspace に入ります。プロンプトの先頭に `(hako)` が付きます。**以降のコマンドは、すべてこの `(hako)` シェルの `hakoniwa-business-pack` で実行します。**

   ```bash
   python3.12 tools/workspace.py enter
   ```

## 1. Urban Studio を開く

```bash
python ../hakoniwa-urban-mobility/tools/urban_studio.py start --open-browser
```

ブラウザで http://127.0.0.1:28090/ が開きます。あとで開き直すときは `urban_studio.py open`、状態を見るときは `urban_studio.py status` です。

## 2. まず平らな World で飛ばしてみる（近道）

City を作らなくても、平らな World ですぐ試せます。

1. **Simulation** タブで、例の Composition `plain-hexa-rc`（平らな World と EAMS Hexa 1 機）を選びます。
2. **Configure** を押します。初回は、必要なリポジトリの取得やビルドがあるので時間がかかります。
3. **Start** を押し、**Viewer を開く** で表示します。
4. PS4 / PS5 のコントローラで操縦します（Cross でアーム、左スティックで上昇）。
5. 終わったら **Stop** を押します。

## 3. City を作る

City の元になる地図データは 2 種類あり、取れる範囲と精度が違います。Environment Studio の地図ページの左上のトグルで切り替えます（範囲はそのまま残ります）。

| | PLATEAU | OpenStreetMap |
|---|---|---|
| 範囲 | PLATEAU が整備された都市だけ | 世界中どこでも |
| 建物 | LOD2 の形とテクスチャ、建物ごとの当たり判定 | 外形を押し出した箱（高さは推定を含む） |
| 地形・道路 | 地形（DEM）、道路面、路面標示 | 平らな地面、道路の線から作った面 |

まず PLATEAU で診断し、データがなければ OpenStreetMap で作るのがおすすめです。

1. **City** タブの **Environment Studio で作る** を押します。Environment Studio が起動し、地図ページが別のタブで開きます（初回は必要なものを configure するので数分かかります）。
2. 地図ページで、青い範囲を動かして場所を決めます（中心のマーカー、四隅のハンドル。一辺は 20〜2000 m）。
3. 左上のトグルを **PLATEAU** にして、**2. Capabilityを診断** を押します。範囲にあるデータ（自治体、Building・Terrain・Road などの有無と LOD）が表示され、診断した PLATEAU のメッシュが地図に重なります。生成条件（Building Physics Level など）は、最初は既定のままで構いません。

### PLATEAU で作る（診断で「作れます」と出たとき）

1. 診断が **生成候補あり** になったら、**3. City Worldを生成** を押します。進捗が表示されます。範囲と建物の数によって、数分〜数十分かかります。ダウンロードしたデータは次から使い回します。
2. できあがると、自動で urban に書き出されます。ID は自治体と中心の位置から自動で付きます（例：`hokkaido-01100-lat43_068-lon141_351`）。
3. **生成結果** で作った City World を選ぶと、Collider の内訳が表示されます。**3D Viewer** で見た目（Visual）と当たり判定（Collider）を重ねて確かめられます。

診断で DEM がないと出たときは、「DEM未被覆領域」を「標高0 mで補完」にしてもう一度診断すれば、平らな地面で作れます。同じ場所をもう一度生成すると作り直します（urban に書き出し済みのときは、先に生成結果で **書き出しを消す** を押します）。

### OpenStreetMap で作る（PLATEAU にデータがないとき）

1. 診断結果の **この範囲を OpenStreetMap で作る** を押すと、範囲を保ったまま OpenStreetMap に切り替わります（トグルで切り替えても同じです）。
2. ID と名前を入れ、**この範囲を取り込む** を押します。建物と道路が部品になった環境ができます。
3. **Studio で開く** で Environment Studio の画面を開き、必要なら建物や道路を動かして保存し、画面上部の **書き出す** を押します。

OpenStreetMap で作業していて「この場所は PLATEAU にもあるかな？」と思ったら、**この範囲が PLATEAU にあるか確かめる** で、PLATEAU に切り替えて同じ範囲を診断できます。

### 登録される

書き出された City は、Urban Studio の City タブの **Environment Studio から届いた City** に現れ、自動で検査・登録されます（高さ計算用のモデルを一度だけ準備するので、大きな街は数分）。**登録済み** になれば使えます。

PLATEAU の City を編集したいときは、生成結果で **部品として取り込む** を押し、Environment Studio の画面で建物や道路を動かして保存し、**書き出す** を押します。書き出したものも同じように自動で登録されます。

## 4. 車両と組み合わせて走らせる

1. **Compose** タブで **新規** を押し、ID を付けます。
2. **World** に、3 で作った City を選びます。
3. 車両（ゴルフカート、EAMS Hexa など）を選んで **追加** し、制御を **RC（コントローラ）** にします。
4. **配置** の **地図** で、車両を道路の上に置きます（高さは地面・屋上に合わせて自動）。
5. **保存して検証** を押し、**Simulation へ** で移って、その Composition を選んで **Configure** → **Start** を押します。
6. **Viewer を開く** で表示します。建物の当たり判定を重ねて見たいときは、隣の **Viewer（当たり判定）を開く** を押します。
7. 終わったら **Stop** を押します。

## 5. 片付け

Workspace を抜ける前に、動いているものを止めます（`exit` しても、裏で動いているものは止まりません）。

1. Simulation で動かしているものは **Stop**
2. City タブの **Environment Studio を停止**
3. Urban Studio を止めて、Workspace を抜けます。

   ```bash
   python ../hakoniwa-urban-mobility/tools/urban_studio.py stop
   exit
   ```

## ブラウザを使わずに操作するとき

| やりたいこと | コマンド（`(hako)` シェルの `hakoniwa-business-pack` で） |
|---|---|
| Environment Studio を起動（configure してから、書き出し先を urban にして） | `python ../hakoniwa-urban-mobility/tools/urban_city_authoring.py start --open-browser` |
| Environment Studio を停止 | `python ../hakoniwa-urban-mobility/tools/urban_city_authoring.py stop` |
| Composition を configure / 起動 / 停止 | `python ../hakoniwa-urban-mobility/tools/urban_simulation.py configure --composition <path>`（`start` / `stop` / `status`） |
| 登録済みの City などを一覧 | `python ../hakoniwa-urban-mobility/tools/urban_assets.py list` |

## 困ったとき

| 症状 | 対処 |
|---|---|
| `port 28090 is in use` などと出る | すでに動いています。`urban_studio.py open` で開くか、`urban_studio.py stop` で止めてから起動し直します。 |
| City タブに「書き出し先が urban の受け取りフォルダではありません」と出る | Environment Studio を手で（書き出し先なしで）起動しています。City タブの **Environment Studio を停止** を押し、**Environment Studio で作る** で起動し直してください。 |
| City の登録が「登録できませんでした」になる | 行の「出力」を開くと理由が出ます。City World の形式の検査（`schemas/city-world-job.yaml`）で止まっています。 |
| `Hakoniwa Workspace is not active` と出る | `(hako)` シェルの外で実行しています。`python3.12 tools/workspace.py enter` で入ってから実行します。 |

## 使うポート

どれも、よく使われるポートとぶつからない番号にしてあります（`urban.manifest.yaml`）。

| 用途 | ポート |
|---|---|
| Urban Studio | 28090 |
| Environment Studio | 28097 |
| シミュレーションの Viewer（HTTP） | 28100 |
| WebBridge（統合・ドローン / 車だけ / fleet） | 28865 / 28866 / 28867 |

## 次に読むもの

- 仕組みと契約：[`docs/asset-contract.md`](asset-contract.md)（Asset、Composition、City World ジョブ）、[`docs/urban-manifest.md`](urban-manifest.md)（部品とポート）
- City の作り方の詳細：[Environment Studio](https://github.com/hakoniwalab/hakoniwa-environment-studio)
