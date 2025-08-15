# MailAI バックエンド

MailAI アプリケーションのバックエンドです。

-----

## インストール

1.  **リポジトリをクローンする:**

    ```bash
    git clone <repository-url>
    cd mailai-backend
    ```

2.  **仮想環境を作成する:**

    ```bash
    python -m venv .venv
    ```

3.  **仮想環境をアクティベートする:**

      * **Windows:**
        ```bash
        .venv\Scripts\activate
        ```
      * **macOS/Linux:**
        ```bash
        source .venv/bin/activate
        ```

4.  **依存関係をインストールする:**

    ```bash
    pip install -r "requirements.txt"
    ```

-----

## アプリケーションの実行

アプリケーションを実行するには、次のコマンドを実行します。

```bash
Flask run
```

または

```bash
python app.py
```

-----

## Redis のセットアップ (Windows と WSL の場合)

1.  **WSL に Redis をインストールする:**
    WSL ターミナルを開き、次のコマンドを実行します。

    ```bash
    sudo apt-get update
    sudo apt-get install redis-server
    ```

2.  **Redis サーバーを起動する:**

    ```bash
    sudo service redis-server start
    ```

3.  **Redis が実行されていることを確認する:**

    ```bash
    redis-cli ping
    ```

    `PONG` と表示されるはずです。

-----

## MongoDB Compass のセットアップ

1.  **MongoDB Compass をダウンロードしてインストールする:**
    [MongoDB Compass ダウンロードページ](https.www.mongodb.com/try/download/compass) にアクセスし、お使いのオペレーティングシステム用のインストーラーをダウンロードします。

2.  **MongoDB Compass をインストールする:**
    ダウンロードしたインストーラーを実行し、画面の指示に従います。

3.  **MongoDB データベースに接続する:**
    インストール後、MongoDB Compass を開き、接続文字列を使用して MongoDB データベースに接続します。

-----

## Ngrok のセットアップ

1.  **ngrok にサインアップする:**
    [ngrok ダッシュボード](https://dashboard.ngrok.com/signup) にアクセスし、無料アカウントを作成します。

2.  **ngrok をインストールする:**
    お使いのオペレーティングシステム用の [ngrok ダウンロードページ](https://ngrok.com/download) の手順に従います。

3.  **認証トークンを設定する:**
    ngrok のインストール後、ダッシュボードの認証トークンを使用してアカウントを接続します。

    ```bash
    ngrok config add-authtoken <YOUR_AUTHTOKEN>
    ```

4.  **無料の静的ドメインを取得する:**
    ngrok ダッシュボードで、**Cloud Edge** \> **Domains** に移動し、新しいドメインを作成します。これにより、再利用できる静的 URL が得られます。

5.  **静的ドメインでトンネルを開始する:**
    静的ドメインを使用してローカルポートをインターネットに公開するには、次のコマンドを使用します (例: ポート 5000 の場合)。

    ```bash
    ngrok http --domain=<YOUR_STATIC_DOMAIN> 5000
    ```

    `<YOUR_STATIC_DOMAIN>` を、前のステップで作成したドメインに置き換えます。

    静的ドメインを持っていない場合は、このコマンドを使用します。

    ```bash
    ngrok http 5000
    ```

    この場合、ngrok を起動するたびに URL が更新されます。

-----

## 環境変数

このプロジェクトでは、環境変数を管理するために `.env` ファイルを使用します。プロジェクトのルートディレクトリに `.env` という名前のファイルを作成し、次の変数を追加します。

### Google Cloud & Gmail API

  * `GOOGLE_CLIENT_ID`: Google Cloud プロジェクトのクライアント ID。
  * `GOOGLE_CLIENT_SECRET`: Google Cloud プロジェクトのクライアント シークレット。
  * `GOOGLE_REDIRECT_URI`: Google Cloud プロジェクトのリダイレクト URI。
  * `GMAIL_PUB_SUB_TOPIC`: Gmail プッシュ通知用の Google Cloud Pub/Sub トピック名。
  * `GCP_PROJECT_ID`: Google Cloud プロジェクト ID。

### Microsoft Graph & Entra アプリ

  * `MS_GRAPH_CLIENT_ID`: Microsoft Entra アプリケーションのクライアント ID。
  * `MS_GRAPH_CLIENT_SECRET`: Microsoft Entra アプリケーションのクライアント シークレット。
  * `MS_GRAPH_REDIRECT_URI`: Microsoft Entra アプリケーションのリダイレクト URI。
  * `MS_GRAPH_TENANT_ID`: Microsoft Entra アプリケーションのテナント ID。
  * `MS_GRAPH_WEBHOOK_NOTIFICATION_URL`: Microsoft Graph が webhook 通知を送信する URL。

-----

## Microsoft Entra アプリの作成

1.  **Microsoft Entra 管理センターにサインインする:**
    [https://entra.microsoft.com/](https://entra.microsoft.com/) にアクセスし、管理者アカウントでサインインします。

2.  **「アプリの登録」に移動する:**
    左側のナビゲーション ウィンドウで、**ID** \> **アプリケーション** \> **アプリの登録** に移動します。

3.  **新しいアプリの登録を作成する:**

      * **新しい登録** をクリックします。
      * アプリケーションに名前を付けます (例: `MailAI-Backend`)。
      * **サポートされているアカウントの種類** で、**任意の組織ディレクトリ内のアカウント (任意の Microsoft Entra ID テナント - マルチテナント) と個人用 Microsoft アカウント (Skype、Xbox など)** を選択します。
      * **登録** をクリックします。

4.  **クライアント ID とテナント ID を取得する:**

      * アプリが作成されると、アプリの **概要** ページが表示されます。
      * **アプリケーション (クライアント) ID** と **ディレクトリ (テナント) ID** をコピーします。これらがそれぞれ `MS_GRAPH_CLIENT_ID` と `MS_GRAPH_TENANT_ID` になります。

5.  **クライアント シークレットを作成する:**

      * アプリの左側のナビゲーション ウィンドウで、**証明書とシークレット** に移動します。
      * **新しいクライアント シークレット** をクリックします。
      * シークレットに説明を付け、有効期限を選択します。
      * **追加** をクリックします。
      * **重要:** クライアント シークレットの **値** をすぐにコピーしてください。これが `MS_GRAPH_CLIENT_SECRET` になります。この値は二度と表示されません。

6.  **リダイレクト URI を設定する:**

      * アプリの左側のナビゲーション ウィンドウで、**認証** に移動します。
      * **プラットフォームを追加** をクリックし、**Web** を選択します。
      * **リダイレクト URI** セクションで、次の URI を追加します: `https://your-domain.com/outlook-oauth2callback` ( `https://your-domain.com` を実際のアプリケーションの URL に置き換えてください)。
      * **構成** をクリックします。

7.  **API アクセス許可を追加する:**

      * アプリの左側のナビゲーション ウィンドウで、**API のアクセス許可** に移動します。
      * **アクセス許可を追加** をクリックし、**Microsoft Graph** を選択します。
      * **委任されたアクセス許可** を選択します。
      * 次のアクセス許可を追加します。
          * `Mail.Read`
          * `Mail.ReadBasic.All`
          * `Mail.Send`
          * `Mail.ReadWrite`
      * **アクセス許可の追加** をクリックします。

-----

## Google Cloud プロジェクトと Pub/Sub トピックの作成

1.  **新しい Google Cloud プロジェクトを作成する:**

      * [Google Cloud コンソール](https://console.cloud.google.com/) にアクセスします。
      * 上部のナビゲーション バーにあるプロジェクト セレクターのドロップダウンをクリックし、**新しいプロジェクト** をクリックします。
      * プロジェクトに名前を付け、**作成** をクリックします。

2.  **Gmail API を有効にする:**

      * ナビゲーション メニューで、**API とサービス** \> **ライブラリ** に移動します。
      * 「Gmail API」を検索し、プロジェクトで有効にします。

3.  **OAuth 2.0 認証情報を作成する:**

      * ナビゲーション メニューで、**API とサービス** \> **認証情報** に移動します。
      * **認証情報を作成** をクリックし、**OAuth クライアント ID** を選択します。
      * アプリケーションの種類として **ウェブ アプリケーション** を選択します。
      * 名前を付けます。
      * **承認済みのリダイレクト URI** の下に、`https://your-domain.com/oauth2callback` を追加します ( `https://your-domain.com` を実際のアプリケーションの URL に置き換えてください)。
      * **作成** をクリックします。
      * **クライアント ID** と **クライアント シークレット** が記載されたダイアログ ボックスが表示されます。これらの値をコピーします。これらが `GOOGLE_CLIENT_ID` と `GOOGLE_CLIENT_SECRET` になります。

4.  **Pub/Sub トピックを作成する:**

      * ナビゲーション メニューで、**Pub/Sub** \> **トピック** に移動します。
      * **トピックを作成** をクリックします。
      * トピックに ID を付けます (例: `gmail-notifications`)。これが `GMAIL_PUB_SUB_TOPIC` になります。
      * **作成** をクリックします。

5.  **Gmail サービス アカウントに Pub/Sub の公開権限を付与する:**

      * Pub/Sub トピックに移動し、**権限** タブをクリックします。
      * **プリンシパルを追加** をクリックします。
      * **新しいプリンシパル** フィールドに `gmail-api-push@system.gserviceaccount.com` を追加します。
      * **ロールを選択** のドロップダウンで、**Pub/Sub パブリッシャー** を選択します。
      * **保存** をクリックします。

6.  **Pub/Sub サブスクリプションを作成する:**

      * ナビゲーション メニューで、**Pub/Sub** \> **サブスクリプション** に移動します。
      * **サブスクリプションを作成** をクリックします。
      * サブスクリプションに ID を付けます (例: `gmail-subscription`)。
      * 前のステップで作成したトピックを選択します。
      * **配信タイプ** で **Push** を選択します。
      * **エンドポイント URL** フィールドに、`https://your-domain.com/gmail-webhook` と入力します ( `https://your-domain.com` を実際のアプリケーションの URL に置き換えてください)。
      * **作成** をクリックします。