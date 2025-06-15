#!/usr/bin/env python3
"""
trace_loader.py
---------------

Parse Istio *and* non-Istio OTLP logs into tidy pandas DataFrames.

Expected file names inside LOG_DIR (any number of VUs):

    logs_istio_client_10vus.txt   logs_client_10vus.txt
    logs_istio_server_10vus.txt   logs_server_10vus.txt
    logs_istio_client_50vus.txt   logs_client_50vus.txt
    ...

Key features
------------
* No command-line arguments – just set LOG_DIR below.
* Adds metadata columns: implementation, vus, side.
* Keeps traces that only appear in client files (circuit-breaker cases).
* Returns dict   {"istio": {"spans": df, "traces": df}, "plain": {...}}

Usage in a notebook
-------------------
    from trace_loader import load_all_traces, LOG_DIR

    data = load_all_traces(LOG_DIR)
    istio_spans  = data["istio"]["spans"]
    plain_traces = data["plain"]["traces"]

    # Example: latency violin plot, Istio vs. Plain, 50 VUs
    import seaborn as sns, matplotlib.pyplot as plt                # optional
    sns.violinplot(
        data=pd.concat([istio_spans, plain_spans]),
        x="implementation", y="latency_s", hue="side",
        order=["plain", "istio"]
    )
    plt.title("Span latencies – 50 VUs"); plt.show()
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
LOG_DIR = Path("/home/hpc/aleksandr/plots/raw_data_load")          # <-- edit me


# ----------------------------------------------------------------------
# 2. Filename helpers
# ----------------------------------------------------------------------
FILE_RE = re.compile(
    r"^logs(?:_istio)?_(client|server)_(\d+)vus\.txt$", re.IGNORECASE
)


def _parse_filename(fname: str) -> Tuple[str, str, int]:
    """(implementation, side, vus) from filename."""
    m = FILE_RE.match(fname)
    if not m:
        raise ValueError(f"Unknown filename pattern: {fname}")
    side, vus = m.groups()
    implementation = "istio" if "_istio_" in fname else "plain"
    return implementation, side, int(vus)


# ----------------------------------------------------------------------
# 3. Span extraction with robust key checking
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
    vus: int,
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
                out.append(
                    {
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
                        "vus": vus,
                        "source_file": source_file,
                    }
                )
    return out


def _parse_line(
    raw: str,
    *,
    implementation: str,
    side: str,
    vus: int,
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
        source_file=source_file,
        stats=stats,
    )


# ----------------------------------------------------------------------
# 4. Public loader
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
            impl, side, vus = _parse_filename(fp.name)
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
                        source_file=fp.name,
                        stats=stats_global,
                    )
                )

    if not spans:
        raise RuntimeError("No valid OTLP spans found!")

    spans_df = pd.DataFrame(spans)

    traces_df = (
        spans_df.groupby(["trace_id", "implementation", "vus"], sort=False)
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
# 6. PLOTTING HELPERS  – requires seaborn≥0.13 and matplotlib≥3.7
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
                      ax: Optional[plt.Axes] = None) -> plt.Axes:
    """
    Bar-with-CI comparison of mean span latency (milliseconds) for each span
    name, Istio vs. Plain.  Optionally filter on `vus` level.
    """
    data = harmonise_span_sets(data)

    spans = pd.concat([
        data["plain"]["spans"].assign(impl="Plain"),
        data["istio"]["spans"].assign(impl="Istio"),
    ])

    if vus is not None:
        spans = spans.query("vus == @vus")

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
    ax.set_title(
        f"Mean span latency – {'all loads' if vus is None else f'{vus} VUs'}")
    ax.set_xlabel("Span name")
    ax.set_ylabel("Latency (ms)")

    # rotate & right-align tick labels
    ax.tick_params(axis="x", labelrotation=45)
    for lbl in ax.get_xticklabels():
        lbl.set_horizontalalignment("right")

    return ax


def plot_trace_duration(data, vus=None, ax=None):
    
    traces = pd.concat([
        data["plain"]["traces"].assign(impl="Plain"),
        data["istio"]["traces"].assign(impl="Istio"),
    ])
    if vus is not None:
        traces = traces.query("vus==@vus")

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

    ax.set_title(
        f"Trace duration distribution – {'all' if vus is None else vus} VUs"
    )
    ax.set_xlabel("")
    ax.set_ylabel("Duration (ms)")
    return ax


def plot_span_latency_vs_load(
    data: dict,
    estimator: Union[str, Callable] = "mean",
    ci: Union[int, str] = 95,
    col_wrap: int = 3,
    ax: Optional[plt.Axes] = None,
):
    """Line plot of trace duration vs load - only traces with both client and server spans."""
    # Use trace data instead of span data, filter for traces with server_present=True
    traces = pd.concat([
        data["plain"]["traces"].assign(impl="Plain"),
        data["istio"]["traces"].assign(impl="Istio"),
    ])
    
    # Only include traces that have both client and server spans
    traces = traces[traces["server_present"] == True].copy()

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
    within its (name, vus) group. Then prune traces without spans.
    """
    data = deepcopy(data)
    for impl in ("plain","istio"):
        sp = data[impl]["spans"]
        # compute IQR per (name,vus)
        def filter_grp(g):
            q1 = g.latency_ms.quantile(0.25)
            q3 = g.latency_ms.quantile(0.75)
            upper = q3 + factor*(q3-q1)
            return g[g.latency_ms <= upper]
        sp_clean = sp.groupby(["name","vus"], group_keys=False).apply(filter_grp)
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


def plot_mean_span_latency_by_vus(
    data: dict,
    estimator: Union[str, Callable] = "mean",
    ci: Union[int, str, None] = 95,
    ax: Optional[plt.Axes] = None,
    hue_order: Tuple[str, str] = ("Plain", "Istio"),
):
    """Bar chart: average trace duration (ms) versus VUs, Istio vs. Plain.
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

    estf = {"mean": np.mean, "median": np.median}.get(estimator, estimator)
    if not callable(estf):
        raise ValueError("estimator must be 'mean', 'median' or callable")

    sns.set_theme(style="whitegrid", context="talk")
    ax = ax or plt.gca()

    sns.barplot(
        data=traces,
        x="vus",
        y="trace_duration_ms",
        hue="impl",
        estimator=estf,
        errorbar=("ci", ci),
        order=sorted(traces["vus"].unique()),
        hue_order=hue_order,
        capsize=0.1,
        ax=ax,
    )

    ax.legend(title="Type")
    ax.set_xlabel("Virtual users (VUs)")
    ax.set_ylabel(f"{estimator.capitalize()} trace duration (ms)")
    ax.set_title(f"{estimator.capitalize()} trace duration vs number of VUs")

    return ax



# ----------------------------------------------------------------------
# 5. Sanity-check when run as a script
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

    # Span Latency Plot
    fig1 = plt.figure(figsize=(13,6))
    plot_span_latency(data, vus=50)
    plt.tight_layout()
    fig1.savefig('./span_latency.png')

    

    # Mean Span Latency vs Load Plot
    fig3, ax3 = plt.subplots(figsize=(13, 6))
    plot_mean_span_latency_by_vus(data, ax=ax3)
    fig3.tight_layout()
    fig3.savefig('./MEAN_span_latency_vs_load.png')
    
    # Span Latency vs Load Plot (single plot version)
    fig4, ax4 = plt.subplots(figsize=(13, 6))
    plot_span_latency_vs_load(data, ax=ax4)
    fig4.tight_layout()
    fig4.savefig('./span_latency_vs_load.png')
    

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
    fig.savefig('./trace_duration_subplots.png')

    print("\nAll plots saved successfully!")