# Urban Studio の Windows ポータブル版（開発者向け）

Urban Studio を、Python・Git・C++ ビルド環境のない Windows で動く ZIP にまとめる方法です（issue #82）。利用者向けの説明は [portable/README-WINDOWS.txt](../portable/README-WINDOWS.txt) で、ZIP のルートに `README-WINDOWS.txt` として入ります。

## しくみ

ZIP は Business Pack の `tools/package_portable_workspace.py` が作ります。Urban 固有のことは、このリポジトリの 2 つのファイルが受け持ちます。

| ファイル | 内容 |
|---|---|
| [portable/windows-profile.json](../portable/windows-profile.json) | 同梱するリポジトリと、その中のパス（`include_paths`）、組み込み用 Python の `._pth` に入れるフォルダ（`python_paths`）、作成時に import を確かめるモジュール |
| [tools/urban_portable.py](../tools/urban_portable.py) | packager が呼ぶ `collect` / `doctor` / `prepare` と、ZIP の `start` / `status` / `stop-urban-studio.bat` が呼ぶ `start` / `status` / `stop` |
| [portable/demo-data.json](../portable/demo-data.json) | 同梱するデモデータ（街、Composition、ルート、飛行計画、人のシーン） |

| コマンド | 実行される場所 | 内容 |
|---|---|---|
| `collect` | 作成元の Workspace | `build\portable-runtime\` に集める：Urban Car の Plant と隣の DLL と features ファイル（`bin\`）、Drone Core 用の `glfw3.dll`（`drone-bin\`、drone-core の `win\` にあれば不要）、デモデータ（`urban-demo-data.zip`）。hakoniwa-fpv-drone の `fpv_portable.py collect` も実行する |
| `bundle` | デモの街がある Workspace（macOS でもよい） | デモデータの ZIP だけを作る（`--output`） |
| `doctor` | 作成元の Workspace | Plant（MIRROR=ON）、Drone Core の Windows 版と MuJoCo、Foundation の `hako-cmd.exe`・WebBridge・`shakoc.dll`、Python パッケージ、デモデータがそろっているか確かめる。ほかのプログラムが使っているポートも知らせる |
| `prepare` | ZIP の中（初回の `start`、作成時の検証） | Foundation の receipt の `install.prefix` と `core_mmap_path` を展開先に合わせ、`workspace.py prepare` を実行し、デモデータを `hakoniwa-business-pack\work\` に展開する（展開先が同じなら次回からは省く。フォルダを移動したら、work の中のパスを書き換える） |
| `start` / `status` / `stop` | ZIP の中 | `start`：`prepare`、ポートの確認、`urban_studio.py start`、ポートの待ち合わせ、ブラウザ。`stop`：動いているシミュレーション（Launcher の session）、Environment Studio、Urban Studio の順に止める |

### portable モード

`.bat` は `HAKONIWA_PORTABLE_WORKSPACE=1` を設定します（`urban_manifest.portable()`）。このとき：

- `urban_simulation.prepare_route`（ドローン・群・FPV の経路）と `urban_mobility.py configure`（車・統合の経路）は、Business Pack の `recipe.py configure` / `doctor`（Git と pip を使う）を飛ばす。
- 車の経路と統合の経路は CMake を使わず、同梱の Plant を使う（`multi_car.ensure_car_asset` / `require_built_car_asset`）。摩擦（`road_friction`）のある経路と統合の経路は、Plant Directive（`HAKO_URBAN_ENABLE_MIRROR=ON`）の Plant が必要。
- Environment Studio の起動（`urban_city_authoring.py start`）は configure を飛ばす（`--no-configure` と同じ）。

### Plant Directive の判定

`multi_car.built_with_plant_directive()` は、Plant の隣の `urban-car-hakoniwa-asset.features.json`（`CMakeLists.txt` が configure 時に書く）を読みます。ない場合（この変更より前に configure した build）は、これまでどおり `build\CMakeCache.txt` を読みます。`collect` は features ファイルがなければ CMakeCache から作り、MIRROR=OFF の Plant は断ります。Plant は `build\bin\`、なければ `build\portable-runtime\bin\` から探します（`multi_car.plant_executable()`）。

### デモデータ

`collect`（または `bundle`）は、`portable/demo-data.json` の `include` と、それらのファイルが参照する work の中のファイル（絶対パス、`${repo:hakoniwa-business-pack}/work/...`）を集めます。JSON・YAML・XML の中の作成元のパスは、プレースホルダー（work は `__HAKONIWA_PORTABLE_WORK__`、リポジトリを並べたフォルダは `__HAKONIWA_PORTABLE_ROOT__`）に置き換えます。`/`・`\`・JSON のエスケープ・XML のエスケープ・大文字小文字の違いを含めて置き換え、残っていれば `collect` は失敗します。`prepare` は展開先のパスで埋め戻します（空白・日本語・`&` を含んでもよい）。PLATEAU の元データ（`build\source`）、Environment Studio の cache、Urban の cache（World の高さモデル、初回に作り直す）は同梱しません。

packager は `work\` と、同梱するリポジトリの中の `build\` を（明示したもの以外）外すので、デモデータは ZIP の中の ZIP（`build\portable-runtime\urban-demo-data.zip`）として運びます。作成時の検証で `prepare` が staging に展開したものは、`staging_cleanup` で消してから ZIP にします。

## 作り方（Windows x64、`(hako)` シェル、`hakoniwa-business-pack` で実行）

### 1. リポジトリを最新にする

`windows-profile.json` の `repositories` にあるもの（urban-mobility、environment-studio、envsim、drone-core、threejs-drone、map-viewer、mbody-registry、mujoco-robots、pdu-registry、pdu-python、fpv-drone）と、Business Pack を最新にします。

### 2. レシピを configure する（Python パッケージを Foundation Python に入れる）

```powershell
python tools\recipe.py configure --recipe ..\hakoniwa-urban-mobility\recipes\usecases\urban-car-rc.yaml
python tools\recipe.py configure --recipe ..\hakoniwa-urban-mobility\recipes\usecases\urban-drone-rc.yaml
python tools\recipe.py configure --recipe ..\hakoniwa-urban-mobility\recipes\usecases\urban-drone-fleet.yaml
python tools\recipe.py configure --recipe ..\hakoniwa-urban-mobility\recipes\experiments\urban-mobility-rc.yaml
python tools\recipe.py configure --recipe ..\hakoniwa-urban-mobility\recipes\usecases\urban-fpv-rc.yaml
python ..\hakoniwa-urban-mobility\tools\urban_city_authoring.py configure
```

最後の行は、Environment Studio のレシピ（shapely、trimesh、lxml など）も configure します。`hakoniwa-drone-core\win\` がなければ、次で Drone Core の Windows 版を取得します。

```powershell
python ..\hakoniwa-urban-mobility\tools\urban_mobility.py prepare-native --recipe recipes\experiments\urban-mobility-rc.yaml
```

### 3. Plant を MIRROR=ON で組み立てる

```powershell
cd ..\hakoniwa-urban-mobility
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DHAKO_URBAN_ENABLE_MIRROR=ON -DHAKO_URBAN_ENABLE_VIEWER=ON -DBUILD_TESTING=ON
cmake --build build --config Release --target urban-car-hakoniwa-asset --parallel 4
type build\bin\urban-car-hakoniwa-asset.features.json
cd ..\hakoniwa-business-pack
```

`features.json` が `"plant_directive": true` になっていることを確かめます。Urban Studio で摩擦のあるデモ（3-4）を Configure しても、同じ設定で組み立てられます。

### 4. デモデータを用意する

デモの街（都庁 `tokyo-13104-multi-lat35_689-lon139_691`、`tocho-bridge-a-hull`、`sapporo-rotary-snow`）がこの Workspace の work にあれば、`collect` がそこから作ります。ない場合は、街がある Workspace（macOS）で作って持ってきます。

```bash
# macOS、(hako) シェル、hakoniwa-business-pack で
python ../hakoniwa-urban-mobility/tools/urban_portable.py bundle --output ~/urban-demo-data.zip
```

```powershell
# Windows で、持ってきた ZIP を指定する（build\portable-runtime\urban-demo-data.zip に置かれ、次からはそれを使う）
python ..\hakoniwa-urban-mobility\tools\urban_portable.py collect --demo-data C:\path\to\urban-demo-data.zip
```

### 5. collect と doctor

```powershell
python ..\hakoniwa-urban-mobility\tools\urban_portable.py collect
python ..\hakoniwa-urban-mobility\tools\urban_portable.py doctor
```

`collect` は、いちばん深いデモのファイルのパスの長さを表示します（都庁のファイルは `hakoniwa-business-pack\work\` から 169 文字）。

### 6. ZIP を作る

動いている Urban Studio、シミュレーション、Environment Studio を止めてから実行します。

```powershell
python ..\hakoniwa-urban-mobility\tools\urban_studio.py stop
python ..\hakoniwa-urban-mobility\tools\urban_city_authoring.py stop
python tools\package_portable_workspace.py --profile urban-studio
```

出力は `dist\hako-urban-studio-win64.zip` です。packager は `collect` と `doctor` を自分でも実行し、staging で `prepare` と `validation_imports` の import を確かめます。中身を見たいときは `--keep-staging`、取得済みの embeddable Python があれば `--python-embed-zip` を付けます。最後に表示される「Extraction folder path may be up to N characters」には、ZIP の中のデモデータの深さは入っていません。デモデータの分は手順 5 の表示で確かめます（`hako-urban-studio-win64\` を含めて約 195 文字なので、展開先は 60 文字ほどまで）。

### 7. 確かめる

1. 空白と日本語を含む短いパス（例 `C:\箱庭 デモ\`）に展開し、`start-urban-studio.bat` を実行する。ブラウザに Urban Studio が開く。
2. Simulation タブで、デモ 3-1〜3-5 をそれぞれ Configure → Start → Viewer を開く → Stop。黒いコンソール窓が開かないこと。3-4・3-5 で摩擦が効くこと（凍結で止まりきれない）。
3. City タブの「Environment Studio で作る」で小さな街を作り、Compose の World に出ること。
4. `status-urban-studio.bat`、`stop-urban-studio.bat` で全部止まること（ポート 28090・28097・28100・28865〜28867 が空く）。
5. フォルダを移動（名前を変える）して、もう一度 start し、デモが動くこと（`デモデータ: フォルダの移動に合わせてパスを書き換えました`）。
6. ポートの衝突：`python -m http.server 28100` などでポートを使った状態で start し、どのポートを誰が使っているかと直し方が表示されること。28090 を使った状態では start が止まること。
7. 結果を issue #82 に書く。

## 制限

- 画面は CDN（unpkg、jsdelivr）と OSM のタイルを読むので、インターネット接続が必要です。街の作成も PLATEAU と Overpass にアクセスします。
- MAX_PATH：展開先は 60 文字ほどまで。`prepare` は展開前に確かめ、長すぎれば短い場所を案内して止まります。
- Foundation Python に絶対パスの `.pth`（editable install）があると、packager が断ります。
