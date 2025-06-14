import os
import time
import requests
from flask import Flask, request, jsonify
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from otlp_json_console_exporter import OTLPJsonConsoleExporter


from circuit_breaker import CircuitBreaker

# ---------------------------------------------------- OpenTelemetry plumbing
resource = Resource.create(attributes={"service.name": "circuit-breaker-proxy"})
trace.set_tracer_provider(TracerProvider(resource=resource))
trace.get_tracer_provider().add_span_processor(
    BatchSpanProcessor(OTLPJsonConsoleExporter())
)

# ---------------------------------------------------- Flask app
app = Flask(__name__)
FlaskInstrumentor().instrument_app(app)
RequestsInstrumentor().instrument()

breaker = CircuitBreaker(failure_threshold=3, open_timeout=30)
tracer = trace.get_tracer(__name__)

# ---------------------------------------------------- Routes
@app.route("/echo", methods=["POST"])
def proxy_echo():
    with tracer.start_as_current_span("echo_proxy_client") as span:
        start = time.time()

        def forward():
            server_service = os.getenv("SERVER_SERVICE", "flask-server")
            real_url = f"http://{server_service}:5001/echo"
            resp = requests.post(real_url, json=request.get_json(), timeout=3)
            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")
            return resp

        try:
            resp = breaker.call(forward)
            span.set_attribute("proxy_echo.status", "ok")
            return resp.json()
        except Exception as exc:
            span.record_exception(exc)
            span.set_attribute("proxy_echo.status", "error")
            return jsonify({"error": str(exc)}), 503


@app.route("/test-failure", methods=["POST"])
def test_failure_endpoint():
    with tracer.start_as_current_span("test_failure"):

        def always_fail():
            raise RuntimeError("simulated backend failure")

        breaker.call(always_fail)  # always raises & counts as failure
        # We’ll never reach here
        return jsonify({"msg": "unexpected"})


@app.errorhandler(404)
def catch_all(_):
    return jsonify({"error": "endpoint not found"}), 404


if __name__ == "__main__":
    print("[Proxy] starting on :8001")
    app.run(host="0.0.0.0", port=8001, debug=True)
