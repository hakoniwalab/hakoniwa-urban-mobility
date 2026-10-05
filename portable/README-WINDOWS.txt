Hakoniwa Urban Studio - Windows ポータブル版

街（PLATEAU の 3D 都市モデル）の中で、車・カート・ドローン・人を動かすシミュレーションを、
ブラウザの画面（Urban Studio）から組み立てて実行します。
Python / Git / WSL / Docker / C++ ビルド環境のインストールは不要です。


■ 最初に：展開先のフォルダの名前（重要）

展開先のフォルダのパスには、英数字と _ . - だけを使ってください。

  よい例：    C:\hako
              D:\work\hako-urban
  動かない例：C:\箱庭              （日本語）
              C:\hako demo         （空白）
              C:\Users\山田\Downloads （ユーザー名が日本語）
              C:\R&D\hako          （記号）

日本語・空白・記号の入ったパスに展開すると、初回の起動で止まります。
物理エンジン（MuJoCo）と車のシミュレーターは、英数字以外の文字を含むパスのファイルを開けません。
空白や記号は、起動の bat やコマンドの引数を崩します。
Windows のユーザー名が日本語や空白入りのときは、ダウンロードやデスクトップのフォルダも使えないので、
C:\hako のようなフォルダを作って、そこに展開してください。


■ 必要なもの

- Windows 10 / 11（64 ビット）
- ブラウザ（Microsoft Edge、Google Chrome など）
- インターネット接続
    画面の部品（3D 表示のライブラリ）と地図のタイルをインターネットから読み込みます。
    新しい街を作るときは、PLATEAU と OpenStreetMap からデータを取得します。
- ディスクの空き 3 GB 以上（初回の起動で、デモの街と計算用のキャッシュを作ります）
- 起動直後に「DLL が見つからない」と出る場合は、
  Microsoft Visual C++ 再頒布可能パッケージ（x64）をインストールしてください。


■ 展開

1. ZIP を右クリックして「すべて展開」を選びます。
   ZIP を開いたまま（エクスプローラーで ZIP の中を見ている状態）では動きません。
2. 展開先は C:\hako のような「英数字だけの短いパス」にしてください
   （上の「最初に：展開先のフォルダの名前」）。
   Windows のパスの長さの上限（260 文字）を超えても、初回の起動で止まります。

展開すると、次のファイルがあります。

  start-urban-studio.bat    起動
  status-urban-studio.bat   状態の確認
  stop-urban-studio.bat     停止
  README-WINDOWS.txt        この説明
  hakoniwa-*                プログラムとデータ（中身は変更しないでください）


■ 起動

1. start-urban-studio.bat をダブルクリックします。
   「Windows によって PC が保護されました」（SmartScreen）と出た場合は、
   配布元を確認したうえで「詳細情報」→「実行」を選んでください。
   Windows Defender ファイアウォールの確認が出た場合は、「プライベート ネットワーク」を許可してください
   （通信はこの PC の中だけです）。
2. 初回と、フォルダを移動したあとの最初の起動では、デモの街をこのフォルダ用に準備するので、
   少し時間がかかります。
3. 起動が終わると、ブラウザに Urban Studio（http://127.0.0.1:28090/）が開きます。


■ デモを動かす

Urban Studio の「Simulation」タブで Composition（シミュレーションの組み合わせ）を選び、

  Configure → Start → 「Viewer を開く」 → 見終わったら Stop

の順に押します。Configure は、その Composition を初めて動かすときと、
街や車両の組み合わせを変えたときだけ必要です（初回は数分かかることがあります）。

  3-1  demo31-tocho-drone-*   都庁でドローンを飛ばす（通常・突風・故障・もしも）
  3-2  demo32-*               都庁の外壁点検（北西・南東・南西の面、通常・突風・故障）
  3-3  demo33-a-current / demo33-a-hull
                              歩道橋の下をくぐる（当たり判定の作り方による違いの比較）
  3-4  demo34-*               都庁の南西の坂道を走る箱庭カート（乾燥・濡れ・雪・凍結の路面）
  3-5  demo35-*               雪の日の札幌駅南口ロータリー（凍結路面での停止、人とお祭りの屋台）

「Compose」タブで車両を置き換えたり、「Route」「Flight」タブで走る道や飛ぶ経路を作ったりして、
「保存して検証」したものも、同じように動かせます。


■ 画面（Viewer）の見方（重要：GPU）

シミュレーションは CPU だけで動きます。街の 3D 表示はブラウザが GPU で描きます。

- おすすめ：別の PC やスマホから操作・表示する
    同じネットワーク（同じサブネット）の Mac・PC・スマホから、次を開けます。
    このパソコンでブラウザを開く必要はありません。
      Urban Studio : http://<このパソコンの IP アドレス>:28090/
                     （start-urban-studio.bat の画面に表示されます）
      Viewer       : Simulation タブの「スマホ・別の PC で見る」で URL と QR コードを表示
    使うには、このパソコンのファイアウォールで、ポートを同じネットワークからだけ
    許可します。管理者の PowerShell で次を実行してください（使い終わったら 2 行目で消します）。
      New-NetFirewallRule -DisplayName "Hakoniwa Urban (LAN)" -Direction Inbound -Protocol TCP -LocalPort 28090,28100,28865-28867 -RemoteAddress LocalSubnet -Action Allow -Profile Any
      Remove-NetFirewallRule -DisplayName "Hakoniwa Urban (LAN)"
    ポートに届く人は、シミュレーションを動かしたり止めたり、展開したフォルダの中身を
    見たりできます。信頼できるネットワークだけで使ってください。
    新しい街を作る Environment Studio（28097）は、このパソコンからだけ使えます。

- このパソコンで見るとき：ブラウザを「高パフォーマンス」の GPU にする
    GPU が 2 つあるノートパソコン（内蔵 GPU と NVIDIA など）では、Windows は
    ブラウザを内蔵 GPU で動かします。内蔵 GPU が街の描画でいっぱいになると、
    同じ CPU で動くシミュレーションまで遅れて、1〜3 秒ごとに画面がカクつきます。
    1. 設定 → システム → ディスプレイ → グラフィック
    2. 使うブラウザ（Chrome / Edge）を選び、「オプション」→「高パフォーマンス」→ 保存
    3. ブラウザをすべて閉じて、開き直す
    （Chrome では chrome://gpu、Edge では edge://gpu で、使っている GPU を確認できます）


■ 新しい街を作る

1. 「City」タブの「Environment Studio で作る」を押します。
   Environment Studio（http://127.0.0.1:28097/map.html）が別のタブで開きます。
2. 地図で範囲を選んで City World を作ります（PLATEAU からデータを取得するので、
   範囲の広さによって数分〜数十分かかります）。
3. できた街は Urban Studio に自動で登録され、「Compose」タブの World に並びます。


■ 状態の確認と終了

- status-urban-studio.bat : Urban Studio、Environment Studio、動いているシミュレーションを表示します。
- stop-urban-studio.bat   : 動いているシミュレーション、Environment Studio、Urban Studio を止めます。
  ブラウザのタブを閉じただけでは止まりません。
  フォルダを削除・移動する前にも、必ず stop-urban-studio.bat を実行してください。


■ うまく動かないとき

- 「ポート 28090（urban-studio ...）は ... が使っています」
    別のプログラムがそのポートを使っています。表示されたプログラムを終了してから、
    もう一度 start してください。終了できないときは、メモ帳で
      hakoniwa-business-pack\work\urban\ports.yaml
    を作り、次のように空いている番号を書くと、そのポートを変えられます。
      ports:
        urban-studio: 38090
        viewer-http: 38100
    使うポート: 28090（Urban Studio）、28097（Environment Studio、変更できません）、
    28100（Viewer）、28865〜28867（WebBridge）
- 「展開先のフォルダのパスに、使えない文字…が入っています」
    C:\hako のような、英数字だけのフォルダに展開し直してください。
- 「展開先のフォルダのパスが長すぎます」
    C:\hako のような短い場所に展開し直してください。
- Viewer に何も表示されない、画面が真っ白
    インターネットに接続されているか確認してください。社内のプロキシなどで
    unpkg.com / cdn.jsdelivr.net への接続が止められていると表示できません。
- 画面が 1〜3 秒ごとにカクつく（RTF は 1.0 のまま）
    ブラウザが内蔵 GPU で描いています。「画面（Viewer）の見方」の手順で、
    ブラウザを「高パフォーマンス」の GPU にしてください。別の PC で見るのも有効です。
- Start が失敗する
    Simulation タブの出力の最後の行と、下のログを確認してください。
    Configure をもう一度実行すると直ることがあります。
- 起動に失敗する（原因不明）
    stop-urban-studio.bat を実行してから、もう一度 start してください。

ログの場所:
  Urban Studio           hakoniwa-business-pack\work\urban\studio\studio.log
  シミュレーション       hakoniwa-business-pack\work\recipes\<Recipe>\logs
  Environment Studio     hakoniwa-business-pack\work\recipes\environment-studio\studio\studio.log


同梱する各リポジトリのライセンスは、それぞれのフォルダにある LICENSE を参照してください。
