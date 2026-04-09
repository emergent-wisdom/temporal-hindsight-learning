# Temporal Hindsight Learning

**Blindness as Teacher, Hindsight as Curriculum**

[![Paper](https://img.shields.io/badge/Paper-PDF-red)](temporal-hindsight-learning.pdf)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.19462833.svg)](https://doi.org/10.5281/zenodo.19462833)
[![License](https://img.shields.io/badge/License-MIT-green)]()
[![Model](https://img.shields.io/badge/Model-HuggingFace-yellow)](https://huggingface.co/emergent-wisdom/thl-llama-3.3-70b-lora)

A framework that converts raw historical logs into high-quality reasoning supervision. A Teacher model with hindsight generates "ideal prediction" traces, which are distilled into a Student model constrained to past data. The core insight: **hindsight generates perfect logic puzzles** — the Teacher knows the answer and can identify which reasoning chains were valid.

## Key Result

A 70B model fine-tuned on **505 reasoning traces** achieves prediction accuracy competitive with a ~1T frontier model on 15 genuinely unseen 2025 events:

| Model | Reasoning (1–5) | Accuracy (1–7) |
|-------|:---:|:---:|
| Base Llama 3.3 70B | 3.21 | 4.72 |
| **THL Student (Ours)** | **3.87** | **5.07** |
| Gemini 3 Flash (~1.2T) | 4.36 | 5.01 |

- **+20% reasoning improvement** over base model (p < 0.001)
- **No significant accuracy difference** from Gemini (p = 0.97) despite 17× fewer parameters
- THL wins on 7/15 events, ties 2, loses 6

## How It Works

```
Teacher (Hindsight Oracle) ──→ Generates ideal reasoning traces for 2024 events
         │
         ▼
Student (Blind, Dec 2023) ──→ Trained via SFT to reproduce Teacher's reasoning
         │
         ▼
2025 Frontier Test ──→ Predict genuinely unseen events using learned patterns
         │
         ▼
Auditor (Claude Opus 4.6) ──→ Scores reasoning quality + prediction accuracy
```

The Student learns *how things happen* (causal mechanisms) rather than *what happened* (facts), enabling transfer from 2024 patterns to 2025 predictions.

## Repository Structure

```
temporal-hindsight-learning.tex    # Paper source (22 pages)
temporal-hindsight-learning.pdf    # Compiled paper
references.bib                     # Bibliography

experiment/
├── data/
│   ├── train_sft.jsonl            # 505 training traces (106 events × 5 angles)
│   ├── eval_sft.jsonl             # Held-out evaluation split
│   ├── test_2025_exam.json        # 75 test prompts + ground truth
│   └── events_2024.json           # 106 training event metadata
├── output/
│   ├── predictions_{2025,baseline,gemini}.jsonl  # Raw model outputs
│   ├── scores_{thl,baseline,gemini}.jsonl        # Auditor scores
│   ├── summary_{thl,baseline,gemini}.json        # Aggregate results
│   └── figures/                                   # Publication figures
├── generate_data.py               # Teacher-Oracle data generation pipeline
├── generate_test_data.py          # Test data generation (same pipeline)
├── run_inference_2025.py          # THL Student inference (Vertex AI)
├── run_inference_baseline.py      # Base Llama inference (Vertex AI)
├── submit_inference_job.py        # Vertex AI job submission
├── submit_vertex_job.py           # Training job submission
├── task.py                        # SFT training worker (Unsloth/LoRA)
├── visualize_results.py           # Generate all figures
├── auditor_prompt_template.txt    # Anchored Ordinal Rubric
└── requirements.txt               # Python dependencies
```

## Reproducing the Results

### Prerequisites
- Google Cloud account with Vertex AI access (for GPU inference)
- Gemini API key (for data generation)
- Hugging Face token (for Llama 3.3 70B access)

### Steps

```bash
# 1. Generate training data (requires Gemini API key)
cd experiment
export GEMINI_API_KEY="your-key"
python generate_data.py

# 2. Train the model (requires Vertex AI GPU)
export GCP_PROJECT_ID="your-project"
export HF_TOKEN="your-token"
python submit_vertex_job.py

# 3. Run inference on 2025 test events
python submit_inference_job.py --both

# 4. Score predictions (via Claude Code agents using auditor_prompt_template.txt)

# 5. Generate figures
python visualize_results.py
```

The training data (`train_sft.jsonl`) and all model outputs (`predictions_*.jsonl`, `scores_*.jsonl`) are included in the repository for direct analysis without re-running inference.

## Citation

```bibtex
@misc{westerberg2026temporal,
  title        = {Temporal Hindsight Learning: Blindness as Teacher, Hindsight as Curriculum},
  author       = {Westerberg, Henrik},
  year         = {2026},
  month        = apr,
  publisher    = {Zenodo},
  doi          = {10.5281/zenodo.19462833},
  url          = {https://doi.org/10.5281/zenodo.19462833}
}
```

See [`CITATION.cff`](CITATION.cff) for the machine-readable version (GitHub
renders a "Cite this repository" button from it).

## License

MIT
