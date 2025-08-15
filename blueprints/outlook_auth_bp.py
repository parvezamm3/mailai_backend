from flask import Blueprint, request, redirect, url_for, session, jsonify
from config import Config
from utils.outlook_utils import msal_app, save_outlook_credentials, subscribe_to_outlook_mail_webhook, authorize_unlicensed_mail # Import the msal_app instance

outlook_auth_bp = Blueprint('outlook_auth_bp', __name__)

@outlook_auth_bp.route('/outlook-authorize')
def outlook_authorize():
    """Initiates the Microsoft Graph OAuth 2.0 authorization flow."""
    # The 'state' parameter protects against CSRF attacks
    session['outlook_oauth_state'] = 'some_random_state_string' # Use a real random string in production
    
    auth_url = msal_app.get_authorization_request_url(
        scopes=Config.MS_GRAPH_SCOPES,
        redirect_uri=Config.MS_GRAPH_REDIRECT_URI,
        state=session['outlook_oauth_state']
    )
    print(f"Outlook authorization initiated. Redirecting to: {auth_url}")
    return redirect(auth_url)

@outlook_auth_bp.route('/outlook-oauth2callback')
def outlook_oauth2callback():
    """Handles the callback from Microsoft's OAuth 2.0 server."""
    session_state = session.pop('outlook_oauth_state', None)
    request_state = request.args.get('state')

    if not session_state or session_state != request_state:
        print("Error: Invalid state parameter. Mismatch or missing state during Outlook OAuth.")
        return jsonify({"error": "Invalid state parameter."}), 400

    if "error" in request.args:
        print(f"Outlook OAuth error: {request.args.get('error_description')}")
        return jsonify({"error": request.args.get('error_description')}), 400

    if request.args.get('code'):
        try:
            # Use MSAL to acquire token using authorization code
            result = msal_app.acquire_token_by_authorization_code(
                request.args['code'],
                scopes=Config.MS_GRAPH_SCOPES,
                redirect_uri=Config.MS_GRAPH_REDIRECT_URI
            )
            
            if "access_token" in result:
                access_token = result['access_token']
                expires_in = result.get('expires_in')
                user_id = result.get('id_token_claims', {}).get('preferred_username') or \
                          result.get('id_token_claims', {}).get('email') # Get user's email/UPN
                # print(f'Access Token {access_token} expires_in {expires_in}')
                
                if not user_id:
                    print("Could not determine user_id from Outlook OAuth response.")
                    return jsonify({"error": "Could not determine user identity."}), 500

                save_outlook_credentials(user_id, result, expires_in)

                # Optionally subscribe to webhooks immediately after successful auth
                if subscribe_to_outlook_mail_webhook(user_id):
                    return jsonify({"message": f"Outlook Authorization successful for {user_id}! Webhook subscription created."})
                else:
                    return jsonify({"message": f"Outlook Authorization successful for {user_id}, but webhook subscription failed."}), 500

            else:
                print(f"Error acquiring Outlook token: {result.get('error_description')}")
                return jsonify({"error": f"Failed to acquire Outlook token: {result.get('error_description')}"}), 500

        except Exception as e:
            print(f"Outlook OAuth callback error: {e}")
            return jsonify({"error": f"Failed to complete Outlook OAuth flow: {e}"}), 500
    
    return jsonify({"error": "No authorization code found."}), 400


@outlook_auth_bp.route('/outlook-authorize-unlicensed', methods=['POST'])
def outlook_authorize_unlicensed():
    """
    Endpoint to authorize an unlicensed mailbox.
    Requires an email address and assumes admin consent is pre-configured.
    """
    data = request.get_json()
    # print(data)
    email_address = data.get('email')
    # print(email_address)
    if not email_address:
        return jsonify({"error": "Email address is required."}), 400

    success, message = authorize_unlicensed_mail(email_address)
    if success:
        return jsonify({"message": message}), 200
    else:
        return jsonify({"error": message}), 500