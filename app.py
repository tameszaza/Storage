from flask import Flask
from lib.config import Config
from lib.extensions import bcrypt
from lib.request_logging import register_request_logging, setup_logging
from routes import register_all_routes


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    setup_logging(app)
    bcrypt.init_app(app)
    register_request_logging(app)
    register_all_routes(app)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
