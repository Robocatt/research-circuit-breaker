import time
import os 
from flask import Flask, request, jsonify

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
# from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from otlp_json_console_exporter import OTLPJsonConsoleExporter

# otlp_endpoint = os.getenv("OTLP_COLLECTOR_ENDPOINT", "collector-endpoint:4317")
resource = Resource(attributes={"service.name": "flask-server"})
provider = TracerProvider(resource=resource)
trace.set_tracer_provider(provider)

# otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
# provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
json_exporter = OTLPJsonConsoleExporter()
provider.add_span_processor(BatchSpanProcessor(json_exporter))

app = Flask(__name__)
FlaskInstrumentor().instrument_app(app)
RequestsInstrumentor().instrument()

# tracer instance to manually create spans
tracer = trace.get_tracer(__name__)

@app.route('/echo', methods=['POST'])
def echo():
    # Root span for the entire echo request.
    with tracer.start_as_current_span("echo_request_server") as span:
        start_time = time.time()

        # Span for parsing the incoming JSON payload.
        with tracer.start_as_current_span("parse_request_server") as parse_span:
            data = request.get_json()
            message = data.get("message", "") if data else ""
            parse_span.set_attribute("message.length", len(message))
        
        # Span for processing the message.
        with tracer.start_as_current_span("process_message_server") as process_span:
            print(f"[Server] Received message from client: {message}")
            # time.sleep(0.1)
            process_span.set_attribute("message.logged", True)

        # if random.random() < 0.25:
        #     print("[Server] Simulated failure!")
        #     return "Internal Server Error", 500
    
        # Span for constructing the response.
        with tracer.start_as_current_span("construct_response_server") as construct_span:
            response_data = {
                "message": f"Echo: {message}",
                "timestamp": int(time.time()),
            }
            construct_span.set_attribute("response.size", len(str(response_data)))
            response = jsonify(response_data)
        
        total_duration = time.time() - start_time
        span.set_attribute("total.duration", total_duration)
        return response

if __name__ == "__main__":
    print("[Server] Starting with OTel tracing on port 5001 ...")
    app.run(host="0.0.0.0", port=5001, debug=True)
