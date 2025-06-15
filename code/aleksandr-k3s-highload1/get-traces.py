"""
Export Jaeger traces into one Pickle file per load-test run.

Features
--------
* honours  start / end  **only if**  lookback=custom  (Jaeger quirk #1)
* offset-based pagination to fetch >N traces (Jaeger quirk #2)
* split either by:
    – explicit wall-clock windows  OR
    – span tag, e.g.  test_id = "10vus"
"""
from __future__ import annotations
import os, sys, time, requests, pandas as pd
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Iterator

# ─── editable runtime parameters ────────────────────────────────────────────────
JAEGER_URL   = os.getenv("JAEGER_URL", "http://localhost:31686")
SERVICE      = os.getenv("JAEGER_SERVICE", "flask-server")
TAG_KEY      = "test_id"          # tag we inject from k6; empty → don’t split
PAGE_LIMIT   = 3_000              # safe with Badger & Elastic; tune if needed
VERIFY_TLS   = True
TIMEOUT      = 30                 # seconds

# fixed windows (ISO-8601 UTC). leave empty to fetch last 24 h then split by tag
TEST_WINDOWS: dict[str, tuple[str, str]] = {
    # "10vus": ("2025-06-10T08:00:00Z", "2025-06-10T08:05:00Z"),
    # "50vus": ("2025-06-10T08:10:00Z", "2025-06-10T08:15:00Z"),
}

# ─── helpers ────────────────────────────────────────────────────────────────────
session = requests.Session()
session.verify = VERIFY_TLS

def _us(ts: datetime) -> int:                     # datetime → μs epoch
    return int(ts.replace(tzinfo=timezone.utc).timestamp() * 1_000_000)

def query_page(start_us: int, end_us: int, offset: int) -> list[dict[str, Any]]:
    params = {
        "service":  SERVICE,
        "lookback": "custom",      # without this Jaeger ignores start/end  :contentReference[oaicite:0]{index=0}
        "start":    start_us,
        "end":      end_us,
        "limit":    PAGE_LIMIT,
        "offset":   offset,        # offset = “skip first N traces”  :contentReference[oaicite:1]{index=1}
    }
    r = session.get(f"{JAEGER_URL}/api/traces", params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json().get("data", [])

def fetch_window(start: datetime, end: datetime) -> Iterator[dict[str, Any]]:
    """Yield every trace within [start,end) handling pagination."""
    offset = 0
    while True:
        batch = query_page(_us(start), _us(end), offset)
        if not batch:
            break
        yield from batch
        if len(batch) < PAGE_LIMIT:
            break          # last page
        offset += PAGE_LIMIT

def traces_to_df(traces: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for t in traces:
        tid = t["traceID"]
        for sp in t["spans"]:
            parent = (sp.get("references") or [{}])[0].get("spanID", "")
            tags   = {kv["key"]: kv["value"] for kv in sp.get("tags", [])}
            rows.append({
                "trace_id":    tid,
                "span_id":     sp["spanID"],
                "parent_id":   parent,
                "operation":   sp["operationName"],
                "start_us":    sp["startTime"],
                "duration_us": sp["duration"],
                **tags,
            })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["duration_ms"] = df["duration_us"] / 1_000
    return df

# ─── main ───────────────────────────────────────────────────────────────────────
def download_named_run(name: str, ts0: datetime, ts1: datetime) -> pd.DataFrame:
    print(f"→  {name}: {ts0:%F %T} → {ts1:%F %T}")
    all_traces = list(fetch_window(ts0, ts1))
    return traces_to_df(all_traces)

def main() -> None:
    runs: dict[str, pd.DataFrame] = {}

    if TEST_WINDOWS:
        for run, (iso0, iso1) in TEST_WINDOWS.items():
            runs[run] = download_named_run(run,
                                           datetime.fromisoformat(iso0.replace("Z","+00:00")),
                                           datetime.fromisoformat(iso1.replace("Z","+00:00")))
    else:
        now   = datetime.now(timezone.utc)
        start = now - timedelta(hours=24)
        big   = download_named_run("all", start, now)

        if TAG_KEY and TAG_KEY in big.columns:
            for tag_val, sub in big.groupby(TAG_KEY):
                runs[str(tag_val)] = sub.copy()
        else:
            runs["all"] = big

    for name, df in runs.items():
        if df.empty:
            print(f"⚠️  {name}: no data")
            continue
        fn = f"{name}.pkl"
        df.to_pickle(fn, protocol=4)
        print(f"✔  {fn}  ({len(df)} spans)")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
