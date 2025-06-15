# otlp_json_console_exporter.py
import json, sys, threading, collections
from typing import Any, Dict, Sequence
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

def _enc(v: Any) -> Dict[str, Any]:
    if isinstance(v, bool):  return {"boolValue": v}
    if isinstance(v, int):   return {"intValue": str(v)}
    if isinstance(v, float): return {"doubleValue": v}
    return {"stringValue": str(v)}

class OTLPJsonConsoleExporter(SpanExporter):
    """
    Emits one OTLP/JSON line per *process-root* trace.
    Works best with BatchSpanProcessor.
    """
    def __init__(self):
        self._lock   = threading.Lock()
        self._buffer = collections.defaultdict(list)  # trace_id → list[ReadableSpan]

    # ---------- public API -----------------------------------------------
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        with self._lock:
            for span in spans:
                tid = span.context.trace_id
                self._buffer[tid].append(span)

                # flush when SERVER span ends or we truly have no parent
                parent = span.parent
                if parent is None or not parent.is_valid or parent.is_remote:
                    self._flush(tid)

        return SpanExportResult.SUCCESS

    def shutdown(self):
        with self._lock:
            for tid in list(self._buffer):
                self._flush(tid)

    # ---------- helper ----------------------------------------------------
    def _flush(self, tid: int) -> None:
        batch = self._buffer.pop(tid, [])
        if not batch:
            return

        buckets = collections.defaultdict(lambda: collections.defaultdict(list))
        for s in batch:
            r_key = tuple(sorted(s.resource.attributes.items()))
            s_key = (s.instrumentation_scope.name, s.instrumentation_scope.version)
            buckets[r_key][s_key].append(s)

        payload = {"resourceSpans": []}
        for r_key, scopes in buckets.items():
            payload["resourceSpans"].append({
                "resource": {"attributes": [{"key": k, "value": _enc(v)} for k, v in r_key]},
                "scopeSpans": [
                    {
                        "scope": {"name": n, "version": v or ""},
                        "spans": [_span_json(s) for s in slist],
                    }
                    for (n, v), slist in scopes.items()
                ],
            })

        sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
        sys.stdout.flush()

def _span_json(s: ReadableSpan) -> Dict[str, Any]:
    par = ""
    if s.parent and s.parent.is_valid and s.parent.span_id != 0:
        par = f"{s.parent.span_id:016x}"
    return {
        "traceId": f"{s.context.trace_id:032x}",
        "spanId":  f"{s.context.span_id:016x}",
        "parentSpanId": par,
        "name": s.name,
        "kind": int(s.kind.value),
        "startTimeUnixNano": str(s.start_time),
        "endTimeUnixNano":   str(s.end_time),
        "status": {
            "code": int(s.status.status_code.value),
            "message": s.status.description or "",
        },
        "attributes": [{"key": k, "value": _enc(v)} for k, v in s.attributes.items()],
    }
