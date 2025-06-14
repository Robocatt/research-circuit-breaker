import threading
import collections
import sys
import json

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
def _enc(v):
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}

    return {"stringValue": str(v)}

def _span_json(s: ReadableSpan):
    parent_id = ""
    if s.parent and s.parent.is_valid and s.parent.span_id != 0:
        parent_id = f"{s.parent.span_id:016x}"
    return {
        "traceId": f"{s.context.trace_id:032x}",
        "spanId":  f"{s.context.span_id:016x}",
        "parentSpanId": parent_id,
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

class OTLPJsonConsoleExporter(SpanExporter):
    """
    Emits one OTLP/JSON line per process-root trace.
    Works with SimpleSpanProcessor or BatchSpanProcessor.
    """
    def __init__(self):
        self._lock   = threading.Lock()
        self._buffer = collections.defaultdict(list)  # trace_id -> list[ReadableSpan]

    def export(self, spans):
        with self._lock:
            for span in spans:
                tid = span.context.trace_id
                self._buffer[tid].append(span)

                parent = span.parent
                if parent is None or not parent.is_valid or parent.is_remote:
                    self._flush(tid)

        return SpanExportResult.SUCCESS

    def shutdown(self):
        with self._lock:
            for tid in list(self._buffer.keys()):
                self._flush(tid)

    def _flush(self, tid: int):
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
                "resource": {
                    "attributes": [
                        {"key": k, "value": _enc(v)} for (k, v) in r_key
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": name, "version": version or ""},
                        "spans": [_span_json(sp) for sp in span_list],
                    }
                    for (name, version), span_list in scopes.items()
                ],
            })

        # Write exactly one JSON object (no extra newlines, no prefix)
        sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
        sys.stdout.flush()

