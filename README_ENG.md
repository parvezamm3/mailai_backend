# MailAI Backend

This is the backend for the MailAI application.

## Installation

1.  **Clone the repository:**

    ```bash
    git clone git@github.com:parvezamm3/mailai_backend.git
    cd mailai-backend
    ```

2.  **Create a virtual environment:**

    ```bash
    python -m venv .venv
    ```

3.  **Activate the virtual environment:**

    - **Windows:**
      ```bash
      .venv\Scripts\activate
      ```
    - **macOS/Linux:**
      ```bash
      source .venv/bin/activate
      ```

4.  **Install the dependencies:**
    ```bash
    pip install -r "requirements.txt"
    ```

## Running the Application

To run the application, execute the following command:

```bash
Flask run
```

or

```bash
python app.py
```

## Redis Setup (for Windows with WSL)

1.  **Install Redis on WSL:**
    Open your WSL terminal and run the following commands:

    ```bash
    sudo apt-get update
    sudo apt-get install redis-server
    ```

2.  **Start the Redis server:**

    ```bash
    sudo service redis-server start
    ```

3.  **Verify that Redis is running:**
    ```bash
    redis-cli ping
    ```
    You should see `PONG` as the response.

## MongoDB Compass Setup

1.  **Download and install MongoDB Compass:**
    Go to the [MongoDB Compass download page](https.www.mongodb.com/try/download/compass) and download the installer for your operating system.

2.  **Install MongoDB Compass:**
    Run the downloaded installer and follow the on-screen instructions.

3.  **Connect to your MongoDB database:**
    Once installed, open MongoDB Compass and connect to your MongoDB database using your connection string.

## Ngrok Setup

1.  **Sign up for ngrok:**
    Go to the [ngrok dashboard](https://dashboard.ngrok.com/signup) and create a free account.

2.  **Install ngrok:**
    Follow the instructions on the [ngrok download page](https://ngrok.com/download) for your operating system.

3.  **Configure your authtoken:**
    After installing ngrok, connect your account using the authtoken from your dashboard:

    ```bash
    ngrok config add-authtoken <YOUR_AUTHTOKEN>
    ```

4.  **Get a free static domain:**
    On the ngrok dashboard, navigate to **Cloud Edge** > **Domains** and create a new domain. This will give you a static URL that you can reuse.

5.  **Start a tunnel with your static domain:**
    To expose a local port to the internet using your static domain, use the following command (e.g., for port 5000):

    ```bash
    ngrok http --domain=<YOUR_STATIC_DOMAIN> 5000
    ```

    Replace `<YOUR_STATIC_DOMAIN>` with the domain you created in the previous step.

    If you don't have a static domain use this command

    ```bash
    ngrok http 5000
    ```

    Here, the url will refresh every time you start the ngrok.

## Environment Variables

This project uses a `.env` file to manage environment variables. Create a file named `.env` in the root directory of the project and add the following variables:

### Google Cloud & Gmail API

- `GOOGLE_CLIENT_ID`: Your Google Cloud project's client ID.
- `GOOGLE_CLIENT_SECRET`: Your Google Cloud project's client secret.
- `GOOGLE_REDIRECT_URI`: The redirect URI for your Google Cloud project.
- `GMAIL_PUB_SUB_TOPIC`: The name of your Google Cloud Pub/Sub topic for Gmail push notifications.
- `GCP_PROJECT_ID`: Your Google Cloud project ID.

### Microsoft Graph & Entra App

- `MS_GRAPH_CLIENT_ID`: The client ID of your Microsoft Entra application.
- `MS_GRAPH_CLIENT_SECRET`: The client secret of your Microsoft Entra application.
- `MS_GRAPH_REDIRECT_URI`: The redirect URI for your Microsoft Entra application.
- `MS_GRAPH_TENANT_ID`: The tenant ID of your Microsoft Entra application.
- `MS_GRAPH_WEBHOOK_NOTIFICATION_URL`: The URL where Microsoft Graph will send webhook notifications.

## Creating a Microsoft Entra App

1.  **Sign in to the Microsoft Entra admin center:**
    Go to [https://entra.microsoft.com/](https://entra.microsoft.com/) and sign in with your administrator account.

2.  **Navigate to App registrations:**
    In the left-hand navigation pane, go to **Identity** > **Applications** > **App registrations**.

3.  **Create a new app registration:**

    - Click on **New registration**.
    - Give your application a name (e.g., `MailAI-Backend`).
    - For **Supported account types**, select **Accounts in any organizational directory (Any Microsoft Entra ID tenant - Multitenant) and personal Microsoft accounts (e.g. Skype, Xbox)**.
    - Click **Register**.

4.  **Get the Client ID and Tenant ID:**

    - After the app is created, you will be taken to the app's **Overview** page.
    - Copy the **Application (client) ID** and the **Directory (tenant) ID**. These are your `MS_GRAPH_CLIENT_ID` and `MS_GRAPH_TENANT_ID` respectively.

5.  **Create a Client Secret:**

    - In the left-hand navigation pane for your app, go to **Certificates & secrets**.
    - Click on **New client secret**.
    - Give the secret a description and choose an expiration time.
    - Click **Add**.
    - **Important:** Copy the **Value** of the client secret immediately. This is your `MS_GRAPH_CLIENT_SECRET`. You will not be able to see this value again.

6.  **Configure the Redirect URI:**

    - In the left-hand navigation pane for your app, go to **Authentication**.
    - Click on **Add a platform** and select **Web**.
    - In the **Redirect URIs** section, add the following URI: `https://your-domain.com/outlook-oauth2callback` (replace `https://your-domain.com` with your actual application's URL).
    - Click **Configure**.

7.  **Add API Permissions:**
    - In the left-hand navigation pane for your app, go to **API permissions**.
    - Click on **Add a permission** and select **Microsoft Graph**.
    - Select **Delegated permissions**.
    - Add the following permissions:
      - `Mail.Read`
      - `Mail.ReadBasic.All`
      - `Mail.Send`
      - `Mail.ReadWrite`
    - Click **Add permissions**.

## Creating a Google Cloud Project and Pub/Sub Topic

1.  **Create a new Google Cloud project:**

    - Go to the [Google Cloud Console](https://console.cloud.google.com/).
    - Click on the project selector dropdown in the top navigation bar and click **New Project**.
    - Give your project a name and click **Create**.

2.  **Enable the Gmail API:**

    - In the navigation menu, go to **APIs & Services** > **Library**.
    - Search for "Gmail API" and enable it for your project.

3.  **Create OAuth 2.0 Credentials:**

    - In the navigation menu, go to **APIs & Services** > **Credentials**.
    - Click on **Create Credentials** and select **OAuth client ID**.
    - Select **Web application** as the application type.
    - Give it a name.
    - Under **Authorized redirect URIs**, add `https://your-domain.com/oauth2callback` (replace `https://your-domain.com` with your actual application's URL).
    - Click **Create**.
    - A dialog box will appear with your **client ID** and **client secret**. Copy these values. These are your `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.

4.  **Create a Pub/Sub Topic:**

    - In the navigation menu, go to **Pub/Sub** > **Topics**.
    - Click on **Create Topic**.
    - Give your topic an ID (e.g., `gmail-notifications`). This is your `GMAIL_PUB_SUB_TOPIC`.
    - Click **Create**.

5.  **Grant Pub/Sub publish permissions to the Gmail service account:**

    - Go to your Pub/Sub topic and click on the **Permissions** tab.
    - Click on **Add Principal**.
    - In the **New principals** field, add `gmail-api-push@system.gserviceaccount.com`.
    - In the **Select a role** dropdown, select **Pub/Sub Publisher**.
    - Click **Save**.

6.  **Create a Pub/Sub Subscription:**
    - In the navigation menu, go to **Pub/Sub** > **Subscriptions**.
    - Click on **Create Subscription**.
    - Give your subscription an ID (e.g., `gmail-subscription`).
    - Select the topic you created in the previous step.
    - For **Delivery Type**, select **Push**.
    - In the **Endpoint URL** field, enter `https://your-domain.com/gmail-webhook` (replace `https://your-domain.com` with your actual application's URL).
    - Click **Create**.
