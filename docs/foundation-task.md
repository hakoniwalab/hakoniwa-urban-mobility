# Urban Mobility Foundation migration tasks

## 1. 目的

本書は [hakoniwa-urban-mobility Issue #2](https://github.com/hakoniwalab/hakoniwa-urban-mobility/issues/2)
を、一度に大規模変更せず、回帰を確認しながら進めるための実施チェックリストである。

現在成立している1台のCarと1台のDroneを回帰基準として基盤を整備し、その基盤上で
次のデモを構築する。

- 静岡PLATEAU City Worldを使用する
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

- [x] Urbanの最終Recipe IDを決める（`urban-mobility-shizuoka-rc`）
- [x] 単体回帰用Recipeと統合デモRecipeの関係を決める
- [x] 追跡対象の正本と`work/`生成物を分類する
- [x] Foundation requirementsとRecipe固有configureを分離する
- [x] Launcher owner、Conductor owner、Car plant owner、Drone physics ownerを明記する
- [x] HTTP、WebSocket、Core、PDU、Viewerのport・接続所有者を明記する
- [x] `plan / doctor / configure / start / status / stop / open-viewer`の責務を定義する

暫定的なRecipe構成案:

```text
Business Pack Recipe
  id: urban-mobility-shizuoka-rc
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

- [ ] Urban専用Recipe IDを`work/recipes/<recipe-id>/`へ解決する
- [ ] DroneのFleet、PDU、Bridge、Viewer、Launcher、MJB、PID生成物を移す
- [ ] Carの生成物も同じRecipe配下の責務別directoryへ配置する
- [ ] 単体回帰構成と統合構成が別Recipe生成物を上書きしないようにする
- [ ] 既存`drone-fleet-single-host` workspaceへ書き込まないことをテストする
- [ ] logs、validation、runtime sessionの配置を揃える

想定レイアウト:

```text
work/recipes/urban-mobility-shizuoka-rc/
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

## 9. Step 5: Foundation要求と依存sourceをRecipeへ宣言する

- [ ] Business Pack側にUrban用Recipe manifestを追加する
- [ ] `foundation_contract.mode: required`を宣言する
- [ ] Core PRO、PDU Python、Endpoint、Bridgeの必要Capabilityを宣言する
- [ ] 必要なbuild limitを実測したAsset/PDU/Service数から算出する
- [ ] Urban、Drone PRO、robot-runtime、MuJoCo robots、MBody registry、Viewer等をRecipe local requirementとして整理する
- [ ] private repositoryやlicenseの前提をagency boundaryへ記述する
- [ ] `recipe.py plan`が全sourceとFoundation要求を表示することを確認する
- [ ] `recipe.py doctor`が不足・不整合を起動前に説明することを確認する

完了条件:

- 正常なinstalled Foundationは再buildせず再利用される
- 必須sourceまたはArtifactが欠けた場合、configure開始前に停止する
- Foundation要求とUrban runtime設定が混在していない

## 10. Step 6: 標準ライフサイクルとpreflightを統一する

- [ ] Business Packの標準入口からplan、doctor、configureへ到達できる
- [ ] Urbanの薄いwrapperからstart、status、stop、open-viewerへ到達できる
- [ ] `start`前にFoundation SATISFIEDを確認する
- [ ] Launcher session RUNNINGとDemo Readyを別々に判定する
- [ ] 使用port、既存session、残存process、HTTP、WebSocketをpreflightする
- [ ] 別Recipeがportを所有している場合、誤ったURLを開かず明示的に停止する
- [ ] `open-viewer`は対象RecipeがRUNNINGかつHTTP応答可能な場合だけURLを開く
- [ ] `stop`後に子processとportが残らないことを確認する

完了条件:

- 404画面や別Recipeのstatusを正常結果として表示しない
- startを二重実行した場合、対象sessionを示す明確なエラーになる
- stop、再start、ブラウザ再接続が同じ手順で成功する

## 11. Step 7: 1 Carと1 Droneの回帰を新基盤へ移す

機能追加の前に、現在の単体構成を新しいRecipe/workspaceで再現する。

- [ ] 1 Carのconfigure、doctor、start、操作、Viewer、stopを確認する
- [ ] 1 Droneのconfigure、doctor、start、操作、Viewer、stopを確認する
- [ ] Carの起動時ENU pose変更がCity再構築なしで反映される
- [ ] Droneの起動時ENU pose変更がCity再構築なしで反映される
- [ ] Urban管理PIDの変更が`stop -> start`で反映される
- [ ] `configure -> start`でもUrban管理PIDが復元される
- [ ] Collider、地図、搭載カメラのUIが両方で同じ操作感になる

完了条件:

- Step 0の回帰項目をすべて満たす
- 旧workspace共有や関数差し替えへ戻す互換経路を必要としない

## 12. Step 8: 1 Drone＋複数Carの統合Recipeを作る

- [ ] 静岡City Receiptを1個だけ選択する
- [ ] EAMS Hexa Drone 1台をPS4 RC構成で生成する
- [ ] Golf Cart台数をRecipeから指定できるようにする
- [ ] Carの走行内容をRecipe本体ではなくscenario fileから読み込む
- [ ] 静岡の道路上に往路・復路と安全な折返し点を定義する
- [ ] 複数Carの開始位置と開始時刻をずらし、初期重なりを防ぐ
- [ ] Drone assetだけがConductorを所有し、Car asset側では起動しない
- [ ] 1個のLauncherへDrone、Car、controller、Bridge、Viewerを統合する
- [ ] 1個のThree.js画面で全車両とDroneを表示する
- [ ] Drone監視カメラ、左下地図、任意のCollider表示を維持する

完了条件:

- DroneをRC操作しながら複数Carが静岡の道路を継続して往復する
- Car同士のnamespace、command、poseが混線しない
- exactly one Conductor ownerで共通シミュレーション時刻が進む
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
- [ ] 1、2、目標台数のCarで性能を比較する
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
