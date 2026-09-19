# Urban Mobility Foundation migration tasks

## 1. 目的

本書は [hakoniwa-urban-mobility Issue #2](https://github.com/hakoniwalab/hakoniwa-urban-mobility/issues/2)
を、一度に大規模変更せず、回帰を確認しながら進めるための実施チェックリストである。

現在成立している1台のCarと1台のDroneを回帰基準として基盤を整備し、その基盤上で
次のデモを構築する。

- City World ReceiptをRecipe入力とし、初回回帰では静岡PLATEAUを使用する
- EAMS Hexa Droneを1台、PS4コントローラでRC操作する
- Golf Cartを複数台、シナリオに従って道路上で往復させる
- 1個のLauncherと1個のConductor ownerで全Assetを起動する
- 1個のThree.js画面でCity、Drone、Car、地図、搭載カメラを確認する

本作業は物理近似や制御方式を変更するものではない。Foundation、Recipe、Urban固有処理の
所有境界を整理し、再現可能な構築・起動経路を作ることが目的である。

## 2. 進め方の原則

- 各Stepは独立したcommitにし、完了条件を満たしてから次へ進む。
- 構造変更とデモ機能追加を同じStepに混ぜない。
- `work/`の生成物を正本にしない。追跡対象の入力から再生成できることを確認する。
- Foundationは共有するが、Recipe生成物は`work/recipes/<recipe-id>/`へ分離する。
- FoundationのCore configとmmapは共有runtimeであり、同時実行を保証するものではない。
- LauncherとConductorのownerは、それぞれ1個に限定する。
- Urban固有のCity合成、車両シナリオ、Drone RC、Mirror、Viewer構成はUrban側に残す。
- workdir、Foundation要求評価、installed Receipt確認、共通Python解決はBusiness Pack契約へ揃える。
- 既存Recipeの処理は明示的な入出力を持つ部品として再利用し、関数差し替えは行わない。
- macOSで成立した現在の機能を回帰基準とし、対応OSの拡大は本作業へ混ぜない。

## 3. 現在の回帰基準

基盤変更前の基準revisionは、作業開始時に各repositoryのcommit IDとして記録する。
Urban側の開始候補は次である。

```text
hakoniwa-urban-mobility: b396d93
```

現時点で個別に成立している機能は次のとおり。

- `urban-car-one.yaml`
  - 静岡City World
  - Golf Cart 1台
  - PS5 RC操作
  - 起動時のENU初期位置反映
  - Three.js、左下地図、前方カメラ、Collider表示
- `urban-drone-one.yaml`
  - 静岡City World
  - EAMS Hexa Drone 1台
  - PS4 RC操作
  - 起動時のENU初期位置反映
  - Urban管理のPID正本を`start`時に再適用
  - Three.js、左下地図、監視カメラ、Collider表示
- 共通制約
  - CarとDroneの単体Recipeは同じHTTP/WebSocket portを使用するため同時起動しない
  - Drone側は`drone-fleet-single-host`のRecipe workspaceを暫定利用している
  - `drone_one.py`は既存Recipeの`write_launcher`を差し替えている

## 4. Step 0: 基盤変更前の証跡を固定する

目的は、リファクタリング後に同じ動作へ戻れる比較対象を作ることである。このStepでは
構成生成ロジックを変更しない。

- [x] 関連repositoryのremote URL、branch、commit ID、dirty stateを記録する
- [x] Foundation installed Receiptと利用中のPython、MuJoCo versionを記録する
- [x] 使用する静岡City World Receiptのpathとjob IDを記録する
- [x] `urban-car-one`をconfigure、doctor、start、status、HTTP/WebSocket確認、stopまで実行する
- [x] `urban-drone-one`をconfigure、doctor、start、status、HTTP/WebSocket確認、stopまで実行する
- [x] CarのPS5操作、前方カメラ、地図、Collider表示を確認する
- [x] DroneのPS4操作、監視カメラ、地図、Collider、PID再適用を確認する
- [x] 実行コマンド、結果、既知の制約を`foundation-baseline.md`へ保存する

完了条件:

- 1 Carと1 Droneを個別に再現できる手順とrevisionが残っている
- 失敗時に比較できるログ、設定、画面確認項目が定義されている
- 未追跡の一時ログをcommit対象へ含めていない

## 5. Step 1: 最終的な所有境界とRecipe契約を固定する

実装前に、入力、生成物、共有物、runtime ownerを表にして固定する。

- [x] Urbanの最終Recipe IDを決める（都市非依存の`urban-mobility-rc`）
- [x] 単体回帰用Recipeと統合デモRecipeの関係を決める
- [x] 追跡対象の正本と`work/`生成物を分類する
- [x] Foundation requirementsとRecipe固有configureを分離する
- [x] Launcher owner、Conductor owner、Car plant owner、Drone physics ownerを明記する
- [x] HTTP、WebSocket、Core、PDU、Viewerのport・接続所有者を明記する
- [x] `plan / doctor / configure / start / status / stop / open-viewer`の責務を定義する

暫定的なRecipe構成案:

```text
Business Pack Recipe
  id: urban-mobility-rc
  Foundation要求、source要求、標準workspace、標準入口を所有

Urban composition config
  City Receipt、Drone、Car台数、操作方法、Viewer、scenario選択を所有

Urban scenario
  Carごとの開始位置、往復経路、速度、開始時刻を所有
```

完了条件:

- 同じ値を複数の正本へ記述しない設計になっている
- Urban固有処理をBusiness Packへ移しすぎていない
- Business Pack共通処理をUrbanで再実装しない設計になっている

## 6. Step 2: workdirとFoundation環境解決を共通化する

このStepでは生成物の場所だけを正し、Car/Droneの構成内容は変更しない。

- [x] `multi_car.py`の固定`work/foundation/install`参照を共通resolverへ置き換える
- [x] `drone_one.py`と`drone_car_rc.py`も同じresolverを使用する
- [x] Foundation Python、install prefix、config、runtimeを個別に文字列連結しない
- [x] 標準workdirで単体テスト、doctor、1 Car/1 Drone回帰を実行する
- [x] 一時的な別`HAKONIWA_WORK_DIR`でCar/Droneの解決先を単体テストする
- [x] 標準workdirと変更workdirのFoundation参照が混ざらないことを確認する

完了条件:

- 3ツールが同じBusiness Pack rootとworkdirを解決する
- Foundation Pythonとinstalled Receiptが同じprefixを指す
- hard-codedな`hakoniwa-business-pack/work/foundation/install`参照が残っていない

## 7. Step 3: Drone Fleet生成処理を明示的APIへ分離する

Urban専用workspaceを作る前に、既存Drone Fleet Recipeから必要な生成処理を安全に
再利用できるようにする。

再利用する公開入口は`configure`、`doctor`、`start`、`control`とし、標準Launcher生成は
`write_launcher`を使用する。Urbanは明示的な`workspace`と`launcher_writer`を渡し、
標準Recipeは両引数を省略して従来の`ROOT` / `RECIPE_ID`と標準Launcherを使用する。

- [x] `drone_fleet_single_host`の再利用対象処理を列挙する
- [x] workspace、experiment、出力先、Launcher hookを明示的な引数にする
- [x] module globalの`RECIPE_ID`へ依存しない生成APIを用意する
- [x] `base.write_launcher = ...`による関数差し替えを不要にする
- [x] 既存Drone Fleet Recipeが同じAPIで従来動作することを確認する
- [x] Urban側が公開API以外のprivate/global状態へ依存しないことを確認する

完了条件:

- 既存Drone Fleet Recipeのテストとsmokeが成功する
- Urban用Launcherをcallbackまたは生成後の明示的処理で構成できる
- import順序によってLauncher生成結果が変化しない

## 8. Step 4: Urban専用Recipe workspaceを導入する

- [x] Urban専用Recipe IDを`work/recipes/<recipe-id>/`へ解決する
- [x] DroneのFleet、PDU、Bridge、Viewer、Launcher、MJB、PID生成物を移す
- [x] Carの生成物も同じRecipe配下の責務別directoryへ配置する
- [x] 単体回帰構成と統合構成が別Recipe生成物を上書きしないようにする
- [x] 既存`drone-fleet-single-host` workspaceへ書き込まないことをテストする
- [x] logs、validation、runtime sessionの配置を揃える

想定レイアウト:

```text
work/recipes/urban-mobility-rc/
  config/
    foundation-requirements.yaml
    launcher.json
    drone/
    car/
    bridge/
    viewer/
  assets/
  missions/
  logs/
  runtime/
  validation/
```

完了条件:

- Urbanのconfigure後も既存Drone Fleet Recipeの生成物が変化しない
- Urbanのstop/statusがUrban自身のsession fileだけを操作する
- workspaceを削除してconfigureすれば追跡済み入力から再生成できる

検証結果:

- `urban-car-one`と`urban-drone-one`を空の専用workspaceへShizuoka入力から生成した
- 両Recipeで`doctor`と`start -> status -> stop`が成功した
- 両sessionは各Recipeの`runtime/launcher-session.json`だけを使用した
- 実行前後の`drone-fleet-single-host`全ファイル内容ハッシュは
  `caed9cab5167e2f392d761c46c94d669966a1333`で一致した
- stop後、HTTP 8000、WebSocket 8765、Launcher 54111にlistenerが残っていない

## 9. Step 5: Foundation要求と依存sourceをRecipeへ宣言する

- [x] Urban側にmanaged Recipe manifestを追加し、Business Pack Recipe engineから評価する
- [x] `foundation_contract.mode: required`を宣言する
- [x] Core PRO、PDU Python、Endpoint、Bridgeの必要Capabilityを宣言する
- [x] 必要なbuild limitを実測したAsset/PDU/Service数から算出する
- [x] Urban、Drone PRO、robot-runtime、MuJoCo robots、MBody registry、Viewer等をRecipe local requirementとして整理する
- [x] private repositoryやlicenseの前提をagency boundaryへ記述する
- [x] `recipe.py plan`が全sourceとFoundation要求を表示することを確認する
- [x] `recipe.py doctor`が不足・不整合を起動前に説明することを確認する

完了条件:

- 正常なinstalled Foundationは再buildせず再利用される
- 必須sourceまたはArtifactが欠けた場合、configure開始前に停止する
- Foundation要求とUrban runtime設定が混在していない

検証結果:

- 都市非依存の`recipes/experiments/urban-mobility-rc.yaml`をUrban側へ追加した
- `plan`は8個のRecipe-local sourceをすべて既存checkoutとして再利用し、Foundationを`SATISFIED`と判定した
- `doctor`は4個のFoundation componentと8個のRecipe-local sourceをすべて`SATISFIED`と判定した

## 10. Step 6: 標準ライフサイクルとpreflightを統一する

- [x] Business Packの標準入口からplan、doctor、configureへ到達できる
- [x] Urbanの薄いwrapperからstart、status、stop、open-viewerへ到達できる
- [x] `start`前にFoundation SATISFIEDを確認する
- [x] Launcher session RUNNINGとDemo Readyを別々に判定する
- [x] 使用port、既存session、残存process、HTTP、WebSocketをpreflightする
- [x] 別Recipeがportを所有している場合、誤ったURLを開かず明示的に停止する
- [x] `open-viewer`は対象RecipeがRUNNINGかつHTTP応答可能な場合だけURLを開く
- [x] `stop`後に子processとportが残らないことを確認する

完了条件:

- 404画面や別Recipeのstatusを正常結果として表示しない
- startを二重実行した場合、対象sessionを示す明確なエラーになる
- stop、再start、ブラウザ再接続が同じ手順で成功する

検証結果:

- `tools/urban_mobility.py`を都市非依存Recipeの標準入口として追加し、`plan / doctor / configure`がBusiness Pack Recipe engineへ委譲されることを確認した
- Launcher session、HTTP、WebSocketを別々に判定し、`demo_ready`は3条件が揃った場合だけ真になる
- 二重start、別Launcherのsession、占有port、HTTP未準備、stop後の残存portを単体テストで固定した
- 統合Launcherの実生成とstartはStep 8で行う。Step 6では入口と失敗時の安全性を先に確立した

## 11. Step 7: 1 Carと1 Droneの回帰を新基盤へ移す

機能追加の前に、現在の単体構成を新しいRecipe/workspaceで再現する。

- [x] 1 Carのconfigure、doctor、start、操作、Viewer、stopを確認する
- [x] 1 Droneのconfigure、doctor、start、操作、Viewer、stopを確認する
- [x] Carの起動時ENU pose変更がCity再構築なしで反映される
- [x] Droneの起動時ENU pose変更がCity再構築なしで反映される
- [x] Urban管理PIDの変更が`stop -> start`で反映される
- [x] `configure -> start`でもUrban管理PIDが復元される
- [x] Collider、地図、搭載カメラのUIが両方で同じ操作感になる

完了条件:

- Step 0の回帰項目をすべて満たす
- 旧workspace共有や関数差し替えへ戻す互換経路を必要としない

検証結果（2026-09-19、macOS、静岡City World）:

- Carは専用`work/recipes/urban-car-one` sessionで起動し、DualSense認識、HTTP 200、WebSocket 101と`UrbanFleet`配信を確認した
- Droneは専用`work/recipes/urban-drone-one` sessionで起動し、HTTP 200、WebSocket 101と`DroneVisualStatePublisher`配信を確認した
- Droneの実行用PIDファイルはUrban正本`config/drone/eams-rc-controller-params.txt`とSHA-1が一致した
- 6ローター、監視カメラ、左下地図、任意Colliderの生成設定を確認した。Car側も前方カメラ、左下地図、任意Colliderを維持している
- 両構成ともstop後はLauncherが`TERMINATED`となり、8000、8765、54111にlistenerが残らないことを確認した
- PS5/PS4の実操作と画面上の操作感はStep 0で確認済みであり、Step 7では同じ生成契約とデータ経路が新workspaceで維持されることを再確認した

## 12. Step 8: 1 Drone＋複数Carの統合Recipeを作る

### 8.1 静岡の道路ルートを確定する

- [x] 静岡City Receiptを1個だけ選択する
- [x] PLATEAU由来の道路面と同じ座標源から、広い道路上の閉ループ基準軌道を定義する
- [x] 基準軌道とCar初期位置をThree.jsのデバッグoverlayで事前表示する
- [x] 全車両の初期footprintと走行目標が道路面内に収まることを検証する
- [x] 建物Collider、道路外形、急旋回、狭窄部との干渉を起動前に検出する

進捗（2026-09-19）:

- 静岡専用候補`recipes/scenarios/shizuoka-five-car-formation-loop.yaml`を追加した
- PLATEAUの`roadway / lane / intersection`を正本として、5台それぞれの全周swept footprintと初期footprintを検証する`tools/route_surface_preview.py`を追加した
- 中央交差点内に長さ95.47 m、最小旋回半径3.35 mの候補ループを生成し、全5台が道路面内に収まることを自動確認した
- 軌道、5台分の走行線、初期footprint、City World Colliderを重ねるThree.js用GLBを生成した
- Three.jsで軌道とColliderの非干渉を目視確認し、`approval_status: approved`としてStep 8.1を完了した

### 8.2 5台の`1 + 2 + 2`編隊制御を作る

- [x] Golf Cart台数を5台としてscenario fileに宣言する
- [x] Carの走行内容をRecipe本体ではなく静岡専用scenario fileから読み込む
- [x] 先頭の`Car-1`と、その後方2列に左右2台ずつを配置する
- [x] 各車両へ基準軌道に対する前後方向と横方向のoffsetを設定できるようにする
- [x] カーブでは基準軌道の接線と法線から各車両の目標位置を計算する
- [x] 既存10台デモと同じ`external_python`方式で、1個のscenario executorが5台を制御する
- [x] Car用PS5 senderを統合デモのLauncherへ追加しない
- [x] Carだけの構成で初期重なり、編隊走行、旋回、連続周回を確認する

目標配置（進行方向は左）:

```text
          Car-2       Car-4
Car-1     Car-3       Car-5
<-- 進行方向
```

初期値は前後間隔4.5 m、左右offsetは`+1.8 m / -1.8 m`とし、道路幅と車体footprintの検証結果に応じてscenario側で調整する。

進捗（2026-09-19）:

- schema v3へ`lateral_offset_m`を追加し、schema v2は横offset 0 mとして互換維持した
- `urban-car-five-formation.yaml`から静岡専用scenarioを読み、5台の初期ENU poseを生成した
- 1個のLauncherへCar plant、1個のscenario executor、Bridge、HTTP serverを生成し、PS5 senderが含まれないことを確認した
- 共有メモリ上で5台すべてのpose更新と編隊目標への追従を確認した
- 自動テスト64件に成功し、Three.jsで5台の編隊表示と走行を目視確認してStep 8.2を完了した
- Map ViewerがHTML既定のWebSocket URIをconfigより優先する問題を修正し、5台用Bridge（8766）へ自動接続することを確認した

### 8.3 Droneの屋上発進を作る

- [ ] 静岡モデル内から、平らで離陸余裕のある建物屋上を1か所選択する
- [ ] 屋上中央のENU位置、屋上面高さ、yaw、脚のclearanceをscenarioに記録する
- [ ] EAMS Hexa Drone 1台をPS4 RC構成で屋上へ配置する
- [ ] Drone単体で屋上静止、離陸、道路上空への移動を確認する
- [ ] プロペラ、脚、機体が屋上や周辺建物へ初期干渉しないことを確認する

### 8.4 LauncherとViewerを統合する

- [ ] Drone assetだけがConductorを所有し、Car asset側では起動しない
- [ ] 1個のLauncherへDrone、Car plant、scenario executor、PS4 controller、Bridge、HTTP serverを統合する
- [ ] 1個のThree.js画面でDrone 1台とCar 5台を表示する
- [ ] メイン3D画面、右上のDrone監視カメラ、左下地図の構成にする
- [ ] 統合デモではCar搭載カメラを生成せず、Drone搭載カメラだけを表示する
- [ ] Colliderと基準軌道のoverlayをそれぞれオプションでON/OFFできるようにする
- [ ] 単体`urban-car-one`の前方カメラは変更せず、既存の単体回帰を維持する

完了条件:

- Droneを屋上からPS4 RC操作しながら、Car 5台が静岡の道路面に沿って`1 + 2 + 2`編隊で継続周回する
- Car同士のnamespace、command、poseが混線しない
- exactly one Conductor ownerで共通シミュレーション時刻が進む
- ブラウザに表示する搭載カメラはDrone監視カメラ1個だけである
- Viewerを表示しないheadless smokeでも状態更新を検証できる

## 13. Step 9: 回帰・再現性・性能を確認する

- [ ] 単体テストを全件実行する
- [ ] Business Pack Recipe validationを実行する
- [ ] 標準workdirでclean configureから統合デモを再現する
- [ ] 変更workdirでplan、doctor、configureの分離を確認する
- [ ] start、status、stop、再startを連続して確認する
- [ ] Launcher、Conductor、HTTP、WebSocketの残存processがないことを確認する
- [ ] MJBを利用し、start時にCity XMLを再コンパイルしないことを確認する
- [ ] configure時間、start時間、実時間係数を記録する
- [ ] 1、2、5台のCarで性能を比較する
- [ ] 失敗時のログ位置と診断コマンドを手順書へ記載する

完了条件:

- Step 0の単体回帰とStep 8の統合デモが両方成功する
- clean workspaceから第三者が同じ入口で再現できる
- 性能低下がある場合、数値と原因が記録されている

## 14. Step 10: ドキュメントとIssueを完了する

- [ ] READMEの入口を実際の標準コマンドへ揃える
- [ ] 単体Car、単体Drone、統合デモの手順を分けて記載する
- [ ] Recipe、scenario、PID、生成物の編集場所を明記する
- [ ] LauncherとConductorのownerを構成図で明記する
- [ ] Foundation SATISFIED、Launcher RUNNING、Demo Readyの違いを記載する
- [ ] Issue #2へrevision、実行結果、残課題をコメントする
- [ ] Issue #2のチェックリストを実結果に合わせて更新する
- [ ] 対象外項目を新規Issueへ分離し、Issue #2をcloseする

## 15. 各Step共通の確認テンプレート

各Stepの完了時は最低限、次を記録する。

```text
Step:
Date:
Repositories and revisions:
Changed ownership/contract:
Commands executed:
Automated tests:
Manual checks:
Generated workspace:
Known limitations:
Rollback point:
```

次のStepへ進める条件は、変更をcommitしたことではなく、そのStepの完了条件と直前までの
回帰項目が成立したことである。
