# server.py
import os
import time
import sys
import json
import threading
import collections

from flask import Flask, request, jsonify
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    SpanExporter,
    SpanExportResult,
    BatchSpanProcessor,
)
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
# Custom exporter
from otlp_json_console_exporter import OTLPJsonConsoleExporter


# 2) Tracing setup

resource = Resource(attributes={"service.name": "flask-server"})
provider = TracerProvider(resource=resource)
trace.set_tracer_provider(provider)

json_exporter = OTLPJsonConsoleExporter()
# Use BatchSpanProcessor so that as soon as "echo_request_server" ends,
# exporter’s _flush() writes the JSON of the entire trace to stdout.
provider.add_span_processor(BatchSpanProcessor(json_exporter))


# Previous gRPC to Jaeger attempt
# from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
# jaeger_uri = os.getenv("JAEGER_ENDPOINT", "jaeger-otlp-service:4317")
# otlp_exporter = OTLPSpanExporter(endpoint=jaeger_uri, insecure=True)
# provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

# 3) Flask + instrumentation
app = Flask(__name__)
FlaskInstrumentor().instrument_app(app)
RequestsInstrumentor().instrument()
tracer = trace.get_tracer(__name__)

@app.route("/echo", methods=["POST"])
def echo():
    with tracer.start_as_current_span("echo_request_server") as span:
        start_time = time.time()
        try:
            with tracer.start_as_current_span("parse_request_server"):
                data = request.get_json()
                message = data.get("message", "") if data else ""

            with tracer.start_as_current_span("process_message_server"):
                # Example application logs, written to stdout:
                print(f"[Server] Received message: {message}")

            with tracer.start_as_current_span("construct_response_server"):
                response_data = {
                    "message": f"Echo: {message}",
                    "timestamp": int(time.time())
                }

            duration = time.time() - start_time
            span.set_attribute("total.duration", duration)
            return jsonify(response_data)

        except Exception as e:
            print(f"[Server] ERROR: {e}", file=sys.stderr)
            span.record_exception(e)
            return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    print("[Server] Starting server on 0.0.0.0:5001 (stdout for logs + spans)")
    app.run(host="0.0.0.0", port=5001, debug=False)
