#!/usr/bin/env python3
"""
trace_loader.py
---------------

Parse Istio *and* non-Istio OTLP logs into tidy pandas DataFrames.
Now supports both VU-based and payload-size-based log files.

Expected file names inside LOG_DIR:

VU-based files:
    logs_istio_client_10vus.txt   logs_client_10vus.txt
    logs_istio_server_10vus.txt   logs_server_10vus.txt
    logs_istio_client_50vus.txt   logs_client_50vus.txt
    ...

Payload-based files:
    logs_client_parse_1kb.txt     logs_istio_client_parse_1kb.txt
    logs_server_parse_1kb.txt     logs_istio_server_parse_1kb.txt
    logs_client_parse_5kb.txt     logs_istio_client_parse_5kb.txt
    ...

Key features
------------
* No command-line arguments – just set LOG_DIR below.
* Adds metadata columns: implementation, side, and either vus OR payload_kb.
* Keeps traces that only appear in client files (circuit-breaker cases).
* Returns dict   {"istio": {"spans": df, "traces": df}, "plain": {...}}

Usage in a notebook
-------------------
    from trace_loader import load_all_traces, LOG_DIR

    data = load_all_traces(LOG_DIR)
    istio_spans  = data["istio"]["spans"]
    plain_traces = data["plain"]["traces"]

    # Example: latency violin plot by payload size
    import seaborn as sns, matplotlib.pyplot as plt
    sns.violinplot(
        data=pd.concat([istio_spans, plain_spans]),
        x="implementation", y="latency_s", hue="side"
    )
    plt.title("Span latencies by payload size"); plt.show()
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Callable, Union
from copy import deepcopy

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns

# --------------------------------------------------------------------------- #
# 1. CONFIGURATION –– change this directory to wherever the *.txt live
# --------------------------------------------------------------------------- #
LOG_DIR = Path("/home/hpc/aleksandr/plots/raw_data_load_new")          # <-- edit me


# ----------------------------------------------------------------------
# 2. Filename helpers - updated to handle both VU and payload patterns
# ----------------------------------------------------------------------
VU_FILE_RE = re.compile(
    r"^logs(?:_istio)?_(client|server)_(\d+)vus\.txt$", re.IGNORECASE
)

PAYLOAD_FILE_RE = re.compile(
    r"^logs(?:_istio)?_(client|server)_parse_(\d+)kb\.txt$", re.IGNORECASE
)


def _parse_filename(fname: str) -> Tuple[str, str, Optional[int], Optional[int]]:
    """(implementation, side, vus, payload_kb) from filename.
    Either vus OR payload_kb will be None, never both."""
    
    # Try VU pattern first
    m = VU_FILE_RE.match(fname)
    if m:
        side, vus = m.groups()
        implementation = "istio" if "_istio_" in fname else "plain"
        return implementation, side, int(vus), None
    
    # Try payload pattern
    m = PAYLOAD_FILE_RE.match(fname)
    if m:
        side, payload_kb = m.groups()
        implementation = "istio" if "_istio_" in fname else "plain"
        return implementation, side, None, int(payload_kb)
    
    raise ValueError(f"Unknown filename pattern: {fname}")


# ----------------------------------------------------------------------
# 3. Span extraction with robust key checking (unchanged)
# ----------------------------------------------------------------------
REQUIRED_KEYS = {
    "traceId",
    "spanId",
    "startTimeUnixNano",
    "endTimeUnixNano",
    "name",
}


def _extract_spans(
    otlp: dict,
    *,
    implementation: str,
    side: str,
    vus: Optional[int],
    payload_kb: Optional[int],
    source_file: str,
    stats: dict,
) -> List[dict]:
    """Return list[dict] of valid spans; update stats for skipped ones."""
    out: List[dict] = []

    for res in otlp.get("resourceSpans", []):
        for scope in res.get("scopeSpans", []):
            for span in scope.get("spans", []):
                missing = REQUIRED_KEYS.difference(span)
                if missing:
                    stats["malformed_spans"] += 1
                    for key in missing:
                        stats.setdefault(f"missing_{key}", 0)
                        stats[f"missing_{key}"] += 1
                    continue

                start_ns = int(span["startTimeUnixNano"])
                end_ns = int(span["endTimeUnixNano"])
                
                span_dict = {
                    "trace_id": span["traceId"],
                    "span_id": span["spanId"],
                    "parent_span_id": span.get("parentSpanId", ""),
                    "name": span["name"],
                    "kind": span.get("kind"),
                    "start_ns": start_ns,
                    "end_ns": end_ns,
                    "latency_s": (end_ns - start_ns) / 1_000_000_000,
                    "status_code": int(span.get("status", {}).get("code", 0)),
                    "status_message": span.get("status", {}).get("message", ""),
                    # metadata
                    "implementation": implementation,
                    "side": side,
                    "source_file": source_file,
                }
                
                # Add either vus or payload_kb
                if vus is not None:
                    span_dict["vus"] = vus
                    span_dict["payload_kb"] = None
                else:
                    span_dict["vus"] = None
                    span_dict["payload_kb"] = payload_kb
                    
                out.append(span_dict)
    return out


def _parse_line(
    raw: str,
    *,
    implementation: str,
    side: str,
    vus: Optional[int],
    payload_kb: Optional[int],
    source_file: str,
    stats: dict,
) -> List[dict]:
    """Parse one log line → list of span dicts (or empty list)."""
    try:
        outer = json.loads(raw)
    except json.JSONDecodeError:
        return []  # gunicorn noise etc.

    payload = None
    if "body" in outer:        # k8s `"body":"{\"resourceSpans\": ...}"`
        try:
            payload = json.loads(outer["body"])
        except json.JSONDecodeError:
            return []
    elif "log" in outer:       # docker `"log":"{\"resourceSpans\": ...}"`
        try:
            payload = json.loads(outer["log"])
        except json.JSONDecodeError:
            return []
    elif "resourceSpans" in outer:   # already-decoded OTLP
        payload = outer

    if payload is None:
        return []

    return _extract_spans(
        payload,
        implementation=implementation,
        side=side,
        vus=vus,
        payload_kb=payload_kb,
        source_file=source_file,
        stats=stats,
    )


# ----------------------------------------------------------------------
# 4. Public loader - updated for both patterns
# ----------------------------------------------------------------------
def load_all_traces(log_dir: Path | str = LOG_DIR) -> Dict[str, Dict[str, pd.DataFrame]]:
    """Parse every recognised log file in *log_dir*."""
    log_dir = Path(log_dir)
    if not log_dir.is_dir():
        raise NotADirectoryError(log_dir)

    spans: List[dict] = []
    stats_global = {"malformed_spans": 0}

    for fp in sorted(log_dir.glob("logs*_*.txt")):
        try:
            impl, side, vus, payload_kb = _parse_filename(fp.name)
        except ValueError:
            continue

        with fp.open(encoding="utf-8") as f:
            for line in f:
                spans.extend(
                    _parse_line(
                        line,
                        implementation=impl,
                        side=side,
                        vus=vus,
                        payload_kb=payload_kb,
                        source_file=fp.name,
                        stats=stats_global,
                    )
                )

    if not spans:
        raise RuntimeError("No valid OTLP spans found!")

    spans_df = pd.DataFrame(spans)

    # Determine grouping columns based on what data we have
    has_vus = 'vus' in spans_df.columns and spans_df['vus'].notna().any()
    has_payload = 'payload_kb' in spans_df.columns and spans_df['payload_kb'].notna().any()
    
    if has_vus and has_payload:
        raise RuntimeError("Mixed VU and payload data found - please separate into different directories")
    
    group_cols = ["trace_id", "implementation"]
    if has_vus:
        group_cols.append("vus")
    if has_payload:
        group_cols.append("payload_kb")

    traces_df = (
        spans_df.groupby(group_cols, sort=False)
        .agg(
            trace_start_ns=("start_ns", "min"),
            trace_end_ns=("end_ns", "max"),
            total_latency_s=("latency_s", "sum"),
            span_count=("span_id", "size"),
            client_span_count=("side", lambda s: (s == "client").sum()),
            server_span_count=("side", lambda s: (s == "server").sum()),
        )
        .reset_index()
        .assign(
            trace_duration_s=lambda df: (df.trace_end_ns - df.trace_start_ns)
            / 1_000_000_000,
            server_present=lambda df: df.server_span_count > 0,
        )
        .drop(columns=["trace_start_ns", "trace_end_ns"])
    )

    out: Dict[str, Dict[str, pd.DataFrame]] = {}
    for impl in ("plain", "istio"):
        out[impl] = {
            "spans": spans_df.loc[spans_df.implementation == impl].reset_index(
                drop=True
            ),
            "traces": traces_df.loc[traces_df.implementation == impl].reset_index(
                drop=True
            ),
        }

    out["stats"] = stats_global
    return out


# ---------------------------------------------------------------------- #
# 5. PLOTTING HELPERS – updated for payload size analysis
# ---------------------------------------------------------------------- #
def harmonise_span_sets(data: dict) -> dict:
    """
    Ensure Istio and Plain have identical span 'name' sets.
    Extra spans (e.g. 'circuit_breaker_call' in Plain) are dropped.

    Returns a shallow copy of *data* with filtered DataFrames.
    """
    istio_names  = set(data["istio"]["spans"]["name"].unique())
    plain_names  = set(data["plain"]["spans"]["name"].unique())
    common_names = istio_names & plain_names

    filt = lambda df: df[df["name"].isin(common_names)].copy()

    new_data = {
        "istio":  {"spans": filt(data["istio"]["spans"]),
                   "traces": data["istio"]["traces"]},
        "plain":  {"spans": filt(data["plain"]["spans"]),
                   "traces": data["plain"]["traces"]},
    }
    return new_data


def plot_span_latency(data: dict,
                      vus: Optional[int] = None,
                      payload_kb: Optional[int] = None,
                      ax: Optional[plt.Axes] = None) -> plt.Axes:
    """
    Bar-with-CI comparison of mean span latency (milliseconds) for each span
    name, Istio vs. Plain. Filter on either `vus` or `payload_kb` level.
    """
    data = harmonise_span_sets(data)

    spans = pd.concat([
        data["plain"]["spans"].assign(impl="Plain"),
        data["istio"]["spans"].assign(impl="Istio"),
    ])

    if vus is not None:
        spans = spans.query("vus == @vus")
    elif payload_kb is not None:
        spans = spans.query("payload_kb == @payload_kb")

    sns.set_theme(style="whitegrid")
    ax = ax or plt.gca()
    sns.barplot(
        data=spans,
        x="name", y="latency_ms",
        hue="impl",
        errorbar=("ci", 95),
        capsize=.1,
        ax=ax,
    )
    
    filter_desc = 'all loads'
    if vus is not None:
        filter_desc = f'{vus} VUs'
    elif payload_kb is not None:
        filter_desc = f'{payload_kb}KB payload'
    
    ax.set_title(f"Mean span latency – {filter_desc}")
    ax.set_xlabel("Span name")
    ax.set_ylabel("Latency (ms)")

    # rotate & right-align tick labels
    ax.tick_params(axis="x", labelrotation=45)
    for lbl in ax.get_xticklabels():
        lbl.set_horizontalalignment("right")

    return ax


def plot_trace_duration(data, vus=None, payload_kb=None, ax=None):
    
    traces = pd.concat([
        data["plain"]["traces"].assign(impl="Plain"),
        data["istio"]["traces"].assign(impl="Istio"),
    ])
    
    if vus is not None:
        traces = traces.query("vus==@vus")
    elif payload_kb is not None:
        traces = traces.query("payload_kb==@payload_kb")

    traces = traces.copy()
    traces["trace_duration_ms"] = pd.to_numeric(
        traces["trace_duration_ms"], errors="coerce"
    )
    traces = traces.dropna(subset=["trace_duration_ms"])

    sns.set_theme(style="whitegrid")
    ax = ax or plt.gca()

    # violin
    sns.violinplot(
        data=traces, x="impl", y="trace_duration_ms",
        palette="pastel", ax=ax
    )

    filter_desc = 'all'
    if vus is not None:
        filter_desc = f'{vus} VUs'
    elif payload_kb is not None:
        filter_desc = f'{payload_kb}KB payload'

    ax.set_title(f"Trace duration distribution – {filter_desc}")
    ax.set_xlabel("")
    ax.set_ylabel("Duration (ms)")
    return ax


def plot_latency_vs_payload(
    data: dict,
    estimator: Union[str, Callable] = "mean",
    ci: Union[int, str] = 95,
    ax: Optional[plt.Axes] = None,
):
    """Line plot of trace duration vs payload size - only traces with both client and server spans."""
    # Use trace data instead of span data, filter for traces with server_present=True
    traces = pd.concat([
        data["plain"]["traces"].assign(impl="Plain"),
        data["istio"]["traces"].assign(impl="Istio"),
    ])
    
    # Only include traces that have both client and server spans
    traces = traces[traces["server_present"] == True].copy()
    
    # Filter out rows where payload_kb is None (VU-based data)
    if 'payload_kb' in traces.columns:
        traces = traces[traces["payload_kb"].notna()].copy()
    else:
        raise ValueError("No payload_kb column found in traces data")

    estf = {"mean": np.mean, "median": np.median}.get(estimator, estimator)
    if not callable(estf):
        raise ValueError("estimator must be 'mean', 'median' or callable")

    # Create single plot showing trace duration vs payload size
    sns.set_theme(style="whitegrid", context="talk")
    ax = ax or plt.gca()
    
    # Aggregate data manually for single plot
    agg_data = traces.groupby(['impl', 'payload_kb']).agg({
        'trace_duration_ms': [estf, 'std', 'count']
    }).reset_index()
    
    # Flatten column names
    agg_data.columns = ['impl', 'payload_kb', 'duration_mean', 'duration_std', 'count']
    
    # Calculate confidence intervals
    from scipy import stats
    alpha = 0.05 if ci == 95 else (100 - ci) / 100
    agg_data['ci'] = agg_data.apply(lambda row: 
        stats.t.interval(1-alpha, row['count']-1, 
                       loc=row['duration_mean'], 
                       scale=row['duration_std']/np.sqrt(row['count']))[1] - row['duration_mean']
        if row['count'] > 1 else 0, axis=1)
    
    # Plot
    for impl in agg_data['impl'].unique():
        impl_data = agg_data[agg_data['impl'] == impl]
        ax.errorbar(impl_data['payload_kb'], impl_data['duration_mean'], 
                   yerr=impl_data['ci'], label=impl, marker='o', capsize=5, linewidth=2)
    
    ax.set_xlabel("Payload Size (KB)")
    ax.set_ylabel("Trace Duration (ms)")
    ax.set_title("Trace duration vs payload size – Istio vs Plain (Client+Server traces only)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    ax.set_xscale('log')  # Log scale often works better for payload sizes
    
    return ax


def plot_latency_vs_load(
    data: dict,
    estimator: Union[str, Callable] = "mean",
    ci: Union[int, str] = 95,
    ax: Optional[plt.Axes] = None,
):
    """Line plot of trace duration vs VU load - only traces with both client and server spans."""
    # Use trace data instead of span data, filter for traces with server_present=True
    traces = pd.concat([
        data["plain"]["traces"].assign(impl="Plain"),
        data["istio"]["traces"].assign(impl="Istio"),
    ])
    
    # Only include traces that have both client and server spans
    traces = traces[traces["server_present"] == True].copy()
    
    # Filter out rows where vus is None (payload-based data)
    if 'vus' in traces.columns:
        traces = traces[traces["vus"].notna()].copy()
    else:
        raise ValueError("No vus column found in traces data")

    estf = {"mean": np.mean, "median": np.median}.get(estimator, estimator)
    if not callable(estf):
        raise ValueError("estimator must be 'mean', 'median' or callable")

    # Create single plot showing trace duration vs load
    sns.set_theme(style="whitegrid", context="talk")
    ax = ax or plt.gca()
    
    # Aggregate data manually for single plot
    agg_data = traces.groupby(['impl', 'vus']).agg({
        'trace_duration_ms': [estf, 'std', 'count']
    }).reset_index()
    
    # Flatten column names
    agg_data.columns = ['impl', 'vus', 'duration_mean', 'duration_std', 'count']
    
    # Calculate confidence intervals
    from scipy import stats
    alpha = 0.05 if ci == 95 else (100 - ci) / 100
    agg_data['ci'] = agg_data.apply(lambda row: 
        stats.t.interval(1-alpha, row['count']-1, 
                       loc=row['duration_mean'], 
                       scale=row['duration_std']/np.sqrt(row['count']))[1] - row['duration_mean']
        if row['count'] > 1 else 0, axis=1)
    
    # Plot
    for impl in agg_data['impl'].unique():
        impl_data = agg_data[agg_data['impl'] == impl]
        ax.errorbar(impl_data['vus'], impl_data['duration_mean'], 
                   yerr=impl_data['ci'], label=impl, marker='o', capsize=5, linewidth=2)
    
    ax.set_xlabel("VUs")
    ax.set_ylabel("Trace Duration (ms)")
    ax.set_title("Trace duration vs load – Istio vs Plain (Client+Server traces only)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    
    return ax


def remove_outlier_traces(data: dict, factor: float = 2.0) -> dict:
    """
    Drop traces whose duration > Q3 + factor·IQR on trace_duration_ms.
    """
    data = deepcopy(data)
    # work on the ms column
    dur_col = "trace_duration_ms"
    all_traces = pd.concat([data["plain"]["traces"], data["istio"]["traces"]])
    q1 = all_traces[dur_col].quantile(0.25)
    q3 = all_traces[dur_col].quantile(0.75)
    upper = q3 + factor * (q3 - q1)

    keep_ids = set(all_traces.loc[all_traces[dur_col] <= upper, "trace_id"])

    for impl in ("plain", "istio"):
        data[impl]["traces"] = (
            data[impl]["traces"]
            .query("trace_id in @keep_ids")
            .reset_index(drop=True)
        )
        data[impl]["spans"] = (
            data[impl]["spans"]
            .query("trace_id in @keep_ids")
            .reset_index(drop=True)
        )

    return data


def to_milliseconds(data: dict) -> dict:
    """
    Convert latency & duration columns to milliseconds in-place.
    """
    for impl in ("plain", "istio"):
        sp = data[impl]["spans"]
        tr = data[impl]["traces"]

        sp["latency_ms"] = sp["latency_s"] * 1_000
        tr["trace_duration_ms"] = tr["trace_duration_s"] * 1_000

        sp.drop(columns=["latency_s"], inplace=True)
        tr.drop(columns=["trace_duration_s"], inplace=True)

    return data


def clean_span_latencies(data: dict) -> dict:
    """
    Drop any span with latency_ms <= 0, and prune its trace.
    """
    data = deepcopy(data)
    for impl in ("plain", "istio"):
        sp = data[impl]["spans"]
        bad = sp["latency_ms"] <= 0
        bad_ids = set(sp.loc[bad, "trace_id"])
        sp.drop(index=sp.index[bad], inplace=True)
        # prune empty traces
        data[impl]["traces"] = (
            data[impl]["traces"]
            .query("trace_id not in @bad_ids")
            .reset_index(drop=True)
        )
    return data


def remove_span_outliers(data: dict, factor: float = 1.5) -> dict:
    """
    For each impl, drop spans whose latency_ms > Q3+factor·IQR
    within its (name, dimension) group where dimension is either vus or payload_kb.
    Then prune traces without spans.
    """
    data = deepcopy(data)
    for impl in ("plain","istio"):
        sp = data[impl]["spans"]
        
        # Determine grouping dimension
        has_vus = 'vus' in sp.columns and sp['vus'].notna().any()
        has_payload = 'payload_kb' in sp.columns and sp['payload_kb'].notna().any()
        
        if has_vus:
            group_cols = ["name", "vus"]
        elif has_payload:
            group_cols = ["name", "payload_kb"]
        else:
            group_cols = ["name"]  # fallback
        
        # compute IQR per group
        def filter_grp(g):
            q1 = g.latency_ms.quantile(0.25)
            q3 = g.latency_ms.quantile(0.75)
            upper = q3 + factor*(q3-q1)
            return g[g.latency_ms <= upper]
        
        sp_clean = sp.groupby(group_cols, group_keys=False).apply(filter_grp)
        data[impl]["spans"] = sp_clean.reset_index(drop=True)
        
        # prune traces lacking spans
        good_ids = set(sp_clean.trace_id)
        data[impl]["traces"] = data[impl]["traces"].query(
            "trace_id in @good_ids"
        ).reset_index(drop=True)
    return data


def prepare_data(raw: dict,
                 span_iqr: float = 1.5,
                 trace_iqr: float = 2.0) -> dict:
    """
    1) convert → ms
    2) drop non‐positive spans
    3) remove per‐span outliers (span_iqr)
    4) remove per‐trace outliers (trace_iqr on trace_duration_ms)
    """
    # 1 & 2
    data = to_milliseconds(deepcopy(raw))
    data = clean_span_latencies(data)
    # 3
    data = remove_span_outliers(data, factor=span_iqr)
    # 4
    data = remove_outlier_traces(data, factor=trace_iqr)
    return data


def plot_mean_trace_duration_by_dimension(
    data: dict,
    estimator: Union[str, Callable] = "mean",
    ci: Union[int, str, None] = 95,
    ax: Optional[plt.Axes] = None,
    hue_order: Tuple[str, str] = ("Plain", "Istio"),
):
    """Bar chart: average trace duration (ms) versus the available dimension (VUs or payload size), Istio vs. Plain.
    Only includes traces with both client and server spans.

    Parameters
    ----------
    data : dict
        Output of ``prepare_data`` – must contain ``trace_duration_ms`` in each traces DataFrame.
    estimator : str or callable, default "mean"
        Aggregation function – either "mean", "median", or a custom callable.
    ci : int, str, or None, default 95
        Size of the confidence interval to draw on the bars. Use "sd" for
        standard deviation, None to omit.
    ax : matplotlib.axes.Axes, optional
        Axis to draw on. If None, uses current axis.
    hue_order : tuple of str, default ("Plain", "Istio")
        Order of implementations in the legend.
    """
    # Use trace data instead of span data, filter for traces with server_present=True
    traces = pd.concat([
        data["plain"]["traces"].assign(impl="Plain"),
        data["istio"]["traces"].assign(impl="Istio"),
    ])
    
    # Only include traces that have both client and server spans
    traces = traces[traces["server_present"] == True].copy()
    
    # Determine which dimension to use - check if columns exist first
    has_vus = 'vus' in traces.columns and traces['vus'].notna().any()
    has_payload = 'payload_kb' in traces.columns and traces['payload_kb'].notna().any()
    
    if has_vus:
        x_col = "vus"
        x_label = "Virtual users (VUs)"
        title_suffix = "vs load"
        order = sorted(traces["vus"].unique())
    elif has_payload:
        x_col = "payload_kb"  
        x_label = "Payload size (KB)"
        title_suffix = "vs payload size"
        order = sorted(traces["payload_kb"].unique())
    else:
        raise ValueError("Neither VUs nor payload_kb data found")

    estf = {"mean": np.mean, "median": np.median}.get(estimator, estimator)
    if not callable(estf):
        raise ValueError("estimator must be 'mean', 'median' or callable")

    sns.set_theme(style="whitegrid", context="talk")
    ax = ax or plt.gca()

    sns.barplot(
        data=traces,
        x=x_col,
        y="trace_duration_ms",
        hue="impl",
        estimator=estf,
        errorbar=("ci", ci),
        order=order,
        hue_order=hue_order,
        capsize=0.1,
        ax=ax,
    )

    ax.set_xlabel(x_label)
    ax.set_ylabel(f"{estimator.capitalize()} trace duration (ms)")
    ax.set_title(f"{estimator.capitalize()} trace duration {title_suffix} – Istio vs Plain (Client+Server traces only)")

    return ax


# ----------------------------------------------------------------------
# 6. Sanity-check when run as a script
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import hashlib
    import pickle
    import gzip
    from datetime import datetime
    
    data = load_all_traces()
    data = prepare_data(data)  
    s = data["stats"]
    print(f"\nSkipped malformed span-like objects: {s.get('malformed_spans',0)}")
    for k, v in s.items():
        if k.startswith("missing_"):
            print(f"  • {k.replace('missing_', ''):22}: {v}")

    for impl in ("plain", "istio"):
        print(f"\n=== {impl.upper()} – first 2 spans ===")
        print(data[impl]["spans"].head(2))

    # Determine what type of data we have
    all_spans = pd.concat([data["plain"]["spans"], data["istio"]["spans"]])
    has_vus = 'vus' in all_spans.columns and all_spans['vus'].notna().any()
    has_payload = 'payload_kb' in all_spans.columns and all_spans['payload_kb'].notna().any()
    
    if has_payload:
        print("\n=== PAYLOAD-BASED DATA DETECTED ===")
        
        # Span Latency Plot for 5KB payload
        fig1 = plt.figure(figsize=(13,6))
        plot_span_latency(data, payload_kb=5)
        plt.tight_layout()
        fig1.savefig('./new-plots/span_latency_5kb.png')

        # Trace Duration Plot for 5KB payload
        fig2 = plt.figure(figsize=(13,6))
        plot_trace_duration(data, payload_kb=5)
        plt.tight_layout()
        fig2.savefig('./new-plots/trace_duration_5kb.png')

        # Mean Trace Duration vs Payload Size
        fig3, ax3 = plt.subplots(figsize=(13, 6))
        plot_mean_trace_duration_by_dimension(data, ax=ax3)
        fig3.tight_layout()
        fig3.savefig('./new-plots/mean_trace_duration_vs_payload.png')
        
        # Trace Duration vs Payload Size (line plot)
        fig4, ax4 = plt.subplots(figsize=(13, 6))
        plot_latency_vs_payload(data, ax=ax4)
        fig4.tight_layout()
        fig4.savefig('./new-plots/trace_duration_vs_payload.png')
        
        print("Payload-based plots saved successfully!")
        
    elif has_vus:
        print("\n=== VU-BASED DATA DETECTED ===")
        
        # Span Latency Plot for 50 VUs
        fig1 = plt.figure(figsize=(13,6))
        plot_span_latency(data, vus=50)
        plt.tight_layout()
        fig1.savefig('./new-plots/span_latency_50vu.png')

        # Trace Duration Plot for 50 VUs
        fig2 = plt.figure(figsize=(13,6))
        plot_trace_duration(data, vus=50)
        plt.tight_layout()
        fig2.savefig('./new-plots/trace_duration_50vu.png')

        # Mean Trace Duration vs VUs
        fig3, ax3 = plt.subplots(figsize=(13, 6))
        plot_mean_trace_duration_by_dimension(data, ax=ax3)
        fig3.tight_layout()
        fig3.savefig('./new-plots/mean_trace_duration_vs_vus.png')
        
        # Trace Duration vs VUs (line plot)
        fig4, ax4 = plt.subplots(figsize=(13, 6))
        plot_latency_vs_load(data, ax=ax4)
        fig4.tight_layout()
        fig4.savefig('./new-plots/trace_duration_vs_vus.png')
        
        print("VU-based plots saved successfully!")
        

        vus_list = [5, 10, 25, 50]
        fig, axes = plt.subplots(nrows=4, ncols=1, figsize=(13, 6 * len(vus_list)), sharex=True)
        for ax, vus in zip(axes, vus_list):
            plot_trace_duration(data, vus=vus, ax=ax)
            # Otherwise, set the current axis before calling:
            # plt.sca(ax)
            # plot_trace_duration(data, vus=vus)
            ax.set_title(f"Trace Duration (VUs = {vus})")
            ax.grid(True)
        plt.tight_layout()
        fig.savefig('./new-plots/trace_duration_subplots.png')

    else:
        print("No VU or payload data found!")

    print("\nAll plots saved successfully!")