"""
Reusable circuit-breaker implementation with OpenTelemetry tracing.

Install locally (pip install -e .) or add the folder to PYTHONPATH and
import with:

    from circuit_breaker import CircuitBreaker
"""

import time
from opentelemetry import trace

__all__ = ["CircuitBreakerState", "CircuitBreaker"]

class CircuitBreakerState:
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """
    A simple, thread-unsafe circuit breaker.

    Parameters
    ----------
    failure_threshold : int
        Number of consecutive failures before opening the circuit.
    open_timeout : int | float
        Seconds to wait before moving from OPEN -> HALF_OPEN.
    """

    def __init__(self, *, failure_threshold: int = 3, open_timeout: int = 10):
        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.failure_threshold = failure_threshold
        self.open_timeout = open_timeout
        self.next_attempt_time: float | None = None
        self.tracer = trace.get_tracer(__name__)

    # ------------------------------------------------------------------ helpers
    def _handle_success(self, span):
        if self.state == CircuitBreakerState.HALF_OPEN:
            span.set_attribute("cb.state_transition", "HALF_OPEN → CLOSED")
        self.state, self.failure_count = CircuitBreakerState.CLOSED, 0
        span.set_attribute("cb.new_state", self.state)

    def _handle_failure(self, span, exc: Exception):
        self.failure_count += 1
        span.set_attribute("cb.failure_count", self.failure_count)

        if self.state == CircuitBreakerState.HALF_OPEN:
            # probe failed -> reopen
            self.state = CircuitBreakerState.OPEN
            self.next_attempt_time = time.time() + self.open_timeout
            span.set_attribute("cb.state_transition", "HALF_OPEN → OPEN")
        elif (
            self.state == CircuitBreakerState.CLOSED
            and self.failure_count >= self.failure_threshold
        ):
            self.state = CircuitBreakerState.OPEN
            self.next_attempt_time = time.time() + self.open_timeout
            span.set_attribute("cb.state_transition", "CLOSED → OPEN")

    # ------------------------------------------------------------------ API
    def call(self, func, *args, **kwargs):
        """
        Invoke *func* under circuit-breaker protection.

        Any exception raised by *func* is wrapped and re-raised
        so callers can handle it uniformly.
        """
        with self.tracer.start_as_current_span("circuit_breaker_call") as span:
            span.set_attribute("cb.current_state", self.state)

            # Guard: circuit is OPEN?
            if self.state == CircuitBreakerState.OPEN:
                if time.time() < (self.next_attempt_time or 0):
                    span.set_attribute("cb.request_status", "blocked (OPEN)")
                    raise RuntimeError("Circuit breaker is OPEN")
                # timeout expired ➜ probe in HALF-OPEN
                self.state = CircuitBreakerState.HALF_OPEN
                self.failure_count = 0
                span.set_attribute("cb.state_transition", "OPEN -> HALF_OPEN")

            # Try the protected call
            try:
                result = func(*args, **kwargs)
            except Exception as exc:
                self._handle_failure(span, exc)
                span.record_exception(exc)
                raise RuntimeError(
                    f"operation error ({self.state}): {exc}"
                ) from exc
            else:
                self._handle_success(span)
                return result
