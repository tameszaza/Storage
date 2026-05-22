import logging
from datetime import datetime
from flask import current_app, request


def setup_logging(app):
    logging.basicConfig(
        filename=app.config["SERVER_LOG_FILE"],
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(threadName)s : %(message)s",
    )


def register_request_logging(app):
    @app.before_request
    def log_request():
        try:
            with open(current_app.config["DATA_TRANSFER_LOG"], "a", encoding="utf-8") as file:
                data_size = request.content_length or 0
                file.write(f"Incoming Request - Path: {request.path}, Method: {request.method}, Data Size: {data_size} bytes\n")
        except OSError:
            logging.exception("Could not write request transfer log")

    @app.after_request
    def log_response(response):
        try:
            if not response.direct_passthrough:
                data_size = len(response.get_data())
                with open(current_app.config["DATA_TRANSFER_LOG"], "a", encoding="utf-8") as file:
                    file.write(f"{datetime.now()}: {request.remote_addr} - {data_size} bytes transferred.\n")
        except Exception:
            logging.exception("Could not write response transfer log")
        return response
