#!/usr/bin/env python3
"""
Cost projection at Duolingo scale (~31M DAU, ~50M+ sessions/day).
Models the transition from startup → unicorn → Duolingo-scale.

Assumptions adjusted for scale:
- Duolingo sessions are shorter (~8 min vs our 30 min), but we model OUR session profile
- At scale you negotiate API pricing, build custom infra, use spot/reserved
- GPU clusters have overhead: orchestration, networking, redundancy, ops team
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
from pathlib import Path

OUT = Path(__file__).parent

# ─── Session Profile (same as before) ────────────────────────────────────────
SESSION_INPUT_TOKENS  = 15_000
SESSION_OUTPUT_TOKENS =  5_000
SESSION_IMAGES        =  1.5
DALLE3_PER_IMAGE      = 0.04

# ─── Scale Tiers ─────────────────────────────────────────────────────────────
SCALE_TIERS = [
    10,        # solo dev
    100,       # early users
    1_000,     # small startup
    10_000,    # growing startup
    100_000,   # series B
    500_000,   # pre-IPO
    1_000_000, # large platform
    5_000_000, # major platform
    10_000_000,# approaching Duolingo
    50_000_000,# full Duolingo DAU × sessions
]

# ─── API Pricing at Scale ────────────────────────────────────────────────────
# At enterprise scale, you negotiate volume discounts
# Anthropic/OpenAI typically offer 10-30% at $100K+/mo, up to 40-50% at $1M+/mo

def api_discount(monthly_spend):
    """Step function: negotiated enterprise discounts."""
    if monthly_spend >= 5_000_000:
        return 0.50  # 50% off at $5M+/mo spend
    elif monthly_spend >= 1_000_000:
        return 0.40  # 40% off at $1M+/mo
    elif monthly_spend >= 500_000:
        return 0.30  # 30% off
    elif monthly_spend >= 100_000:
        return 0.20  # 20% off
    elif monthly_spend >= 10_000:
        return 0.10  # 10% off
    return 0.0

def api_cost_per_session(provider):
    """Base per-session LLM + image cost (no voice — local at all scales)."""
    if provider == "sonnet":
        # Anthropic Sonnet with caching
        llm = (15000 * 3.0 / 1e6 + 5000 * 15.0 / 1e6
               + 10000 * 0.30 / 1e6 + 2000 * 3.75 / 1e6)
        haiku = 3000 * 0.25 / 1e6 + 1000 * 1.25 / 1e6
        return llm + haiku + SESSION_IMAGES * DALLE3_PER_IMAGE
    elif provider == "gpt-4o-mini":
        llm = (15000 * 0.15 / 1e6 + 5000 * 0.60 / 1e6
               + 10000 * 0.075 / 1e6)
        return llm + SESSION_IMAGES * DALLE3_PER_IMAGE
    elif provider == "groq-70b":
        llm = 15000 * 0.59 / 1e6 + 5000 * 0.79 / 1e6
        return llm + SESSION_IMAGES * DALLE3_PER_IMAGE
    elif provider == "groq-8b":
        llm = 15000 * 0.05 / 1e6 + 5000 * 0.08 / 1e6
        return llm + SESSION_IMAGES * DALLE3_PER_IMAGE
    elif provider == "custom-70b":
        # Self-hosted 70B: only image cost remains as API
        return SESSION_IMAGES * DALLE3_PER_IMAGE
    elif provider == "custom-8b":
        return SESSION_IMAGES * DALLE3_PER_IMAGE


def api_monthly_at_scale(sessions_per_day, provider):
    """Monthly API cost with volume discount step functions."""
    base = api_cost_per_session(provider)
    raw_monthly = base * sessions_per_day * 30
    discount = api_discount(raw_monthly)
    return raw_monthly * (1 - discount)


# ─── Self-Hosted GPU Cluster Costs ───────────────────────────────────────────
# At Duolingo scale you're not renting one GPU — you're building clusters

# Throughput: sessions per day per GPU
# CRITICAL: throughput depends on MODEL SIZE, not just GPU
# An H100 running 8B does 5x more sessions than running 70B
GPU_CAPACITY = {
    # (gpu, model_size) → sessions/day
    "H100_70B":   500,    # Llama-70B-FP16, ~15-18 concurrent sessions
    "H100_8B":   2500,    # Llama-8B or fine-tuned small, ~80+ concurrent
    "A100_70B":   300,    # Llama-70B-Q4, ~10 concurrent
    "A100_8B":   1500,    # Llama-8B, ~50 concurrent
    "L4_8B":      100,    # Llama-8B only, limited VRAM
}
# At Duolingo scale, you'd fine-tune a small model (8B or smaller),
# not run 70B for every session. The quality gap shrinks with
# domain-specific fine-tuning + good prompting.

# Cloud GPU cost per unit per month at different commitment levels
# NOTE: these are per-GPU costs regardless of what model you run on them
CLOUD_COST = {
    "H100_ondemand":   {"per_gpu": 1818, "label": "H100 on-demand"},
    "H100_1yr":        {"per_gpu": 1350, "label": "H100 1yr reserved"},
    "H100_3yr":        {"per_gpu": 912,  "label": "H100 3yr reserved"},
    "A100_1yr":        {"per_gpu": 657,  "label": "A100 1yr reserved"},
    "A100_3yr":        {"per_gpu": 474,  "label": "A100 3yr reserved"},
}

# Own hardware at data center scale
OWN_HW_COST = {
    "H100_owned": {
        "per_gpu_purchase": 30_000,
        "per_gpu_power_monthly": 60,   # 700W × 24h × 30d × $0.12/kWh ÷ 1000
        "amortize_months": 36,
    },
    "A100_owned": {
        "per_gpu_purchase": 12_000,  # used market
        "per_gpu_power_monthly": 35,
        "amortize_months": 36,
    },
}

# Cluster overhead scales sublinearly (ops team, networking, orchestration)
def cluster_overhead(n_gpus):
    """Monthly overhead for running a GPU cluster."""
    if n_gpus <= 0:
        return 0
    # Base: monitoring, storage, networking
    base = 500
    # Ops team: 1 SRE per ~50 GPUs, each costs ~$15K/mo loaded
    n_sres = max(1, int(np.ceil(n_gpus / 50)))
    ops = n_sres * 15_000
    # Networking, switches, cooling overhead
    infra = n_gpus * 20
    # Kubernetes/orchestration
    k8s = 200 + n_gpus * 5
    return base + ops + infra + k8s


def cloud_cluster_monthly(sessions_per_day, gpu_type="H100", model_size="70B", commitment="1yr"):
    """Cost of a cloud GPU cluster sized for the load."""
    cap_key = f"{gpu_type}_{model_size}"
    cap = GPU_CAPACITY[cap_key]
    n_gpus = int(np.ceil(sessions_per_day / cap))
    n_gpus = max(n_gpus, 1)

    # Add 20% headroom for redundancy/failover at scale
    if n_gpus > 10:
        n_gpus = int(np.ceil(n_gpus * 1.2))

    key = f"{gpu_type}_{commitment}"
    per_gpu = CLOUD_COST.get(key, CLOUD_COST.get(f"{gpu_type}_1yr"))["per_gpu"]
    gpu_cost = n_gpus * per_gpu
    overhead = cluster_overhead(n_gpus)
    return gpu_cost + overhead, n_gpus


def own_cluster_monthly(sessions_per_day, gpu_type="H100", model_size="70B"):
    """Cost of owning a GPU cluster (amortized purchase + DC costs)."""
    cap_key = f"{gpu_type}_{model_size}"
    cap = GPU_CAPACITY[cap_key]
    n_gpus = int(np.ceil(sessions_per_day / cap))
    n_gpus = max(n_gpus, 1)

    if n_gpus > 10:
        n_gpus = int(np.ceil(n_gpus * 1.2))

    key = f"{gpu_type}_owned"
    hw = OWN_HW_COST[key]
    amortized = hw["per_gpu_purchase"] / hw["amortize_months"] * n_gpus
    power = hw["per_gpu_power_monthly"] * n_gpus
    # Data center colocation at scale: ~$150-300/kW/mo
    colo = n_gpus * 100  # per-GPU colo cost at scale
    overhead = cluster_overhead(n_gpus)
    return amortized + power + colo + overhead, n_gpus


def _money(x, _=None):
    if abs(x) < 1e3: return f"${x:,.0f}"
    if abs(x) < 1e6: return f"${x/1e3:.1f}K"
    if abs(x) < 1e9: return f"${x/1e6:.1f}M"
    return f"${x/1e9:.2f}B"

def _count(x, _=None):
    if abs(x) < 1e3: return f"{x:,.0f}"
    if abs(x) < 1e6: return f"{x/1e3:.0f}K"
    return f"{x/1e6:.0f}M"


# ═══════════════════════════════════════════════════════════════════════════════
# PLOT 1: Monthly Cost — Apples-to-Apples by Quality Tier
# ═══════════════════════════════════════════════════════════════════════════════

def plot_scale_overview():
    spd = np.logspace(1, np.log10(50_000_000), 500)

    # --- "Premium" tier: 70B-class models (Sonnet ≈ Llama-70B quality) ---
    sonnet      = [api_monthly_at_scale(s, "sonnet") for s in spd]
    groq70      = [api_monthly_at_scale(s, "groq-70b") for s in spd]
    cloud_h100_70b = [cloud_cluster_monthly(s, "H100", "70B", "1yr")[0] for s in spd]
    own_h100_70b   = [own_cluster_monthly(s, "H100", "70B")[0] for s in spd]
    own_a100_70b   = [own_cluster_monthly(s, "A100", "70B")[0] for s in spd]

    # --- "Efficient" tier: small model (GPT-4o-mini ≈ fine-tuned 8B quality) ---
    gpt4o_mini  = [api_monthly_at_scale(s, "gpt-4o-mini") for s in spd]
    groq8       = [api_monthly_at_scale(s, "groq-8b") for s in spd]
    cloud_h100_8b  = [cloud_cluster_monthly(s, "H100", "8B", "1yr")[0] for s in spd]
    own_h100_8b    = [own_cluster_monthly(s, "H100", "8B")[0] for s in spd]
    own_a100_8b    = [own_cluster_monthly(s, "A100", "8B")[0] for s in spd]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(20, 9))

    # Left panel: Premium / 70B tier
    ax1.loglog(spd, sonnet,         color="#6366f1", lw=3,   label="API: Sonnet (volume discount)")
    ax1.loglog(spd, groq70,         color="#f59e0b", lw=2.5, label="API: Groq Llama-70B")
    ax1.loglog(spd, cloud_h100_70b, color="#ef4444", lw=2,   label="Cloud: H100 (1yr) running 70B")
    ax1.loglog(spd, own_h100_70b,   color="#14b8a6", lw=2.5, label="Own: H100 cluster running 70B")
    ax1.loglog(spd, own_a100_70b,   color="#7c3aed", lw=2,   label="Own: A100 cluster running 70B")

    # Right panel: Efficient / 8B tier
    ax2.loglog(spd, gpt4o_mini,     color="#10b981", lw=3,   label="API: GPT-4o-mini (volume discount)")
    ax2.loglog(spd, groq8,          color="#22d3ee", lw=2.5, label="API: Groq Llama-8B")
    ax2.loglog(spd, cloud_h100_8b,  color="#ef4444", lw=2,   label="Cloud: H100 (1yr) running 8B")
    ax2.loglog(spd, own_h100_8b,    color="#14b8a6", lw=2.5, label="Own: H100 cluster running 8B")
    ax2.loglog(spd, own_a100_8b,    color="#7c3aed", lw=2,   label="Own: A100 cluster running 8B")

    for ax, title in [(ax1, "Premium Tier (70B-class)"), (ax2, "Efficient Tier (8B / fine-tuned small)")]:
        for ms, ml in [(10, "Dev"), (10_000, "10K"), (1_000_000, "1M"), (50_000_000, "Duolingo")]:
            ax.axvline(x=ms, color="gray", ls=":", alpha=0.3)
            ax.text(ms, 3e9, ml, ha="center", fontsize=8, color="gray",
                    bbox=dict(boxstyle="round,pad=0.2", facecolor="white", alpha=0.8))
        ax.set_xlabel("Sessions per Day")
        ax.set_ylabel("Monthly Cost (USD)")
        ax.set_title(title, fontweight="bold", fontsize=13)
        ax.legend(fontsize=8, loc="upper left")
        ax.grid(True, which="both", alpha=0.2)
        ax.set_xlim(10, 50_000_000)
        ax.set_ylim(10, 5e9)
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(_money))
        ax.xaxis.set_major_formatter(mticker.FuncFormatter(_count))

    fig.suptitle("Monthly Cost: API vs Cloud vs Own Hardware (Apples-to-Apples by Model Size)\n"
                 "Self-hosting a small model competes with small-model APIs; 70B competes with frontier APIs",
                 fontsize=14, fontweight="bold")
    fig.tight_layout()
    fig.savefig(OUT / "08_duolingo_scale.png", dpi=150)
    plt.close(fig)
    print("  08_duolingo_scale.png")


# ═══════════════════════════════════════════════════════════════════════════════
# PLOT 2: GPU Count — 70B vs 8B (the real leverage)
# ═══════════════════════════════════════════════════════════════════════════════

def plot_gpu_count():
    spd = np.logspace(2, np.log10(50_000_000), 300)

    def gpus_needed(s, cap):
        n = int(np.ceil(s / cap))
        return int(np.ceil(n * 1.2)) if n > 10 else n

    h100_70b = [gpus_needed(s, GPU_CAPACITY["H100_70B"]) for s in spd]
    h100_8b  = [gpus_needed(s, GPU_CAPACITY["H100_8B"]) for s in spd]
    a100_70b = [gpus_needed(s, GPU_CAPACITY["A100_70B"]) for s in spd]
    a100_8b  = [gpus_needed(s, GPU_CAPACITY["A100_8B"]) for s in spd]

    fig, ax = plt.subplots(figsize=(14, 8))
    ax.loglog(spd, h100_70b, color="#ef4444", lw=2.5, label="H100 running 70B (500 sess/day/GPU)")
    ax.loglog(spd, h100_8b,  color="#ef4444", lw=2, ls="--", label="H100 running 8B (2,500 sess/day/GPU)")
    ax.loglog(spd, a100_70b, color="#f97316", lw=2.5, label="A100 running 70B (300 sess/day/GPU)")
    ax.loglog(spd, a100_8b,  color="#f97316", lw=2, ls="--", label="A100 running 8B (1,500 sess/day/GPU)")

    # Annotations at Duolingo scale
    for cap_key, color, label in [
        ("H100_70B", "#ef4444", "H100+70B"),
        ("H100_8B", "#ef4444", "H100+8B"),
    ]:
        n = gpus_needed(50_000_000, GPU_CAPACITY[cap_key])
        ax.annotate(f"Duolingo: {n:,} GPUs\n({label})", xy=(50_000_000, n),
                   fontsize=9, fontweight="bold", color=color,
                   xytext=(50_000_000 * 0.15, n * 2.5),
                   arrowprops=dict(arrowstyle="->", color=color))

    ax.set_xlabel("Sessions per Day", fontsize=12)
    ax.set_ylabel("GPUs Required (with 20% redundancy)", fontsize=12)
    ax.set_title("GPU Fleet Size: Model Choice is the Biggest Lever\n"
                 "(8B needs 5x fewer GPUs than 70B for the same throughput)",
                 fontsize=14, fontweight="bold")
    ax.legend(fontsize=10)
    ax.grid(True, which="both", alpha=0.2)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(_count))
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(_count))
    fig.tight_layout()
    fig.savefig(OUT / "09_gpu_count.png", dpi=150)
    plt.close(fig)
    print("  09_gpu_count.png")


# ═══════════════════════════════════════════════════════════════════════════════
# PLOT 3: Unit Economics — Cost per Session at Scale
# ═══════════════════════════════════════════════════════════════════════════════

def plot_unit_economics():
    spd = np.logspace(1, np.log10(50_000_000), 500)

    # APIs
    sonnet_ps   = [api_monthly_at_scale(s, "sonnet") / (s * 30) for s in spd]
    gpt4o_ps    = [api_monthly_at_scale(s, "gpt-4o-mini") / (s * 30) for s in spd]
    groq70_ps   = [api_monthly_at_scale(s, "groq-70b") / (s * 30) for s in spd]
    groq8_ps    = [api_monthly_at_scale(s, "groq-8b") / (s * 30) for s in spd]

    # Self-hosted (the fair comparisons)
    own_h100_70b_ps = [own_cluster_monthly(s, "H100", "70B")[0] / (s * 30) for s in spd]
    own_h100_8b_ps  = [own_cluster_monthly(s, "H100", "8B")[0] / (s * 30) for s in spd]
    own_a100_8b_ps  = [own_cluster_monthly(s, "A100", "8B")[0] / (s * 30) for s in spd]

    fig, ax = plt.subplots(figsize=(16, 9))

    # Premium tier
    ax.semilogx(spd, sonnet_ps,       color="#6366f1", lw=3,   label="Sonnet API (70B-class)")
    ax.semilogx(spd, groq70_ps,       color="#f59e0b", lw=2.5, label="Groq Llama-70B API")
    ax.semilogx(spd, own_h100_70b_ps, color="#14b8a6", lw=2.5, label="Own H100 running 70B")

    # Efficient tier
    ax.semilogx(spd, gpt4o_ps,        color="#10b981", lw=3,   label="GPT-4o-mini API (small-class)")
    ax.semilogx(spd, groq8_ps,        color="#22d3ee", lw=2,   label="Groq Llama-8B API")
    ax.semilogx(spd, own_h100_8b_ps,  color="#ef4444", lw=2.5, ls="--", label="Own H100 running 8B (fine-tuned)")
    ax.semilogx(spd, own_a100_8b_ps,  color="#7c3aed", lw=2,   ls="--", label="Own A100 running 8B (fine-tuned)")

    # ARPU reference
    ax.axhline(y=0.09, color="green", ls=":", lw=2, alpha=0.6)
    ax.text(15, 0.092, "Duolingo ARPU breakeven (~$0.09/session)", fontsize=9,
            color="green", fontstyle="italic")
    ax.axhline(y=0.01, color="green", ls=":", lw=1, alpha=0.4)
    ax.text(15, 0.012, "Target: $0.01/session", fontsize=8, color="green", alpha=0.6)

    ax.axvline(x=50_000_000, color="gray", ls="--", alpha=0.3)
    ax.text(50_000_000, 0.22, "Duolingo", ha="center", fontsize=10, color="gray")

    ax.set_xlabel("Sessions per Day", fontsize=12)
    ax.set_ylabel("Cost per Session (USD)", fontsize=12)
    ax.set_title("Unit Economics at Scale\n"
                 "Self-hosting wins at scale — but only when you compare the SAME model class",
                 fontsize=14, fontweight="bold")
    ax.legend(fontsize=8, loc="upper right", ncol=2)
    ax.set_ylim(0, 0.25)
    ax.set_xlim(10, 50_000_000)
    ax.grid(True, which="both", alpha=0.2)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(_count))
    fig.tight_layout()
    fig.savefig(OUT / "10_unit_economics.png", dpi=150)
    plt.close(fig)
    print("  10_unit_economics.png")


# ═══════════════════════════════════════════════════════════════════════════════
# PLOT 4: Annual Cost at Milestones (with 8B tier)
# ═══════════════════════════════════════════════════════════════════════════════

def plot_annual_milestones():
    milestones = [100, 1_000, 10_000, 100_000, 1_000_000, 10_000_000, 50_000_000]
    labels = ["100/d\nHobby", "1K/d\nStartup", "10K/d\nGrowing", "100K/d\nSeries B",
              "1M/d\nPlatform", "10M/d\nLarge", "50M/d\nDuolingo"]

    options = {
        "Sonnet API":            {"fn": lambda s: api_monthly_at_scale(s, "sonnet") * 12,                        "color": "#6366f1"},
        "GPT-4o-mini API":       {"fn": lambda s: api_monthly_at_scale(s, "gpt-4o-mini") * 12,                   "color": "#10b981"},
        "Groq Llama-70B":        {"fn": lambda s: api_monthly_at_scale(s, "groq-70b") * 12,                      "color": "#f59e0b"},
        "Own H100 (70B)":        {"fn": lambda s: own_cluster_monthly(s, "H100", "70B")[0] * 12,                  "color": "#14b8a6"},
        "Own H100 (8B ft)":      {"fn": lambda s: own_cluster_monthly(s, "H100", "8B")[0] * 12,                   "color": "#ef4444"},
        "Own A100 (8B ft)":      {"fn": lambda s: own_cluster_monthly(s, "A100", "8B")[0] * 12,                   "color": "#7c3aed"},
    }

    fig, ax = plt.subplots(figsize=(18, 10))
    x = np.arange(len(milestones))
    n = len(options)
    width = 0.8 / n

    for i, (name, opt) in enumerate(options.items()):
        vals = [opt["fn"](s) for s in milestones]
        offset = (i - n/2 + 0.5) * width
        bars = ax.bar(x + offset, vals, width, color=opt["color"], label=name,
                     edgecolor="white", linewidth=0.5)
        for bar, val in zip(bars, vals):
            if val > 0:
                ax.text(bar.get_x() + bar.get_width()/2, bar.get_height(),
                       _money(val), ha="center", va="bottom", fontsize=6, rotation=75)

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=10)
    ax.set_ylabel("Annual Cost (USD)", fontsize=12)
    ax.set_title("Annual Infrastructure Cost — Including Fine-Tuned Small Model Option\n"
                 "(Own H100 running 8B = what Duolingo would actually do)",
                 fontsize=14, fontweight="bold")
    ax.legend(fontsize=9, loc="upper left")
    ax.set_yscale("log")
    ax.set_ylim(100, 2e10)
    ax.grid(axis="y", alpha=0.2, which="both")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(_money))
    fig.tight_layout()
    fig.savefig(OUT / "11_annual_milestones.png", dpi=150)
    plt.close(fig)
    print("  11_annual_milestones.png")


# ═══════════════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════════════

def print_summary():
    print("\n" + "=" * 120)
    print("DUOLINGO-SCALE COST ANALYSIS (CORRECTED: apples-to-apples model tiers)")
    print("=" * 120)

    duo = 50_000_000

    print("\n── Premium Tier (70B-class reasoning) ──")
    print(f"  {'Option':<40} {'Monthly':>14} {'Annual':>14} {'$/session':>12} {'GPUs':>10}")
    print("  " + "-" * 92)
    for label, fn, gpu_fn in [
        ("Sonnet API (50% vol discount)",  lambda: api_monthly_at_scale(duo, "sonnet"), None),
        ("Groq Llama-70B API",             lambda: api_monthly_at_scale(duo, "groq-70b"), None),
        ("Cloud H100 1yr (70B)",           lambda: cloud_cluster_monthly(duo, "H100", "70B", "1yr")[0],
                                           lambda: cloud_cluster_monthly(duo, "H100", "70B", "1yr")[1]),
        ("Own H100 cluster (70B)",         lambda: own_cluster_monthly(duo, "H100", "70B")[0],
                                           lambda: own_cluster_monthly(duo, "H100", "70B")[1]),
        ("Own A100 cluster (70B)",         lambda: own_cluster_monthly(duo, "A100", "70B")[0],
                                           lambda: own_cluster_monthly(duo, "A100", "70B")[1]),
    ]:
        m = fn()
        ps = m / (duo * 30)
        gpus = f"{gpu_fn():>,}" if gpu_fn else "n/a"
        print(f"  {label:<40} {_money(m):>14} {_money(m*12):>14} ${ps:>10.4f} {gpus:>10}")

    print("\n── Efficient Tier (8B / fine-tuned small — what you'd actually run at Duolingo scale) ──")
    print(f"  {'Option':<40} {'Monthly':>14} {'Annual':>14} {'$/session':>12} {'GPUs':>10}")
    print("  " + "-" * 92)
    for label, fn, gpu_fn in [
        ("GPT-4o-mini API (50% vol discount)", lambda: api_monthly_at_scale(duo, "gpt-4o-mini"), None),
        ("Groq Llama-8B API",                  lambda: api_monthly_at_scale(duo, "groq-8b"), None),
        ("Cloud H100 1yr (8B)",                lambda: cloud_cluster_monthly(duo, "H100", "8B", "1yr")[0],
                                               lambda: cloud_cluster_monthly(duo, "H100", "8B", "1yr")[1]),
        ("Own H100 cluster (8B fine-tuned)",   lambda: own_cluster_monthly(duo, "H100", "8B")[0],
                                               lambda: own_cluster_monthly(duo, "H100", "8B")[1]),
        ("Own A100 cluster (8B fine-tuned)",   lambda: own_cluster_monthly(duo, "A100", "8B")[0],
                                               lambda: own_cluster_monthly(duo, "A100", "8B")[1]),
    ]:
        m = fn()
        ps = m / (duo * 30)
        gpus = f"{gpu_fn():>,}" if gpu_fn else "n/a"
        print(f"  {label:<40} {_money(m):>14} {_money(m*12):>14} ${ps:>10.4f} {gpus:>10}")

    # Why own hardware beats API at the RIGHT comparison
    print("\n── WHY THE PREVIOUS MODEL WAS WRONG ──")
    own_70b = own_cluster_monthly(duo, "H100", "70B")[0]
    own_8b = own_cluster_monthly(duo, "H100", "8B")[0]
    gpt_api = api_monthly_at_scale(duo, "gpt-4o-mini")
    print(f"  Old comparison: Own H100 running 70B ({_money(own_70b)}/mo) vs GPT-4o-mini API ({_money(gpt_api)}/mo)")
    print(f"    → Self-hosting looked {own_70b/gpt_api:.1f}x MORE expensive. But this is 70B vs small model!")
    print(f"")
    print(f"  Fair comparison: Own H100 running 8B ({_money(own_8b)}/mo) vs GPT-4o-mini API ({_money(gpt_api)}/mo)")
    print(f"    → Self-hosting is {gpt_api/own_8b:.1f}x MORE expensive... or cheaper? Let's see:")
    if own_8b < gpt_api:
        print(f"    → OWN HARDWARE WINS: {_money(gpt_api - own_8b)}/mo savings = {_money((gpt_api-own_8b)*12)}/yr")
    else:
        print(f"    → API still wins by {_money(own_8b - gpt_api)}/mo (OpenAI's scale advantage)")
    print(f"")
    print(f"  And for 70B-class: Own H100 running 70B ({_money(own_70b)}/mo) vs Sonnet API ({_money(api_monthly_at_scale(duo, 'sonnet'))}/mo)")
    if own_70b < api_monthly_at_scale(duo, "sonnet"):
        print(f"    → OWN HARDWARE WINS: {_money(api_monthly_at_scale(duo, 'sonnet') - own_70b)}/mo savings")
    else:
        print(f"    → API still cheaper (provider's scale advantage)")

    # Unit economics
    print(f"\n── Unit Economics @ $0.09/session ARPU ──")
    for label, ps in [
        ("Own H100 8B (fine-tuned)", own_cluster_monthly(duo, "H100", "8B")[0] / (duo * 30)),
        ("Own A100 8B (fine-tuned)", own_cluster_monthly(duo, "A100", "8B")[0] / (duo * 30)),
        ("Groq Llama-8B API",       api_monthly_at_scale(duo, "groq-8b") / (duo * 30)),
        ("GPT-4o-mini API",         api_monthly_at_scale(duo, "gpt-4o-mini") / (duo * 30)),
        ("Own H100 70B",            own_cluster_monthly(duo, "H100", "70B")[0] / (duo * 30)),
        ("Sonnet API",              api_monthly_at_scale(duo, "sonnet") / (duo * 30)),
    ]:
        status = "VIABLE" if ps < 0.05 else ("TIGHT" if ps < 0.09 else "NO")
        margin = 0.09 - ps
        print(f"  {label:<30} ${ps:.4f}/sess  margin: ${margin:+.4f}  [{status}]")

    print("=" * 120)


if __name__ == "__main__":
    print("Generating Duolingo-scale projections (corrected)...\n")
    plot_scale_overview()
    plot_gpu_count()
    plot_unit_economics()
    plot_annual_milestones()
    print_summary()
    print(f"\nAll plots saved to {OUT}/")
