# blueprints/auth_bp.py
from flask import Blueprint, request, redirect, session, jsonify
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from urllib.parse import urlparse, urlunparse

from config import Config # Import CONFIG
from utils.gmail_utils import save_google_credentials, setup_gmail_watch # Import helper functions

gmail_auth_bp = Blueprint('gmail_auth_bp', __name__)

# Create a Flow object for OAuth 2.0 using client_config from CONFIG
# oauth_flow = Flow.from_client_config(
#     client_config={
#         "web": {
#             "client_id": CONFIG['GOOGLE_CLIENT_ID'],
#             "client_secret": CONFIG['GOOGLE_CLIENT_SECRET'],
#             "auth_uri": "https://accounts.google.com/o/oauth2/auth",
#             "token_uri": "https://oauth2.googleapis.com/token",
#             "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
#             "redirect_uris": [CONFIG['GOOGLE_REDIRECT_URI']]
#         }
#     },
#     scopes=CONFIG['SCOPES'],
#     redirect_uri=CONFIG['GOOGLE_REDIRECT_URI']
# )
oauth_flow = Flow.from_client_config(
    client_config={
        "web": {
            "client_id": Config.GOOGLE_CLIENT_ID,
            "client_secret": Config.GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": [Config.GOOGLE_REDIRECT_URI]
        }
    },
    scopes=Config.GOOGLE_SCOPES,
    redirect_uri=Config.GOOGLE_REDIRECT_URI
)

@gmail_auth_bp.route('/authorize')
def authorize():
    """Initiates the Google OAuth 2.0 authorization flow."""
    authorization_url, state = oauth_flow.authorization_url(
        access_type='offline', # Request a refresh token
        include_granted_scopes='true'
    )
    session['oauth_state'] = state
    print(f"Authorization initiated. Stored state in session: {state}")
    return redirect(authorization_url)

@gmail_auth_bp.route('/oauth2callback')
def oauth2callback():
    """Handles the callback from Google's OAuth 2.0 server."""
    session_state = session.pop('oauth_state', None)
    request_state = request.args.get('state')

    print(f"OAuth callback received. Request URL: {request.url}")
    if not session_state or session_state != request_state:
        print("Error: Invalid state parameter. Mismatch or missing state.")
        return jsonify({"error": "Invalid state parameter."}), 400

    try:
        # Parse the incoming request.url to change its scheme to HTTPS
        parsed_url = urlparse(request.url)
        https_callback_url = urlunparse(parsed_url._replace(scheme='https'))
        print(f"Transformed callback URL for fetch_token: {https_callback_url}")
        oauth_flow.fetch_token(authorization_response=https_callback_url)
        credentials = oauth_flow.credentials

        gmail_service = build('gmail', 'v1', credentials=credentials)
        profile = gmail_service.users().getProfile(userId='me').execute()
        user_email = profile['emailAddress']

        # Get the user's email address from the credentials (uses the 'userinfo.email' scope)
        # user_email = credentials.id_token['email'] if 'email' in credentials.id_token else 'unknown_user'
        
        save_google_credentials(user_email, credentials) # Save credentials using helper
        
        # Set up Gmail API watch for push notifications using helper
        if setup_gmail_watch(credentials, user_email):
            return jsonify({"message": f"Authorization successful for {user_email}! Gmail watch set up. You can now send emails to this account to trigger webhooks."})
        else:
            return jsonify({"error": f"Authorization successful for {user_email}, but failed to set up Gmail watch. Check server logs."}), 500

    except Exception as e:
        print(f"OAuth callback error: {e}")
        return jsonify({"error": f"Failed to complete OAuth flow: {e}"}), 500
