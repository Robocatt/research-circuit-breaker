import os
import sys
import json
import time
import logging
from typing import Sequence
from flask import Flask, request, jsonify

# OpenTelemetry imports
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    SpanExporter,
    SpanExportResult,
    BatchSpanProcessor,
    SimpleSpanProcessor
)
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor

###############################################################################
# 1) Custom JSON Exporter that replicates the "file exporter" style from OTEL.
###############################################################################
class OTLPJsonConsoleExporter(SpanExporter):
    """
    For each batch of spans, prints out a JSON object like:
    
    {
      "resourceSpans": [
        {
          "resource": {
            "attributes": [...]
          },
          "scopeSpans": [
            {
              "scope": {
                "name": "...",
                "version": "..."
              },
              "spans": [
                {
                  "traceId": "...",
                  "spanId": "...",
                  "parentSpanId": "...",
                  "name": "...",
                  "kind": ...,
                  "startTimeUnixNano": "...",
                  "endTimeUnixNano": "...",
                  "status": { "code": 0 or 2, ... },
                  "attributes": [...]
                },
                ...
              ]
            }
          ]
        }
      ]
    }

    We print exactly one JSON object per call to `export()`. 
    By default, the OTel SDK calls `export()` periodically with a batch of spans.
    """
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        resource_spans_map = {}

        # Group spans by (resource, instrumentation scope)
        # so we can build the "resourceSpans" -> "scopeSpans" -> "spans" structure
        for readable_span in spans:
            resource = readable_span.resource
            scope = readable_span.instrumentation_scope

            # Convert resource key into a dict key
            # A simpler approach: use the resource's attributes as a tuple of (k,v).
            resource_key = tuple(resource.attributes.items())

            if resource_key not in resource_spans_map:
                resource_spans_map[resource_key] = {}

            scope_key = (scope.name, scope.version)
            if scope_key not in resource_spans_map[resource_key]:
                resource_spans_map[resource_key][scope_key] = []

            resource_spans_map[resource_key][scope_key].append(readable_span)

        # Build the final JSON structure
        final_output = {"resourceSpans": []}

        for resource_key, scopes_dict in resource_spans_map.items():
            # Resource attributes
            resource_attributes = []
            for k, v in resource_key:
                resource_attributes.append({"key": k, "value": {"stringValue": str(v)}})

            scope_spans_list = []
            for (scope_name, scope_version), span_list in scopes_dict.items():
                spans_json = []
                for s in span_list:
                    span_data = {
                        "traceId": f"{s.context.trace_id:032x}",
                        "spanId": f"{s.context.span_id:016x}",

                        "parentSpanId": f"{s.parent.span_id:016x}" if s.parent else "",

                        "name": s.name,
                        "kind": s.kind.value,
                        "startTimeUnixNano": str(s.start_time),
                        "endTimeUnixNano": str(s.end_time),
                        "status": {
                            "code": s.status.status_code.value,
                            "description": s.status.description or ""
                        },
                        # Optional: include span attributes if you want
                        "attributes": [
                            {"key": k, "value": {"stringValue": str(v)}}
                            for k, v in s.attributes.items()
                        ],
                        # Could include events, links, etc. if needed
                    }
                    # Flags (traceFlags) if you want:
                    # span_data["flags"] = s.context.trace_flags

                    spans_json.append(span_data)

                scope_spans_list.append({
                    "scope": {
                        "name": scope_name,
                        "version": scope_version or ""
                    },
                    "spans": spans_json
                })

            final_output["resourceSpans"].append({
                "resource": {
                    "attributes": resource_attributes
                },
                "scopeSpans": scope_spans_list
            })

        # Print one JSON doc to stdout
        print(json.dumps(final_output, ensure_ascii=False))

        # with open("/var/log/otel_spans.json", "a") as f:
        #     # f.write(json.dumps(final_output, ensure_ascii=False) + "\n") ????
        #     f.write(json.dumps(final_output, ensure_ascii=False) )

        return SpanExportResult.SUCCESS

    def shutdown(self):
        return

###############################################################################
# 2) Setup OTel tracer provider with our custom JSON console exporter
###############################################################################
resource = Resource(attributes={"service.name": "flask-server"})
provider = TracerProvider(resource=resource)
trace.set_tracer_provider(provider)

json_exporter = OTLPJsonConsoleExporter()
# provider.add_span_processor(BatchSpanProcessor(json_exporter)) !!!! NOT BATCH
provider.add_span_processor(SimpleSpanProcessor(json_exporter))


# try raw jaeger
otlp_endpoint = os.environ.get("OTLP_COLLECTOR_ENDPOINT", "jaeger-otlp-service:4317")

otlp_exporter = OTLPSpanExporter(
    endpoint=otlp_endpoint,
    insecure=True  # set to True if no TLS; otherwise configure credentials
)
provider.add_span_processor(
    SimpleSpanProcessor(otlp_exporter)
)

###############################################################################
# 3) Setup Python logging for "normal" logs to a file (NOT stdout)
###############################################################################
normal_log_filename = os.getenv("NORMAL_LOG_FILE", "/var/log/flask_server.log")

# Create a separate logger for "normal logs"
log_formatter = logging.Formatter(
    fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
file_handler = logging.FileHandler(normal_log_filename)
file_handler.setFormatter(log_formatter)
file_handler.setLevel(logging.INFO)

normal_logger = logging.getLogger("normal-logger")
normal_logger.setLevel(logging.INFO)
normal_logger.addHandler(file_handler)

###############################################################################
# 4) Flask app with OTel instrumentation
###############################################################################
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
                # Normal logs go to file
                normal_logger.info(f"[Server] Received message: {message}")

            with tracer.start_as_current_span("construct_response_server"):
                response_data = {
                    "message": f"Echo: {message}",
                    "timestamp": int(time.time())
                }

            duration = time.time() - start_time
            span.set_attribute("total.duration", duration)

            return jsonify(response_data)

        except Exception as e:
            normal_logger.exception("[Server] Error processing request")
            span.record_exception(e)
            return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    normal_logger.info("[Server] Starting. All normal logs go to /var/log/flask_server.log")
    app.run(host="0.0.0.0", port=5001, debug=False)
