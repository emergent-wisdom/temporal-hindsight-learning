#!/usr/bin/env python3
"""
THL 2025 Frontier Test — Results Visualization
===============================================
Generates publication-quality charts comparing Gemini, Base Llama, and THL Student.

Usage:
    python visualize_results.py

Output:
    experiment/output/figures/
"""

import json
import numpy as np
import matplotlib.pyplot as plt
import matplotlib
from pathlib import Path
from collections import defaultdict

matplotlib.rcParams['font.family'] = 'serif'
matplotlib.rcParams['font.size'] = 11

OUTPUT_DIR = Path(__file__).parent / "output"
FIGURES_DIR = OUTPUT_DIR / "figures"
FIGURES_DIR.mkdir(parents=True, exist_ok=True)

# =============================================================================
# LOAD DATA
# =============================================================================

def load_scores(filename):
    scores = []
    path = OUTPUT_DIR / filename
    if not path.exists():
        return None
    with open(path) as f:
        for line in f:
            if line.strip():
                scores.append(json.loads(line))
    return scores

gemini = load_scores("scores_gemini.jsonl")
baseline = load_scores("scores_baseline.jsonl")
thl = load_scores("scores_thl.jsonl")  # may not exist yet

models = {}
if gemini:
    models["Gemini 3 Flash"] = gemini
if baseline:
    models["Base Llama 3.3"] = baseline
if thl:
    models["THL Student"] = thl

COLORS = {
    "Gemini 3 Flash": "#4285F4",
    "Base Llama 3.3": "#EA4335",
    "THL Student": "#34A853",
}

print(f"Loaded: {', '.join(f'{k} ({len(v)})' for k, v in models.items())}")

# =============================================================================
# 1. HEADLINE BAR CHART: Average Reasoning & Accuracy by Model
# =============================================================================

def plot_headline_bars():
    fig, axes = plt.subplots(1, 2, figsize=(10, 5))

    names = list(models.keys())

    # Reasoning
    ax = axes[0]
    means = [np.mean([s['reasoning'] for s in models[m]]) for m in names]
    stds = [np.std([s['reasoning'] for s in models[m]]) for m in names]
    bars = ax.bar(names, means, yerr=stds, color=[COLORS[m] for m in names],
                  capsize=5, edgecolor='black', linewidth=0.5)
    ax.set_ylabel("Score")
    ax.set_title("Reasoning Quality (1–5)")
    ax.set_ylim(0, 6.0)
    for bar, mean, std in zip(bars, means, stds):
        ax.text(bar.get_x() + bar.get_width()/2, mean + std + 0.15,
                f'{mean:.2f}', ha='center', va='bottom', fontweight='bold', fontsize=12)

    # Accuracy
    ax = axes[1]
    means = [np.mean([s['accuracy'] for s in models[m]]) for m in names]
    stds = [np.std([s['accuracy'] for s in models[m]]) for m in names]
    bars = ax.bar(names, means, yerr=stds, color=[COLORS[m] for m in names],
                  capsize=5, edgecolor='black', linewidth=0.5)
    ax.set_ylabel("Score")
    ax.set_title("Prediction Accuracy (1–7)")
    ax.set_ylim(0, 8.2)
    for bar, mean, std in zip(bars, means, stds):
        ax.text(bar.get_x() + bar.get_width()/2, mean + std + 0.15,
                f'{mean:.2f}', ha='center', va='bottom', fontweight='bold', fontsize=12)

    plt.tight_layout()
    path = FIGURES_DIR / "headline_bars.png"
    plt.savefig(path, dpi=200, bbox_inches='tight')
    plt.close()
    print(f"  Saved: {path}")


# =============================================================================
# 2. DISTRIBUTION SHIFT: Accuracy histogram per model
# =============================================================================

def plot_accuracy_distribution():
    fig, ax = plt.subplots(figsize=(10, 5))

    names = list(models.keys())
    x = np.arange(1, 8)
    width = 0.25
    offsets = np.linspace(-width, width, len(names))

    for i, name in enumerate(names):
        counts = [sum(1 for s in models[name] if s['accuracy'] == level) for level in range(1, 8)]
        ax.bar(x + offsets[i], counts, width, label=name, color=COLORS[name],
               edgecolor='black', linewidth=0.5)

    ax.set_xlabel("Accuracy Score")
    ax.set_ylabel("Number of Predictions")
    ax.set_title("Prediction Accuracy Distribution by Model")
    ax.set_xticks(x)
    ax.set_xticklabels([
        "1\nWrong", "2\nMostly\nwrong", "3\nMixed",
        "4\nDirection\ncorrect", "5\nOutcome\ncorrect",
        "6\nOutcome+\nmechanism", "7\nNailed\nit"
    ], fontsize=8)
    ax.legend()

    plt.tight_layout()
    path = FIGURES_DIR / "accuracy_distribution.png"
    plt.savefig(path, dpi=200, bbox_inches='tight')
    plt.close()
    print(f"  Saved: {path}")


# =============================================================================
# 3. REASONING vs ACCURACY SCATTER
# =============================================================================

def plot_reasoning_vs_accuracy():
    fig, ax = plt.subplots(figsize=(8, 7))

    for name, scores in models.items():
        r = [s['reasoning'] for s in scores]
        a = [s['accuracy'] for s in scores]
        # Jitter to avoid overlap
        jitter_r = np.array(r) + np.random.normal(0, 0.08, len(r))
        jitter_a = np.array(a) + np.random.normal(0, 0.08, len(a))
        ax.scatter(jitter_r, jitter_a, alpha=0.5, s=40, label=name,
                   color=COLORS[name], edgecolors='black', linewidth=0.3)

    # Quadrant lines
    ax.axhline(y=4, color='gray', linestyle='--', alpha=0.3)
    ax.axvline(x=3, color='gray', linestyle='--', alpha=0.3)

    # Quadrant labels
    ax.text(1.5, 6.5, "Lucky\nguesses", ha='center', va='center',
            fontsize=9, color='gray', style='italic')
    ax.text(4.5, 6.5, "Right for the\nright reasons", ha='center', va='center',
            fontsize=9, color='green', fontweight='bold')
    ax.text(1.5, 1.5, "Failed", ha='center', va='center',
            fontsize=9, color='gray', style='italic')
    ax.text(4.5, 1.5, "Sound reasoning,\nwrong prediction", ha='center', va='center',
            fontsize=9, color='gray', style='italic')

    ax.set_xlabel("Reasoning Quality (1–5)")
    ax.set_ylabel("Prediction Accuracy (1–7)")
    ax.set_title("Reasoning vs. Accuracy: Are Models Right for the Right Reasons?")
    ax.set_xlim(0.5, 5.5)
    ax.set_ylim(0.5, 7.5)
    ax.legend(loc='lower right')

    plt.tight_layout()
    path = FIGURES_DIR / "reasoning_vs_accuracy.png"
    plt.savefig(path, dpi=200, bbox_inches='tight')
    plt.close()
    print(f"  Saved: {path}")


# =============================================================================
# 4. HEATMAP: Event x Model accuracy
# =============================================================================

def plot_event_heatmap():
    names = list(models.keys())

    # Get events in order
    events = []
    seen = set()
    for s in gemini or baseline or thl:
        if s['event_name'] not in seen:
            events.append(s['event_name'])
            seen.add(s['event_name'])

    # Build matrix
    matrix = np.zeros((len(events), len(names)))
    for j, name in enumerate(names):
        by_event = defaultdict(list)
        for s in models[name]:
            by_event[s['event_name']].append(s['accuracy'])
        for i, event in enumerate(events):
            if event in by_event:
                matrix[i, j] = np.mean(by_event[event])

    # Sort by Gemini accuracy (descending)
    if "Gemini 3 Flash" in names:
        sort_idx = np.argsort(-matrix[:, names.index("Gemini 3 Flash")])
    else:
        sort_idx = np.argsort(-matrix[:, 0])
    matrix = matrix[sort_idx]
    events = [events[i] for i in sort_idx]

    # Shorten event names
    short_names = []
    for e in events:
        if len(e) > 35:
            e = e[:32] + "..."
        short_names.append(e)

    fig, ax = plt.subplots(figsize=(8, 10))
    im = ax.imshow(matrix, cmap='RdYlGn', aspect='auto', vmin=1, vmax=7)

    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, fontsize=10)
    ax.set_yticks(range(len(short_names)))
    ax.set_yticklabels(short_names, fontsize=9)

    # Add text annotations
    for i in range(len(events)):
        for j in range(len(names)):
            val = matrix[i, j]
            color = 'white' if val < 3 or val > 5.5 else 'black'
            ax.text(j, i, f'{val:.1f}', ha='center', va='center',
                    fontsize=9, color=color, fontweight='bold')

    ax.set_title("Average Prediction Accuracy by Event", fontsize=13)
    cbar = plt.colorbar(im, ax=ax, shrink=0.6)
    cbar.set_label("Accuracy (1–7)")

    plt.tight_layout()
    path = FIGURES_DIR / "event_heatmap.png"
    plt.savefig(path, dpi=200, bbox_inches='tight')
    plt.close()
    print(f"  Saved: {path}")


# =============================================================================
# 5. ANGLE COMPARISON: Radar/bar chart by Pentagon angle
# =============================================================================

def plot_angle_comparison():
    names = list(models.keys())
    angles = ["Structural/Mechanism", "Economic/Incentives", "Political/Social",
              "Base Rates/Precedents", "Temporal/Pacing"]

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))

    x = np.arange(len(angles))
    width = 0.25
    offsets = np.linspace(-width, width, len(names))

    for metric_idx, (metric, title, ymax) in enumerate([
        ('reasoning', 'Reasoning by Angle', 5.5),
        ('accuracy', 'Accuracy by Angle', 7.5)
    ]):
        ax = axes[metric_idx]
        for i, name in enumerate(names):
            by_angle = defaultdict(list)
            for s in models[name]:
                by_angle[s['angle']].append(s[metric])
            means = [np.mean(by_angle[a]) if a in by_angle else 0 for a in angles]
            ax.bar(x + offsets[i], means, width, label=name, color=COLORS[name],
                   edgecolor='black', linewidth=0.5)

        ax.set_ylabel("Score")
        ax.set_title(title)
        ax.set_xticks(x)
        ax.set_xticklabels([a.split('/')[0] for a in angles], fontsize=9)
        ax.set_ylim(0, ymax)
        ax.legend(fontsize=8)

    plt.tight_layout()
    path = FIGURES_DIR / "angle_comparison.png"
    plt.savefig(path, dpi=200, bbox_inches='tight')
    plt.close()
    print(f"  Saved: {path}")


# =============================================================================
# 6. SUMMARY TABLE (text output)
# =============================================================================

def print_summary_table():
    print(f"\n{'='*60}")
    print("SUMMARY TABLE FOR PAPER")
    print(f"{'='*60}")
    print(f"{'Model':<20} {'N':>4} {'Refuse':>7} {'Leak':>5} {'Reason':>8} {'Accuracy':>9}")
    print("-"*60)
    for name, scores in models.items():
        n = len(scores)
        refuse = sum(1 for s in scores if s.get('is_refusal'))
        leak = sum(1 for s in scores if not s.get('no_leakage', True))
        r = np.mean([s['reasoning'] for s in scores])
        a = np.mean([s['accuracy'] for s in scores])
        print(f"{name:<20} {n:>4} {refuse:>7} {leak:>5} {r:>7.2f}/5 {a:>8.2f}/7")


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    print("Generating figures...")
    plot_headline_bars()
    plot_accuracy_distribution()
    plot_reasoning_vs_accuracy()
    plot_event_heatmap()
    plot_angle_comparison()
    print_summary_table()
    print(f"\nAll figures saved to {FIGURES_DIR}")
