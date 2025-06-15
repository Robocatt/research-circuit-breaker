import time
import requests
import os 
from flask import Flask, request, jsonify

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
# from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from otlp_json_console_exporter import OTLPJsonConsoleExporter

class CircuitBreakerState:
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

class CircuitBreaker:
    def __init__(self, failure_threshold=3, open_timeout=10):
        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.failure_threshold = failure_threshold
        self.open_timeout = open_timeout
        self.next_attempt_time = None
        self.tracer = trace.get_tracer(__name__)

    def call(self, func): 
        with self.tracer.start_as_current_span("circuit_breaker_call") as span:
            span.set_attribute("cb.current_state", self.state)
            
            if self.state == CircuitBreakerState.OPEN:
                # still open
                if time.time() < self.next_attempt_time:
                    span.set_attribute("cb.request_status", "Blocked: OPEN state")
                    raise Exception("circuit breaker: open state blocking requests")
                # try Half open
                else:
                    self.state = CircuitBreakerState.HALF_OPEN
                    self.failure_count = 0
                    span.set_attribute("cb.state_transition", "State change: OPEN → HALF_OPEN")
                    print("[CB] State: OPEN → HALF_OPEN")
            
            # try actual call to the server
            try:
                result = func()
                self.handle_success(span)
                return result
            except Exception as e:
                self.handle_failure(span, e)
                span.record_exception(e)
                raise Exception(f"operation error: {str(e)} (CB state: {self.state})")

    def handle_success(self, span):
        # success in half open
        if self.state == CircuitBreakerState.HALF_OPEN:
            span.set_attribute("cb.state_transition", "State change: HALF_OPEN → CLOSED (probe success)")
            print("[CB] State: HALF_OPEN → CLOSED (probe success)")

        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        span.set_attribute("cb.new_state", self.state)

    def handle_failure(self, span, error):
        self.failure_count += 1
        print(f"[CB] Failure count: {self.failure_count}/{self.failure_threshold}")
        span.set_attribute("cb.failure_message", f"Failure count: {self.failure_count}/{self.failure_threshold}")
        span.set_attribute("cb.failure_count", self.failure_count)

        # error in half open
        if self.state == CircuitBreakerState.HALF_OPEN:
            self.state = CircuitBreakerState.OPEN
            self.next_attempt_time = time.time() + self.open_timeout
            span.set_attribute("cb.state_transition", f"State change: HALF_OPEN → OPEN (retry after {self.open_timeout}s)")
            print(f"[CB] State: HALF_OPEN → OPEN (retry after {self.open_timeout}s)")
        elif self.failure_count >= self.failure_threshold and self.state == CircuitBreakerState.CLOSED:
            self.state = CircuitBreakerState.OPEN
            self.next_attempt_time = time.time() + self.open_timeout
            span.set_attribute("cb.state_transition", f"State change: CLOSED → OPEN (retry after {self.open_timeout}s)")
            print(f"[CB] State: CLOSED → OPEN (retry after {self.open_timeout}s)")


# otlp_endpoint = os.getenv("OTLP_COLLECTOR_ENDPOINT", "collector-endpoint:4317")
resource = Resource(attributes={"service.name": "circuit-breaker-proxy"})
provider = TracerProvider(resource=resource)
trace.set_tracer_provider(provider)

# otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
# provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

json_exporter = OTLPJsonConsoleExporter()
provider.add_span_processor(BatchSpanProcessor(json_exporter))

# Flask
app = Flask(__name__)
FlaskInstrumentor().instrument_app(app)
RequestsInstrumentor().instrument()

breaker = CircuitBreaker(failure_threshold=3, open_timeout=30)
tracer = trace.get_tracer(__name__)

@app.route('/echo', methods=['POST'])
def proxy_echo():
    # span for echo 
    with tracer.start_as_current_span("echo_proxy_client") as span:
        start_time = time.time()
        def forward_request():
            # span for forward request
            with tracer.start_as_current_span("parse_request_client") as parse_span:
                server_service = os.getenv("SERVER_SERVICE", "flask-server")
                real_server_url = f"http://{server_service}:5001/echo"
                json_data = request.get_json()
                
            with tracer.start_as_current_span("redirect_to_server_client") as redirect_span:
                resp = requests.post(real_server_url, json=json_data, timeout=3)
                redirect_span.set_attribute("response.status_code", resp.status_code)
                
                # Consider HTTP error status codes as failures for circuit breaker
                if resp.status_code >= 400:
                    print(f"[Proxy] HTTP error {resp.status_code} - treating as failure for circuit breaker")
                    raise Exception(f"HTTP {resp.status_code}: {resp.text}")
                
                # Only raise for status if we want to catch other HTTP errors too
                # resp.raise_for_status()  # This is now redundant since we check above
            return resp

        try:
            # CB logic is in here
            data = breaker.call(forward_request)
            span.set_attribute("proxy_echo.status", "Successful response from Circuit Breaker")

            with tracer.start_as_current_span("construct_response_client") as response_span:
                data = data.json()
                response_span.set_attribute("response_time", time.time() - start_time)
            return data
        except Exception as e:
            span.record_exception(e)
            span.set_attribute("proxy_echo.status", "Error or circuit breaker is OPEN")
            print("[Proxy] Error or circuit open:", str(e))
            return jsonify({"error": str(e)}), 503

# Add a test endpoint that simulates backend failures to trigger circuit breaker
@app.route('/test-failure', methods=['POST'])
def test_failure_endpoint():
    """Test endpoint that always fails to trigger circuit breaker"""
    with tracer.start_as_current_span("test_failure_proxy") as span:
        def simulate_backend_failure():
            with tracer.start_as_current_span("simulated_backend_failure") as failure_span:
                print("[Proxy] Simulating backend failure for circuit breaker testing")
                failure_span.set_attribute("simulated.failure", True)
                # Simulate different types of backend failures
                import random
                failure_type = random.choice(['timeout', 'connection_error', 'http_500'])
                
                if failure_type == 'timeout':
                    raise Exception("Connection timeout to backend server")
                elif failure_type == 'connection_error':
                    raise Exception("Connection refused by backend server")
                else:
                    raise Exception("HTTP 500: Internal server error from backend")
        
        try:
            # This will always fail and trigger circuit breaker
            result = breaker.call(simulate_backend_failure)
            return jsonify({"message": "This should never happen"}), 200
        except Exception as e:
            span.record_exception(e)
            span.set_attribute("test_failure.status", "Expected failure for testing")
            print("[Proxy] Test failure endpoint triggered circuit breaker:", str(e))
            return jsonify({"error": str(e)}), 503

# Add a catch-all route to handle any other endpoints and return 404
@app.route('/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE'])
def handle_nonexistent_routes(path):
    """Handle requests to non-existent endpoints"""
    print(f"[Proxy] Request to non-existent endpoint: /{path}")
    return jsonify({"error": f"Endpoint /{path} not found"}), 404

if __name__ == "__main__":
    print("[Proxy] Starting circuit-breaker proxy with OTel tracing on port 8001 ...")
    app.run(host="0.0.0.0", port=8001, debug=True)