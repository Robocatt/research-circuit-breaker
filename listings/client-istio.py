# client.py 
import os
import time
import requests
import logging
import json
from flask import Flask, request, jsonify

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider, ReadableSpan
from opentelemetry.sdk.trace.export import BatchSpanProcessor
# from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor

# ─── custom console exporter (exactly the same file you use on the server) ──
from otlp_json_console_exporter import OTLPJsonConsoleExporter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("flask-proxy")

# ---------------------------------------------------------------------------
# Tracing setup: print every finished process-root trace as one JSON line
# ---------------------------------------------------------------------------
resource = Resource(attributes={"service.name": "flask-proxy"})
provider = TracerProvider(resource=resource)
trace.set_tracer_provider(provider)

# ①  send every span to the pod log (Fluent Bit reads it later)
json_exporter = OTLPJsonConsoleExporter()

# ↓ change here: use BatchSpanProcessor so that each "root" span flushes instantly
provider.add_span_processor(BatchSpanProcessor(json_exporter))
# provider.add_span_processor(SimpleSpanProcessor(json_exporter))


app = Flask(__name__)
FlaskInstrumentor().instrument_app(app)
RequestsInstrumentor().instrument()
tracer = trace.get_tracer(__name__)


@app.route("/echo", methods=["POST"])
def proxy_echo():
    with tracer.start_as_current_span("echo_proxy_client") as span:
        start_time = time.time()
        try:
            with tracer.start_as_current_span("parse_request_client"):
                real_server_url = f'http://{os.getenv("SERVER_SERVICE")}:5001/echo'
                json_data = request.get_json()

            with tracer.start_as_current_span("redirect_to_server_client"):
                response = requests.post(real_server_url, json=json_data, timeout=3)
                response.raise_for_status()

            with tracer.start_as_current_span("construct_response_client"):
                response_json = response.json()
                span.set_attribute("response_time", time.time() - start_time)
                return response_json

        except Exception as exc:
            span.record_exception(exc)
            span.add_event("Error during request forwarding")
            logger.error("Error forwarding request: %s", exc)
            return jsonify({"error": str(exc)}), 503
            
if __name__ == "__main__":
    # Make Flask listen on 0.0.0.0:8001 so that K3s' Service (port 8001) can reach it.
    app.run(host="0.0.0.0", port=8001)
