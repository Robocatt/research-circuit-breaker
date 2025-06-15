#!/usr/bin/env python
"""
Compare Jaeger span durations between a “No-Istio” and an “Istio” deployment.
"""

import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path

# ------------------------------------------------------------------------
# 1. Load traces
# ------------------------------------------------------------------------
FILE_NO_ISTIO = Path("./../traces_pure.pkl")   # “No-Istio” run
FILE_ISTIO    = Path("./../traces.pkl")        # “Istio” run

df_no_istio = pd.read_pickle(FILE_NO_ISTIO)
df_istio    = pd.read_pickle(FILE_ISTIO)

# ------------------------------------------------------------------------
# 2. Ensure durations in milliseconds
# ------------------------------------------------------------------------
for df in (df_no_istio, df_istio):
    if "duration_ms" not in df.columns and "duration_us" in df.columns:
        df["duration_ms"] = df["duration_us"] / 1_000  # μs → ms

# ------------------------------------------------------------------------
# 3. Keep only span types common to both data sets
# ------------------------------------------------------------------------
common_ops = set(df_no_istio["operation"]).intersection(df_istio["operation"])
df_no_istio = df_no_istio[df_no_istio["operation"].isin(common_ops)]
df_istio    = df_istio[df_istio["operation"].isin(common_ops)]

# ------------------------------------------------------------------------
# 4. Down-sample to equal span counts per operation
# ------------------------------------------------------------------------
equal_no_istio, equal_istio = [], []

for op in common_ops:
    group_no = df_no_istio[df_no_istio["operation"] == op]
    group_is = df_istio[df_istio["operation"] == op]
    n_equal  = min(len(group_no), len(group_is))    
    equal_no_istio.append(group_no.sample(n_equal, random_state=42))
    equal_istio.append(group_is.sample(n_equal, random_state=42))

df_no_eq = pd.concat(equal_no_istio, ignore_index=True)
df_is_eq = pd.concat(equal_istio,    ignore_index=True)

# ------------------------------------------------------------------------
# 5. Aggregate – mean duration per span type
# ------------------------------------------------------------------------
mean_no = df_no_eq.groupby("operation")["duration_ms"].mean().rename("No-Istio")
mean_is = df_is_eq.groupby("operation")["duration_ms"].mean().rename("Istio version")

mean_df = (
    pd.concat([mean_no, mean_is], axis=1)        # operation  |  No-Istio | Istio version
      .reset_index()
      .melt(id_vars="operation",
            var_name="Implementation",
            value_name="duration_ms")
)

# Order x-axis by overall mean duration (slowest on the right looks intuitive)
order_ops = (
    mean_df.groupby("operation")["duration_ms"]
           .mean()
           .sort_values()
           .index
)

# ------------------------------------------------------------------------
# 6. Plot
# ------------------------------------------------------------------------
sns.set_theme(style="whitegrid", context="talk")  # scientific, high-res style

fig, ax = plt.subplots(figsize=(14, 7))
sns.barplot(
    data=mean_df,
    x="operation",
    y="duration_ms",
    hue="Implementation",
    order=order_ops,
    palette="deep",
    edgecolor="black",
    ax=ax,
    capsize=0.08,
)

# Rotate labels and right‐align them
ax.tick_params(axis="x", labelrotation=45)               # rotate 45°
for lbl in ax.get_xticklabels():                         
    lbl.set_ha("right")                                  # align right

ax.set_xlabel("Span type", labelpad=10)
ax.set_ylabel("Average duration (ms)", labelpad=10)
ax.set_title("Average Span Duration per Implementation", pad=12)

fig.tight_layout()
plt.show()
fig.savefig("out.png") 