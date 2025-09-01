import os
from flask import Flask, redirect, url_for, session, jsonify, g
from flask_cors import CORS
from celery import Celery
from msal import ConfidentialClientApplication # Import MSAL app for Outlook utils
from config import Config
from database import init_db

init_db()

# Global celery_app instance (will be set by create_app)
celery_app = Celery(__name__,include=Config.CELERY_INCLUDE)


def make_celery(app):
    """
    Configures the global Celery instance with Flask application settings.
    """
    celery_app = Celery(__name__,
                    backend=app.config['CELERY_RESULT_BACKEND'],
        broker=app.config['CELERY_BROKER_URL'],
        include=app.config['CELERY_INCLUDE'])
    class ContextTask(celery_app.Task): # Use the global celery_app
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return super(ContextTask, self).__call__(*args, **kwargs)
    
    celery_app.Task = ContextTask
    return celery_app # Return the globally configured instance


def create_app():
    """
    Flask application factory function.
    Initializes the Flask app, loads configuration, sets up database,
    registers blueprints, and configures Celery.
    """
    app = Flask(__name__)

    # Import Config here to ensure it's loaded within the app factory context
    from config import Config
    app.config.from_object(Config)
    CORS(app)
    # Initialize Celery
    make_celery(app)


    # Import utility modules and blueprints AFTER Celery is initialized
    # This ensures that when these modules are loaded, celery_app is already available.
    import utils.gmail_utils as gmail_utils
    import utils.outlook_utils as outlook_utils
    import utils.gemini_utils as gemini_utils
    import workers.tasks as tasks
    import utils.llm_agent as llm_agent

    # Pass initialized celery_app and msal_app to utility modules
    gmail_utils.celery_app = celery_app
    outlook_utils.celery_app = celery_app
    # Initialize msal_app here and pass it
    outlook_utils.msal_app = ConfidentialClientApplication(
        client_id=app.config['MS_GRAPH_CLIENT_ID'],
        client_credential=app.config['MS_GRAPH_CLIENT_SECRET'],
        authority=app.config['MS_GRAPH_AUTHORITY']
    )
    # tasks.celery_app = celery_app

    # Import blueprints
    from blueprints.gmail_auth_bp import gmail_auth_bp as google_auth_bp
    from blueprints.gmail_webhook_bp import webhook_bp as gmail_webhook_bp
    from blueprints.add_on_bp import add_on_bp
    from blueprints.outlook_auth_bp import outlook_auth_bp
    from blueprints.outlook_webhook_bp import outlook_webhook_bp

    # Register blueprints
    app.register_blueprint(google_auth_bp)
    app.register_blueprint(gmail_webhook_bp)
    app.register_blueprint(add_on_bp)
    app.register_blueprint(outlook_auth_bp)
    app.register_blueprint(outlook_webhook_bp)
    
    @app.route('/')
    def index():
        return """
        <h1>Unified AI Assistant Backend</h1>
        <p>This backend supports both Gmail and Outlook add-ons.</p>
        <h2>Gmail Add-on / API Integration:</h2>
        <p><a href="/authorize">Authorize with Google (Gmail API)</a></p>
        <h2>Outlook Add-in / Graph API Integration:</h2>
        <p><a href="/outlook-authorize">Authorize with Microsoft (Outlook Graph API)</a></p>
        <p>Once authorized, you can manage webhooks for real-time notifications.</p>
        <h3>To run Celery worker:</h3>
        <p><code>celery -A app.celery_app worker --loglevel=info</code></p>
        <h3>To run Redis (if not already running):</h3>
        <p><code>redis-server</code></p>
        """
    
    return app

# This block is for running the Flask app directly (e.g., `python app.py`)
if __name__ == '__main__':
    app = create_app()
    app.run(debug=True, port=5000)

