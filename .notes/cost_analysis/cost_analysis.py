#!/usr/bin/env python3
"""
Cost analysis: API vs Cloud Self-Hosting vs Own Hardware
for the pdf_to_audio teaching platform.

Three cost domains with step functions, capacity limits, and breakeven points.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
from pathlib import Path

OUT = Path(__file__).parent

# ─── Session Profile ──────────────────────────────────────────────────────────
# ~30 min teaching session: 10 LLM turns, TTS for each response, STT for user
SESSION_INPUT_TOKENS   = 15_000
SESSION_OUTPUT_TOKENS  =  5_000
SESSION_CACHE_READ     = 10_000
SESSION_CACHE_WRITE    =  2_000
SESSION_TTS_MINUTES    =  8.0
SESSION_STT_MINUTES    =  5.0
SESSION_IMAGES         =  1.5
SESSION_HAIKU_INPUT    =  3_000
SESSION_HAIKU_OUTPUT   =  1_000

# ─── API Pricing (per 1M tokens unless noted) ────────────────────────────────
ANTHROPIC = {
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    "claude-haiku-4-5":  {"input": 0.25, "output": 1.25,  "cache_read": 0.03, "cache_write": 0.30},
    "claude-opus-4-6":   {"input": 15.0, "output": 75.00, "cache_read": 1.50, "cache_write": 18.75},
}
OPENAI = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60, "cache_read": 0.075},
    "gpt-4o":      {"input": 2.50, "output": 10.0, "cache_read": 1.25},
    "gpt-5-mini":  {"input": 0.25, "output": 2.00, "cache_read": 0.025},
}
INFERENCE_SVC = {
    "Llama-70B (Groq)":     {"input": 0.59, "output": 0.79},
    "Llama-70B (Together)":  {"input": 0.88, "output": 0.88},
    "Llama-70B (Deepinfra)": {"input": 0.52, "output": 0.75},
    "Llama-8B (Groq)":      {"input": 0.05, "output": 0.08},
}
OPENAI_TTS_PER_MIN = 0.015
OPENAI_STT_PER_MIN = 0.006
DALLE3_PER_IMAGE   = 0.04

# ─── Anthropic Tier Discounts ─────────────────────────────────────────────────
# Build: pay-as-you-go (1x)
# Scale: $1K+/mo committed → no discount on price, but higher rate limits
# No formal volume discount on per-token pricing, but Batches API is 50% off
ANTHROPIC_BATCH_DISCOUNT = 0.50  # Batches API: 50% off, 24h turnaround (not real-time)

# ─── Cloud GPU Pricing ────────────────────────────────────────────────────────
# on-demand → 1yr reserved → 3yr reserved (step functions)
CLOUD_GPU = {
    "H100 80GB": {
        "on_demand":    {"hourly": 2.49, "monthly": 2.49 * 730, "provider": "Lambda"},
        "1yr_reserved": {"hourly": 1.85, "monthly": 1.85 * 730, "provider": "CoreWeave/AWS"},
        "3yr_reserved": {"hourly": 1.25, "monthly": 1.25 * 730, "provider": "AWS/GCP"},
        "spot":         {"hourly": 1.50, "monthly": 1.50 * 730, "provider": "RunPod/Vast.ai"},
        "vram_gb": 80, "tdp_watts": 700,
        "capacity_sessions_day": 500,  # throughput-limited (see analysis)
    },
    "A100 80GB": {
        "on_demand":    {"hourly": 1.29, "monthly": 1.29 * 730, "provider": "Lambda"},
        "1yr_reserved": {"hourly": 0.90, "monthly": 0.90 * 730, "provider": "AWS/GCP"},
        "3yr_reserved": {"hourly": 0.65, "monthly": 0.65 * 730, "provider": "AWS/GCP"},
        "spot":         {"hourly": 0.80, "monthly": 0.80 * 730, "provider": "Vast.ai"},
        "vram_gb": 80, "tdp_watts": 400,
        "capacity_sessions_day": 300,
    },
    "L40S 48GB": {
        "on_demand":    {"hourly": 0.74, "monthly": 0.74 * 730, "provider": "RunPod"},
        "1yr_reserved": {"hourly": 0.55, "monthly": 0.55 * 730, "provider": "estimated"},
        "spot":         {"hourly": 0.45, "monthly": 0.45 * 730, "provider": "Vast.ai"},
        "vram_gb": 48, "tdp_watts": 350,
        "capacity_sessions_day": 200,
    },
    "L4 24GB": {
        "on_demand":    {"hourly": 0.44, "monthly": 0.44 * 730, "provider": "RunPod"},
        "1yr_reserved": {"hourly": 0.35, "monthly": 0.35 * 730, "provider": "GCP"},
        "spot":         {"hourly": 0.24, "monthly": 0.24 * 730, "provider": "Vast.ai"},
        "vram_gb": 24, "tdp_watts": 72,
        "capacity_sessions_day": 100,  # 8B model only, limited concurrency
    },
}
CLOUD_OVERHEAD_MONTHLY = 80  # networking, storage, monitoring baseline

# ─── Own Hardware (CapEx + OpEx) ──────────────────────────────────────────────
OWN_HARDWARE = {
    "RTX 4090 24GB": {
        "purchase": 1800,
        "tdp_watts": 450,
        "vram_gb": 24,
        "models": "Llama-8B / 70B-Q4 (tight) + Whisper + Kokoro",
        "capacity_sessions_day": 80,
    },
    "RTX 5090 32GB": {
        "purchase": 2200,
        "tdp_watts": 575,
        "vram_gb": 32,
        "models": "Llama-70B-Q4 + Whisper + Kokoro",
        "capacity_sessions_day": 150,
    },
    "2x RTX 4090": {
        "purchase": 3600,
        "tdp_watts": 900,
        "vram_gb": 48,
        "models": "Llama-70B-Q4 (split) + dedicated STT/TTS GPU",
        "capacity_sessions_day": 200,
    },
    "Used A100 80GB (server)": {
        "purchase": 12000,  # GPU ~$8K used + server chassis/CPU/RAM
        "tdp_watts": 400,
        "vram_gb": 80,
        "models": "Llama-70B-FP16 + Whisper-large + Kokoro",
        "capacity_sessions_day": 300,
    },
    "H100 80GB (server)": {
        "purchase": 30000,  # GPU ~$25K + server
        "tdp_watts": 700,
        "vram_gb": 80,
        "models": "Llama-70B-FP16 + Whisper-large + Kokoro",
        "capacity_sessions_day": 500,
    },
}
# Hosting costs for own hardware
COLOCATION_MONTHLY = 150      # 1U-2U colo with power+network
ELECTRICITY_PER_KWH = 0.12   # US average
HOME_INTERNET_ADDON = 30      # static IP / business tier upgrade
MAINTENANCE_MONTHLY = 50      # parts fund, occasional replacements
AMORTIZE_YEARS = 3            # depreciation period


# ═══════════════════════════════════════════════════════════════════════════════
# Cost Functions
# ═══════════════════════════════════════════════════════════════════════════════

def api_session_cost(provider, model):
    """Per-session cost for an API provider."""
    if provider == "anthropic":
        p = ANTHROPIC[model]
        llm = (SESSION_INPUT_TOKENS * p["input"] / 1e6
               + SESSION_OUTPUT_TOKENS * p["output"] / 1e6
               + SESSION_CACHE_READ * p["cache_read"] / 1e6
               + SESSION_CACHE_WRITE * p["cache_write"] / 1e6)
        h = ANTHROPIC["claude-haiku-4-5"]
        haiku = SESSION_HAIKU_INPUT * h["input"] / 1e6 + SESSION_HAIKU_OUTPUT * h["output"] / 1e6
        return llm + haiku
    elif provider == "openai":
        p = OPENAI[model]
        llm = (SESSION_INPUT_TOKENS * p["input"] / 1e6
               + SESSION_OUTPUT_TOKENS * p["output"] / 1e6
               + SESSION_CACHE_READ * p["cache_read"] / 1e6)
        return llm
    elif provider == "inference":
        p = INFERENCE_SVC[model]
        return SESSION_INPUT_TOKENS * p["input"] / 1e6 + SESSION_OUTPUT_TOKENS * p["output"] / 1e6


def voice_cost_api():
    return SESSION_TTS_MINUTES * OPENAI_TTS_PER_MIN + SESSION_STT_MINUTES * OPENAI_STT_PER_MIN

def image_cost():
    return SESSION_IMAGES * DALLE3_PER_IMAGE


def api_monthly(sessions_per_day, per_session):
    """Pure linear — API costs scale linearly with usage."""
    return per_session * sessions_per_day * 30


def cloud_monthly_stepped(sessions_per_day, gpu_key, commitment="on_demand"):
    """
    Step function: you pay for whole GPUs. When you exceed one GPU's capacity,
    you need a second one (cost doubles), etc.
    """
    gpu = CLOUD_GPU[gpu_key]
    cap = gpu["capacity_sessions_day"]
    price = gpu[commitment]["monthly"]
    spd = np.atleast_1d(sessions_per_day).astype(float)
    n_gpus = np.ceil(spd / cap).astype(int)
    n_gpus = np.maximum(n_gpus, 1)  # minimum 1 GPU
    return n_gpus * (price + CLOUD_OVERHEAD_MONTHLY)


def own_hw_monthly(sessions_per_day, hw_key, hosting="colocation"):
    """
    Own hardware: fixed monthly cost (amortized purchase + power + hosting).
    Steps up when capacity exceeded (buy another unit).
    """
    hw = OWN_HARDWARE[hw_key]
    cap = hw["capacity_sessions_day"]
    spd = np.atleast_1d(sessions_per_day).astype(float)
    n_units = np.ceil(spd / cap).astype(int)
    n_units = np.maximum(n_units, 1)

    # Per-unit monthly costs
    amortized = hw["purchase"] / (AMORTIZE_YEARS * 12)
    power_monthly = hw["tdp_watts"] / 1000 * 24 * 30 * ELECTRICITY_PER_KWH
    if hosting == "colocation":
        hosting_cost = COLOCATION_MONTHLY
    else:  # home
        hosting_cost = HOME_INTERNET_ADDON

    per_unit = amortized + power_monthly + hosting_cost + MAINTENANCE_MONTHLY
    return n_units * per_unit


# ═══════════════════════════════════════════════════════════════════════════════
# PLOT 1: Per-Session Cost Breakdown (unchanged from before, kept for reference)
# ═══════════════════════════════════════════════════════════════════════════════

def plot_session_breakdown():
    configs = {
        "Sonnet\n+OAI Voice":    api_session_cost("anthropic", "claude-sonnet-4-6") + voice_cost_api() + image_cost(),
        "Sonnet\n+Local Voice":  api_session_cost("anthropic", "claude-sonnet-4-6") + image_cost(),
        "Opus\n+OAI Voice":      api_session_cost("anthropic", "claude-opus-4-6") + voice_cost_api() + image_cost(),
        "GPT-4o-mini\n+OAI Voice": api_session_cost("openai", "gpt-4o-mini") + voice_cost_api() + image_cost(),
        "GPT-4o\n+OAI Voice":    api_session_cost("openai", "gpt-4o") + voice_cost_api() + image_cost(),
        "Llama-70B\nGroq":       api_session_cost("inference", "Llama-70B (Groq)") + image_cost(),
        "Llama-70B\nDeepinfra":  api_session_cost("inference", "Llama-70B (Deepinfra)") + image_cost(),
        "Llama-8B\nGroq":        api_session_cost("inference", "Llama-8B (Groq)") + image_cost(),
    }
    fig, ax = plt.subplots(figsize=(14, 6))
    x = np.arange(len(configs))
    vals = list(configs.values())
    colors = ["#6366f1", "#8b5cf6", "#c084fc", "#10b981", "#059669", "#f59e0b", "#f97316", "#22d3ee"]
    bars = ax.bar(x, vals, 0.6, color=colors, edgecolor="white")
    for i, v in enumerate(vals):
        ax.text(i, v + 0.005, f"${v:.3f}", ha="center", fontweight="bold", fontsize=9)
    ax.set_xticks(x)
    ax.set_xticklabels(configs.keys(), fontsize=9)
    ax.set_ylabel("Cost per Session (USD)")
    ax.set_title("Per-Session Cost by Provider (~30 min teaching session)", fontweight="bold")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "01_session_cost_breakdown.png", dpi=150)
    plt.close(fig)
    print("  01_session_cost_breakdown.png")


# ═══════════════════════════════════════════════════════════════════════════════
# PLOT 2: The Big Picture — API vs Cloud vs Own Hardware (step functions)
# ═══════════════════════════════════════════════════════════════════════════════

def plot_three_way_comparison():
    spd = np.arange(1, 601)

    # --- API curves (linear) ---
    sonnet_local = api_monthly(spd, api_session_cost("anthropic", "claude-sonnet-4-6") + image_cost())
    sonnet_voice = api_monthly(spd, api_session_cost("anthropic", "claude-sonnet-4-6") + voice_cost_api() + image_cost())
    gpt4o_mini   = api_monthly(spd, api_session_cost("openai", "gpt-4o-mini") + voice_cost_api() + image_cost())
    groq_70b     = api_monthly(spd, api_session_cost("inference", "Llama-70B (Groq)") + image_cost())

    # --- Cloud GPU (stepped) ---
    cloud_h100_od = cloud_monthly_stepped(spd, "H100 80GB", "on_demand")
    cloud_h100_1y = cloud_monthly_stepped(spd, "H100 80GB", "1yr_reserved")
    cloud_a100_od = cloud_monthly_stepped(spd, "A100 80GB", "on_demand")
    cloud_l4_od   = cloud_monthly_stepped(spd, "L4 24GB", "on_demand")

    # --- Own hardware (stepped) ---
    own_4090_colo = own_hw_monthly(spd, "RTX 4090 24GB", "colocation")
    own_4090_home = own_hw_monthly(spd, "RTX 4090 24GB", "home")
    own_5090_colo = own_hw_monthly(spd, "RTX 5090 32GB", "colocation")
    own_a100_colo = own_hw_monthly(spd, "Used A100 80GB (server)", "colocation")

    fig, ax = plt.subplots(figsize=(16, 9))

    # API (solid lines)
    ax.plot(spd, sonnet_local, color="#6366f1", lw=2.5, label="API: Sonnet + local voice")
    ax.plot(spd, sonnet_voice, color="#8b5cf6", lw=1.5, ls="--", label="API: Sonnet + OpenAI voice")
    ax.plot(spd, gpt4o_mini, color="#10b981", lw=2, label="API: GPT-4o-mini + OpenAI voice")
    ax.plot(spd, groq_70b, color="#f59e0b", lw=2, label="API: Llama-70B (Groq) + local voice")

    # Cloud GPU (step functions, dashed)
    ax.step(spd, cloud_h100_od, color="#ef4444", lw=2, ls="-", where="post",
            label="Cloud: H100 on-demand")
    ax.step(spd, cloud_h100_1y, color="#ef4444", lw=1.5, ls="--", where="post",
            label="Cloud: H100 1yr reserved")
    ax.step(spd, cloud_a100_od, color="#f97316", lw=2, ls="-", where="post",
            label="Cloud: A100 on-demand")
    ax.step(spd, cloud_l4_od, color="#fb923c", lw=1.5, ls="-.", where="post",
            label="Cloud: L4 on-demand")

    # Own hardware (step functions, dotted)
    ax.step(spd, own_4090_colo, color="#14b8a6", lw=2, ls="-", where="post",
            label="Own: RTX 4090 (colo)")
    ax.step(spd, own_4090_home, color="#14b8a6", lw=1.5, ls=":", where="post",
            label="Own: RTX 4090 (home)")
    ax.step(spd, own_5090_colo, color="#0891b2", lw=2, ls="-", where="post",
            label="Own: RTX 5090 (colo)")
    ax.step(spd, own_a100_colo, color="#7c3aed", lw=2, ls="-", where="post",
            label="Own: Used A100 server (colo)")

    ax.set_xlabel("Sessions per Day", fontsize=12)
    ax.set_ylabel("Monthly Cost (USD)", fontsize=12)
    ax.set_title("Total Cost of Ownership: API vs Cloud GPU vs Own Hardware\n"
                 "(step functions from GPU capacity limits & commitment tiers)",
                 fontsize=14, fontweight="bold")
    ax.legend(loc="upper left", fontsize=8, ncol=2)
    ax.set_xlim(1, 600)
    ax.set_ylim(0, 5000)
    ax.grid(alpha=0.3)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
    fig.tight_layout()
    fig.savefig(OUT / "02_three_way_comparison.png", dpi=150)
    plt.close(fig)
    print("  02_three_way_comparison.png")


# ═══════════════════════════════════════════════════════════════════════════════
# PLOT 3: Breakeven Heatmap — sessions/day where each option becomes cheapest
# ═══════════════════════════════════════════════════════════════════════════════

def plot_breakeven_table():
    spd = np.arange(1, 801)

    options = {
        "Sonnet API\n(local voice)":      api_monthly(spd, api_session_cost("anthropic", "claude-sonnet-4-6") + image_cost()),
        "GPT-4o-mini API\n(+OAI voice)":  api_monthly(spd, api_session_cost("openai", "gpt-4o-mini") + voice_cost_api() + image_cost()),
        "Groq Llama-70B\n(local voice)":  api_monthly(spd, api_session_cost("inference", "Llama-70B (Groq)") + image_cost()),
        "Cloud H100\n(on-demand)":        cloud_monthly_stepped(spd, "H100 80GB", "on_demand"),
        "Cloud H100\n(1yr reserved)":     cloud_monthly_stepped(spd, "H100 80GB", "1yr_reserved"),
        "Cloud A100\n(on-demand)":        cloud_monthly_stepped(spd, "A100 80GB", "on_demand"),
        "Cloud L4\n(on-demand)":          cloud_monthly_stepped(spd, "L4 24GB", "on_demand"),
        "Own RTX 4090\n(colo)":           own_hw_monthly(spd, "RTX 4090 24GB", "colocation"),
        "Own RTX 4090\n(home)":           own_hw_monthly(spd, "RTX 4090 24GB", "home"),
        "Own RTX 5090\n(colo)":           own_hw_monthly(spd, "RTX 5090 32GB", "colocation"),
        "Own A100\n(colo)":               own_hw_monthly(spd, "Used A100 80GB (server)", "colocation"),
    }

    # Build cost matrix
    names = list(options.keys())
    costs = np.array([options[n] for n in names])  # shape: (n_options, n_spd)

    # Find cheapest at each spd
    cheapest_idx = np.argmin(costs, axis=0)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 12), gridspec_kw={"height_ratios": [2, 1]})

    # Top: cost curves zoomed to interesting range
    colors = ["#6366f1", "#10b981", "#f59e0b",
              "#ef4444", "#dc2626", "#f97316", "#fb923c",
              "#14b8a6", "#2dd4bf", "#0891b2", "#7c3aed"]
    styles = ["-", "-", "-",
              "-", "--", "-", "-.",
              "-", ":", "-", "-"]

    for i, (name, cost_arr) in enumerate(options.items()):
        ax1.plot(spd, cost_arr, color=colors[i], lw=2 if i < 3 else 1.5,
                ls=styles[i], label=name.replace("\n", " "))

    ax1.set_ylabel("Monthly Cost (USD)", fontsize=12)
    ax1.set_title("All Options with Breakeven Crossings", fontsize=14, fontweight="bold")
    ax1.legend(loc="upper left", fontsize=7, ncol=3)
    ax1.set_xlim(1, 800)
    ax1.set_ylim(0, 6000)
    ax1.grid(alpha=0.3)
    ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))

    # Bottom: cheapest option band chart
    for i in range(len(names)):
        mask = cheapest_idx == i
        if mask.any():
            ax2.fill_between(spd, 0, 1, where=mask, color=colors[i], alpha=0.7, label=names[i].replace("\n", " "))
    ax2.set_xlabel("Sessions per Day", fontsize=12)
    ax2.set_ylabel("Cheapest\nOption", fontsize=10)
    ax2.set_yticks([])
    ax2.set_xlim(1, 800)
    ax2.legend(loc="upper center", fontsize=7, ncol=4)
    ax2.set_title("Which Option Wins at Each Scale?", fontsize=12, fontweight="bold")

    fig.tight_layout()
    fig.savefig(OUT / "03_breakeven_bands.png", dpi=150)
    plt.close(fig)
    print("  03_breakeven_bands.png")


# ═══════════════════════════════════════════════════════════════════════════════
# PLOT 4: Cloud GPU Commitment Tiers (step functions)
# ═══════════════════════════════════════════════════════════════════════════════

def plot_cloud_tiers():
    spd = np.arange(1, 601)

    fig, axes = plt.subplots(2, 2, figsize=(16, 12))

    for ax, (gpu_name, gpu) in zip(axes.flat, CLOUD_GPU.items()):
        tiers = {}
        for commitment in ["on_demand", "1yr_reserved", "3yr_reserved", "spot"]:
            if commitment in gpu:
                tiers[commitment] = cloud_monthly_stepped(spd, gpu_name, commitment)

        tier_colors = {"on_demand": "#ef4444", "1yr_reserved": "#f59e0b",
                       "3yr_reserved": "#10b981", "spot": "#6366f1"}
        tier_labels = {"on_demand": "On-Demand", "1yr_reserved": "1yr Reserved",
                       "3yr_reserved": "3yr Reserved", "spot": "Spot/Preemptible"}

        for tier, costs in tiers.items():
            ax.step(spd, costs, color=tier_colors[tier], lw=2, where="post",
                    label=f"{tier_labels[tier]} (${gpu[tier]['hourly']:.2f}/hr)")

        # Capacity markers
        cap = gpu["capacity_sessions_day"]
        for n in range(1, 4):
            ax.axvline(x=cap * n, color="gray", ls=":", alpha=0.4)
            ax.text(cap * n + 2, ax.get_ylim()[1] if ax.get_ylim()[1] > 0 else 3000,
                    f"{n} GPU{'s' if n > 1 else ''}", fontsize=8, color="gray", va="top")

        ax.set_title(f"{gpu_name} ({gpu['vram_gb']}GB, {gpu['tdp_watts']}W)", fontweight="bold")
        ax.set_xlabel("Sessions/Day")
        ax.set_ylabel("Monthly Cost (USD)")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
        ax.set_xlim(1, 600)
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))

    fig.suptitle("Cloud GPU Monthly Cost by Commitment Tier\n(steps = additional GPUs needed at capacity)",
                 fontsize=14, fontweight="bold")
    fig.tight_layout()
    fig.savefig(OUT / "04_cloud_tiers.png", dpi=150)
    plt.close(fig)
    print("  04_cloud_tiers.png")


# ═══════════════════════════════════════════════════════════════════════════════
# PLOT 5: Own Hardware TCO (amortized CapEx + OpEx)
# ═══════════════════════════════════════════════════════════════════════════════

def plot_own_hardware_tco():
    months = np.arange(1, 37)  # 3 years
    spd_scenarios = [10, 50, 100, 300]

    fig, axes = plt.subplots(2, 2, figsize=(16, 12))

    for ax, spd in zip(axes.flat, spd_scenarios):
        # Cumulative costs over time

        # API: linear accumulation
        sonnet_cum = api_monthly(spd, api_session_cost("anthropic", "claude-sonnet-4-6") + image_cost()) * months
        groq_cum = api_monthly(spd, api_session_cost("inference", "Llama-70B (Groq)") + image_cost()) * months

        # Cloud: monthly recurring
        cloud_h100_cum = np.array([cloud_monthly_stepped(spd, "H100 80GB", "on_demand")] * len(months)).flatten() * months
        cloud_a100_cum = np.array([cloud_monthly_stepped(spd, "A100 80GB", "on_demand")] * len(months)).flatten() * months

        # Own hardware: upfront spike + monthly
        for hw_key, color, ls in [
            ("RTX 4090 24GB", "#14b8a6", "-"),
            ("RTX 5090 32GB", "#0891b2", "-"),
            ("Used A100 80GB (server)", "#7c3aed", "-"),
        ]:
            hw = OWN_HARDWARE[hw_key]
            n_units = max(1, int(np.ceil(spd / hw["capacity_sessions_day"])))
            upfront = hw["purchase"] * n_units
            power = hw["tdp_watts"] / 1000 * 24 * 30 * ELECTRICITY_PER_KWH * n_units
            monthly_opex = (COLOCATION_MONTHLY + MAINTENANCE_MONTHLY + power) * n_units
            own_cum = upfront + monthly_opex * months
            ax.plot(months, own_cum, color=color, lw=2, ls=ls,
                    label=f"Own: {hw_key} ({n_units}x)")

        ax.plot(months, sonnet_cum, color="#6366f1", lw=2, label="API: Sonnet")
        ax.plot(months, groq_cum, color="#f59e0b", lw=2, label="API: Groq Llama-70B")
        ax.plot(months, cloud_h100_cum, color="#ef4444", lw=1.5, ls="--", label="Cloud: H100")
        ax.plot(months, cloud_a100_cum, color="#f97316", lw=1.5, ls="--", label="Cloud: A100")

        ax.set_title(f"{spd} sessions/day", fontweight="bold", fontsize=12)
        ax.set_xlabel("Months")
        ax.set_ylabel("Cumulative Cost (USD)")
        ax.legend(fontsize=7, loc="upper left")
        ax.grid(alpha=0.3)
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))

    fig.suptitle("Cumulative Total Cost of Ownership Over 3 Years\n"
                 "(Own hardware includes upfront purchase + colo + power + maintenance)",
                 fontsize=14, fontweight="bold")
    fig.tight_layout()
    fig.savefig(OUT / "05_own_hardware_tco.png", dpi=150)
    plt.close(fig)
    print("  05_own_hardware_tco.png")


# ═══════════════════════════════════════════════════════════════════════════════
# PLOT 6: Zoomed Low-Volume (your current scale: 1-30 sessions/day)
# ═══════════════════════════════════════════════════════════════════════════════

def plot_low_volume():
    spd = np.arange(1, 31)

    configs = {
        "Sonnet + local voice":     api_monthly(spd, api_session_cost("anthropic", "claude-sonnet-4-6") + image_cost()),
        "Sonnet + OAI voice":       api_monthly(spd, api_session_cost("anthropic", "claude-sonnet-4-6") + voice_cost_api() + image_cost()),
        "GPT-4o-mini + OAI voice":  api_monthly(spd, api_session_cost("openai", "gpt-4o-mini") + voice_cost_api() + image_cost()),
        "Groq Llama-70B + local":   api_monthly(spd, api_session_cost("inference", "Llama-70B (Groq)") + image_cost()),
        "Groq Llama-8B + local":    api_monthly(spd, api_session_cost("inference", "Llama-8B (Groq)") + image_cost()),
    }
    fixed_costs = {
        "Own RTX 4090 (home)":  own_hw_monthly(spd, "RTX 4090 24GB", "home"),
        "Own RTX 4090 (colo)":  own_hw_monthly(spd, "RTX 4090 24GB", "colocation"),
        "Cloud L4 (on-demand)": cloud_monthly_stepped(spd, "L4 24GB", "on_demand"),
    }

    fig, ax = plt.subplots(figsize=(14, 8))
    api_colors = ["#6366f1", "#8b5cf6", "#10b981", "#f59e0b", "#22d3ee"]
    fix_colors = ["#14b8a6", "#0d9488", "#fb923c"]

    for (name, costs), color in zip(configs.items(), api_colors):
        ax.plot(spd, costs, color=color, lw=2.5, label=name)

    for (name, costs), color in zip(fixed_costs.items(), fix_colors):
        ax.step(spd, costs, color=color, lw=2, ls="--", where="post", label=name)

    ax.set_xlabel("Sessions per Day", fontsize=12)
    ax.set_ylabel("Monthly Cost (USD)", fontsize=12)
    ax.set_title("Low-Volume Comparison (1-30 sessions/day)\nYour Current Scale",
                 fontsize=14, fontweight="bold")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3)
    ax.set_xlim(1, 30)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
    fig.tight_layout()
    fig.savefig(OUT / "06_low_volume.png", dpi=150)
    plt.close(fig)
    print("  06_low_volume.png")


# ═══════════════════════════════════════════════════════════════════════════════
# PLOT 7: Cost-per-Session at Scale (amortized fixed costs)
# ═══════════════════════════════════════════════════════════════════════════════

def plot_per_session_at_scale():
    """Shows how per-session cost drops for fixed-cost options as utilization increases."""
    spd = np.arange(1, 601)
    total_sessions_monthly = spd * 30

    # API: flat per-session (horizontal lines)
    sonnet_ps = np.full_like(spd, api_session_cost("anthropic", "claude-sonnet-4-6") + image_cost(), dtype=float)
    groq_ps = np.full_like(spd, api_session_cost("inference", "Llama-70B (Groq)") + image_cost(), dtype=float)
    gpt4o_ps = np.full_like(spd, api_session_cost("openai", "gpt-4o-mini") + voice_cost_api() + image_cost(), dtype=float)

    # Fixed costs: divide monthly by sessions to get effective per-session
    cloud_h100_ps = cloud_monthly_stepped(spd, "H100 80GB", "on_demand") / total_sessions_monthly
    cloud_h100_1y_ps = cloud_monthly_stepped(spd, "H100 80GB", "1yr_reserved") / total_sessions_monthly
    cloud_a100_ps = cloud_monthly_stepped(spd, "A100 80GB", "on_demand") / total_sessions_monthly
    own_4090_ps = own_hw_monthly(spd, "RTX 4090 24GB", "colocation") / total_sessions_monthly
    own_5090_ps = own_hw_monthly(spd, "RTX 5090 32GB", "colocation") / total_sessions_monthly

    fig, ax = plt.subplots(figsize=(14, 8))

    ax.plot(spd, sonnet_ps, color="#6366f1", lw=2.5, label="Sonnet API")
    ax.plot(spd, groq_ps, color="#f59e0b", lw=2.5, label="Groq Llama-70B API")
    ax.plot(spd, gpt4o_ps, color="#10b981", lw=2, label="GPT-4o-mini API")
    ax.plot(spd, cloud_h100_ps, color="#ef4444", lw=2, ls="-", label="Cloud H100 (on-demand)")
    ax.plot(spd, cloud_h100_1y_ps, color="#ef4444", lw=1.5, ls="--", label="Cloud H100 (1yr)")
    ax.plot(spd, cloud_a100_ps, color="#f97316", lw=2, label="Cloud A100 (on-demand)")
    ax.plot(spd, own_4090_ps, color="#14b8a6", lw=2, label="Own RTX 4090 (colo)")
    ax.plot(spd, own_5090_ps, color="#0891b2", lw=2, label="Own RTX 5090 (colo)")

    ax.set_xlabel("Sessions per Day", fontsize=12)
    ax.set_ylabel("Effective Cost per Session (USD)", fontsize=12)
    ax.set_title("Effective Per-Session Cost at Scale\n(fixed costs amortized over utilization — sawtooth = capacity steps)",
                 fontsize=14, fontweight="bold")
    ax.legend(fontsize=9, loc="upper right")
    ax.set_xlim(1, 600)
    ax.set_ylim(0, 1.0)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "07_per_session_at_scale.png", dpi=150)
    plt.close(fig)
    print("  07_per_session_at_scale.png")


# ═══════════════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════════════

def print_summary():
    print("\n" + "=" * 90)
    print("COST ANALYSIS — pdf_to_audio Teaching Platform (April 2025)")
    print("=" * 90)

    # API costs
    print("\n── API Per-Session Costs ──")
    print(f"{'Config':<35} {'$/session':>10} {'10/day $/mo':>13} {'100/day $/mo':>14}")
    print("-" * 75)
    for label, (prov, model, voice) in {
        "Sonnet + local voice":     ("anthropic", "claude-sonnet-4-6", False),
        "Sonnet + OpenAI voice":    ("anthropic", "claude-sonnet-4-6", True),
        "Opus + OpenAI voice":      ("anthropic", "claude-opus-4-6", True),
        "GPT-4o-mini + OpenAI voice": ("openai", "gpt-4o-mini", True),
        "GPT-4o + OpenAI voice":    ("openai", "gpt-4o", True),
        "GPT-5-mini + OpenAI voice":("openai", "gpt-5-mini", True),
        "Llama-70B (Groq) + local": ("inference", "Llama-70B (Groq)", False),
        "Llama-70B (Deepinfra)":    ("inference", "Llama-70B (Deepinfra)", False),
        "Llama-8B (Groq) + local":  ("inference", "Llama-8B (Groq)", False),
    }.items():
        ps = api_session_cost(prov, model) + (voice_cost_api() if voice else 0) + image_cost()
        print(f"{label:<35} ${ps:>8.4f} ${ps*10*30:>11.2f} ${ps*100*30:>12.2f}")

    # Cloud GPU
    print("\n── Cloud GPU Monthly (1 GPU, on-demand) ──")
    print(f"{'GPU':<20} {'On-Demand':>12} {'1yr':>12} {'3yr':>12} {'Spot':>12} {'Cap (sess/d)':>14}")
    print("-" * 85)
    for name, gpu in CLOUD_GPU.items():
        od = gpu["on_demand"]["monthly"]
        y1 = gpu.get("1yr_reserved", {}).get("monthly", 0)
        y3 = gpu.get("3yr_reserved", {}).get("monthly", 0)
        sp = gpu.get("spot", {}).get("monthly", 0)
        print(f"{name:<20} ${od:>10,.0f} ${y1:>10,.0f} ${y3:>10,.0f} ${sp:>10,.0f} {gpu['capacity_sessions_day']:>12}")

    # Own hardware
    print("\n── Own Hardware Monthly (amortized over 3yr + colo) ──")
    print(f"{'Hardware':<25} {'Purchase':>10} {'Monthly':>10} {'Cap (sess/d)':>14}")
    print("-" * 62)
    for name, hw in OWN_HARDWARE.items():
        m = own_hw_monthly(1, name, "colocation")[0] if isinstance(own_hw_monthly(1, name), np.ndarray) else own_hw_monthly(1, name, "colocation")
        print(f"{name:<25} ${hw['purchase']:>8,} ${m:>8,.0f} {hw['capacity_sessions_day']:>12}")

    # Breakevens
    print("\n── Key Breakeven Points ──")
    sonnet_ps = api_session_cost("anthropic", "claude-sonnet-4-6") + image_cost()
    groq_ps = api_session_cost("inference", "Llama-70B (Groq)") + image_cost()

    for hw_label, hw_key, hosting in [
        ("RTX 4090 (home)", "RTX 4090 24GB", "home"),
        ("RTX 4090 (colo)", "RTX 4090 24GB", "colocation"),
        ("RTX 5090 (colo)", "RTX 5090 32GB", "colocation"),
    ]:
        hw_m = own_hw_monthly(np.array([1]), hw_key, hosting)[0]
        be_vs_sonnet = hw_m / (sonnet_ps * 30)
        be_vs_groq = hw_m / (groq_ps * 30)
        print(f"  {hw_label}: breaks even vs Sonnet API at {be_vs_sonnet:.0f} sess/day, vs Groq at {be_vs_groq:.0f} sess/day")

    for gpu_label, gpu_key, commit in [
        ("Cloud L4 (on-demand)", "L4 24GB", "on_demand"),
        ("Cloud A100 (on-demand)", "A100 80GB", "on_demand"),
        ("Cloud H100 (1yr)", "H100 80GB", "1yr_reserved"),
    ]:
        gpu_m = cloud_monthly_stepped(np.array([1]), gpu_key, commit)[0]
        be_vs_sonnet = gpu_m / (sonnet_ps * 30)
        be_vs_groq = gpu_m / (groq_ps * 30)
        print(f"  {gpu_label}: breaks even vs Sonnet API at {be_vs_sonnet:.0f} sess/day, vs Groq at {be_vs_groq:.0f} sess/day")

    print("\n" + "=" * 90)


if __name__ == "__main__":
    print("Generating cost analysis plots...\n")
    plot_session_breakdown()
    plot_three_way_comparison()
    plot_breakeven_table()
    plot_cloud_tiers()
    plot_own_hardware_tco()
    plot_low_volume()
    plot_per_session_at_scale()
    print_summary()
    print(f"\nAll plots saved to {OUT}/")
