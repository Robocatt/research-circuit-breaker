import time
import os
import logging
from flask import Flask, request, jsonify

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

otlp_endpoint = os.getenv("OTLP_COLLECTOR_ENDPOINT", "collector-service:4317")


resource = Resource(attributes={"service.name": "flask-server"})
provider = TracerProvider(resource=resource)
trace.set_tracer_provider(provider)

otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

app = Flask(__name__)
FlaskInstrumentor().instrument_app(app)
RequestsInstrumentor().instrument()

tracer = trace.get_tracer(__name__)

cnt = 10

@app.route('/echo', methods=['POST'])
def echo():
    global cnt 
    # global span for echo message
    with tracer.start_as_current_span("echo_request_server") as span:
        start_time = time.time()
        try:
            # Span for parsing the incoming JSON payload.
            with tracer.start_as_current_span("parse_request_server") as parse_span:
                data = request.get_json()
                message = data.get("message", "") if data else ""
                parse_span.set_attribute("message_length", len(message))
      
            # Span for processing the message.
            with tracer.start_as_current_span("process_message_server") as process_span:
                logger.info(f"[Server] Received message: {message}")
                process_span.set_attribute("message.logged", True)

            # Span for constructing and returning the response.
            with tracer.start_as_current_span("construct_response_server") as response_span:
                response_data = {
                    "message": f"Echo: {message}",
                    "timestamp": int(time.time()),
                }
                response_span.set_attribute("response.size", len(str(response_data)))
                response = jsonify(response_data)
                
            total_duration = time.time() - start_time
            span.set_attribute("total.duration", total_duration)
            return response
            # if cnt > 0:
            #     cnt-=1
            #     return response
            # else:
            #     return jsonify({"error": "Simulated server error, code"}), 500

            
        except Exception as e:
            logger.exception("[Server] Error processing request")
            span.record_exception(e)
            return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    logger.info("[Server] Starting with OpenTelemetry tracing on port 5001 ...")
    app.run(host="0.0.0.0", port=5001, debug=False)
