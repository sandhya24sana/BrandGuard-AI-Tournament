import os
import sys
import logging

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException
from flask_cors import CORS
from flask_limiter.errors import RateLimitExceeded

# Ensure backend directory is in Python path
backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from config import Config, configure_rate_limit_storage
from database import init_db
from extensions import limiter

from routes.brands import brands_bp
from routes.content import content_bp
from routes.analysis import analysis_bp
from routes.reviews import reviews_bp
from routes.history import history_bp


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

logger = logging.getLogger(__name__)


def create_app(test_config=None):

    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    configure_rate_limit_storage(app.config)
    CORS(app, resources={r"/api/*": {"origins": Config.CORS_ORIGINS}})
    limiter.init_app(app)

    # Initialize database WITHOUT inserting demo data
    with app.app_context():
        init_db()

    # Register API routes
    app.register_blueprint(brands_bp)
    app.register_blueprint(content_bp)
    app.register_blueprint(analysis_bp)
    app.register_blueprint(reviews_bp)
    app.register_blueprint(history_bp)

    # Health check
    @app.route("/api/health", methods=["GET"])
    def health_check():

        return jsonify({
            "status": "healthy",
            "app": "BrandGuard AI – Intelligent Brand Governance Platform",
            "version": "1.0.0"
        })

    # API fallback
    @app.route("/api/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def api_not_found(path):

        return jsonify({
            "success": False,
            "error": f"API endpoint not found: /api/{path}"
        }), 404

    # Frontend build
    frontend_dist = os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),
            "..",
            "frontend",
            "dist"
        )
    )

    if os.path.exists(frontend_dist):

        @app.route("/", defaults={"path": ""})
        @app.route("/<path:path>")
        def serve_frontend(path):

            if path == "api" or path.startswith("api/"):
                return jsonify({
                    "success": False,
                    "error": "API endpoint not found"
                }), 404

            requested_file = os.path.join(frontend_dist, path)

            if path and os.path.isfile(requested_file):
                return send_from_directory(frontend_dist, path)

            return send_from_directory(frontend_dist, "index.html")

    # Bad request handler
    @app.errorhandler(400)
    def bad_request(e):

        return jsonify({
            "success": False,
            "error": getattr(e, "description", "Bad request")
        }), 400

    # Not found handler
    @app.errorhandler(404)
    def not_found(e):

        if request.path.startswith("/api/"):
            return jsonify({
                "success": False,
                "error": f"API route not found: {request.method} {request.path}"
            }), 404

        if os.path.exists(frontend_dist):
            return send_from_directory(frontend_dist, "index.html")

        return jsonify({
            "success": False,
            "error": "Not found"
        }), 404

    # Method not allowed
    @app.errorhandler(405)
    def method_not_allowed(e):

        return jsonify({
            "success": False,
            "error": f"Method {request.method} not allowed"
        }), 405

    # Internal server error
    @app.errorhandler(RateLimitExceeded)
    def rate_limit_exceeded(_error):

        return jsonify({
            "success": False,
            "error": "Too many requests. Please try again later.",
        }), 429

    # Internal server error
    @app.errorhandler(500)
    def internal_error(e):

        logger.error(
            "Internal server error (exception_category=%s)",
            type(e).__name__,
        )

        return jsonify({
            "success": False,
            "error": "Internal server error"
        }), 500

    # General exception handler
    @app.errorhandler(Exception)
    def handle_unhandled_exception(e):

        if isinstance(e, HTTPException):
            return jsonify({
                "success": False,
                "error": e.description
            }), e.code

        logger.error(
            "Unhandled exception (exception_category=%s)",
            type(e).__name__,
        )

        return jsonify({
            "success": False,
            "error": "An unexpected server error occurred."
        }), 500

    return app


app = create_app()


if __name__ == "__main__":

    port = Config.PORT

    print(
        f"BrandGuard AI backend running on http://localhost:{port}"
    )

    app.run(
        host="127.0.0.1",
        port=port,
        debug=Config.DEBUG
    )