# Golf Cart 1台をPS5コントローラで操作する

`urban-car-one.yaml`を使い、北海道City World上のGolf Cart 1台を
DualSenseで操作し、Three.jsブラウザにCity GLBと緑色のColliderを表示する手順です。
ネイティブMuJoCo Viewerは起動しません。

## 前提

- macOSホストでBusiness PackのFoundationが構築済みであること
- `hakoniwa-urban-mobility`のCarアプリケーションがビルド済みであること
- `urban-car-one.yaml`が参照するCity World receiptとGolf Cartモデルが存在すること
- PS5 DualSenseコントローラをUSBまたはBluetoothでMacへ接続しておくこと

この手順では、すべての`multi_car.py`コマンドに同じ設定を指定します。

```text
--config recipes/experiments/urban-car-one.yaml
```

これを省略すると、別の既定レシピ`recipes/multi-car-viewer.yaml`が選択され、
`status`や`stop`が今回のシミュレーションを参照しません。

## 1. 箱庭Workspaceへ入る

新しいターミナルを開き、Business Pack workspaceへ移動します。

```bash
cd /Users/tmori/project/business-pack
source hakoniwa-business-pack/work/foundation/activate
cd hakoniwa-urban-mobility
```

すでにプロンプトに`(hako)`が表示されている場合、`source`は省略できます。

## 2. 構成を診断する

```bash
python3 tools/multi_car.py doctor \
  --config recipes/experiments/urban-car-one.yaml
```

City MJCF/GLB、Golf Cart、Foundation、WebBridge、Three.jsなどがすべて
`[OK]`になることを確認します。

## 3. 実行ファイル一式を生成する

初回実行時、およびレシピやCity Worldを変更した場合に実行します。

```bash
python3 tools/multi_car.py configure \
  --config recipes/experiments/urban-car-one.yaml
```

生成先は`work/urban-car-one/`です。この処理では、City WorldとGolf Cartを
合成したMJCF/MJB、Runtime manifest、Launcher、WebBridge設定、通常表示と
Collider表示用のThree.js設定を生成します。

## 4. PS5コントローラを確認する

```bash
python3 tools/multi_car.py check-ps5 \
  --config recipes/experiments/urban-car-one.yaml
```

次のようにDualSenseが選択されれば準備完了です。

```text
[INFO] Selected Joystick 0: DualSense Wireless Controller
```

## 5. シミュレーションを起動する

```bash
python3 tools/multi_car.py start \
  --config recipes/experiments/urban-car-one.yaml
```

Launcherは以下をバックグラウンドで起動します。

- ViewerなしのMuJoCo Car plant
- Car-1用PS5 RC sender
- Three.js用WebBridge
- `127.0.0.1:8000`のHTTP server

## 6. 起動状態を確認する

```bash
python3 tools/multi_car.py status \
  --config recipes/experiments/urban-car-one.yaml
```

対象セッションが起動していれば`RUNNING`と表示されます。

## 7. Collider付きThree.js Viewerを開く

```bash
python3 tools/multi_car.py open-viewer --colliders \
  --config recipes/experiments/urban-car-one.yaml
```

ブラウザでは次を重ねて表示します。

- City Worldの表示用GLB
- Golf CartのGLB
- City Worldの物理Collider（緑色ワイヤーフレーム）
- Golf Cart前方カメラ（右上のPiP）

Colliderを表示しない場合は`--colliders`を外します。

```bash
python3 tools/multi_car.py open-viewer \
  --config recipes/experiments/urban-car-one.yaml
```

## 8. PS5コントローラで運転する

- 左スティック左右: ステアリング
- 右スティック上下: 前進／後退
- スティックを中央へ戻す: 速度／操舵指令をゼロへ戻す

このレシピの上限は速度`3.5 m/s`、操舵角`0.70 rad`、デッドゾーン`0.06`です。

## 9. 終了する

```bash
python3 tools/multi_car.py stop \
  --config recipes/experiments/urban-car-one.yaml
```

終了確認:

```bash
python3 tools/multi_car.py status \
  --config recipes/experiments/urban-car-one.yaml
```

`TERMINATED`になれば、Car plant、PS5 sender、WebBridge、HTTP serverは終了しています。
ブラウザのタブは必要に応じて閉じてください。

Workspace環境から抜ける場合は次を実行します。

```bash
deactivate_hakoniwa
```

## トラブルシュート

### `status`が別のPIDや`TERMINATED`を示す

`--config recipes/experiments/urban-car-one.yaml`が付いているか確認してください。
省略時は既定のmulti-Carレシピを参照します。

### PS5コントローラが見つからない

macOS側でDualSenseが接続済みか確認し、`check-ps5`を再実行してください。
シミュレーション起動後に接続した場合は、一度`stop`してから`start`し直します。

### ブラウザを開いても接続されない

先に`start`を実行し、`status`が`RUNNING`であることを確認してください。
レシピ変更後に`configure`していない場合は、停止後に`configure`からやり直します。

### Collider設定がないと言われる

次を再実行してCollider用Three.js設定を生成します。

```bash
python3 tools/multi_car.py configure \
  --config recipes/experiments/urban-car-one.yaml
```
