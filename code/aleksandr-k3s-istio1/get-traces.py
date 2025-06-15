import requests
import pandas as pd
from datetime import datetime, timedelta

# JAEGER_URL = "http://jaeger-ui-service.default.svc.cluster.local:16686"
JAEGER_URL = "http://localhost:31687"
SERVICE   = "flask-server"

def fetch_traces_window(start_us: int, end_us: int, limit: int = 500):
    params = {
        "service": SERVICE,
        "start":   start_us,
        "end":     end_us,
        "limit":   limit,
    }
    resp = requests.get(f"{JAEGER_URL}/api/traces", params=params)
    resp.raise_for_status()
    return resp.json().get("data", [])

def download_all_traces(earliest: datetime, latest: datetime, window_minutes=5):
    rows = []
    cur = earliest
    while cur < latest:
        nxt = cur + timedelta(minutes=window_minutes)
        data = fetch_traces_window(
            int(cur.timestamp()*1e6),
            int(nxt.timestamp()*1e6),
        )
        for trace in data:
            tid = trace["traceID"]
            # optional: read resource tags from trace['processes']
            for span in trace["spans"]:
                # safely extract parent span ID (if any)
                refs = span.get("references") or []
                parent_id = refs[0].get("spanID", "") if refs else ""

                rows.append({
                    "trace_id":  tid,
                    "span_id":   span["spanID"],
                    "parent_id": parent_id,
                    "operation": span["operationName"],
                    "start_us":  span["startTime"],
                    "duration_us": span["duration"],
                    # your deployment.variant tags, etc.
                    **{
                        tag["key"]: tag["value"]
                        for tag in span.get("tags", [])
                        if tag["key"].startswith("deployment.")
                    },
                })

        cur = nxt
    return pd.DataFrame(rows)

if __name__ == "__main__":
    # e.g. last 24h
    end   = datetime.utcnow()
    start = end - timedelta(hours=24)
    df = download_all_traces(start, end)
    # convert micros → ms
    df["duration_ms"] = df["duration_us"] / 1e3

    df.to_pickle("traces_istio_200vu_5min.pkl")
    df.info()

