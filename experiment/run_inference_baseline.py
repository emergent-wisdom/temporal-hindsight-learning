#!/usr/bin/env python3
"""
THL 2025 Frontier Test - Base Llama Baseline (No LoRA)
======================================================
Runs the same trace-based prompts (identical to Gemini and THL Student)
through the raw Llama 3.3 70B WITHOUT the THL LoRA adapter.
This is the control group.

Usage (requires GPU - run via Vertex AI):
    python run_inference_baseline.py

Output:
    experiment/output/predictions_baseline.jsonl
"""

import json
from pathlib import Path
from datetime import datetime

# =============================================================================
# CONFIGURATION
# =============================================================================

TRACES_PATH = Path(__file__).parent / "data" / "test_2025_sft.progress.json"
EXAM_PATH = Path(__file__).parent / "data" / "test_2025_exam.json"
OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_FILE = OUTPUT_DIR / "predictions_baseline.jsonl"

BASE_MODEL = "meta-llama/Llama-3.3-70B-Instruct"
MAX_SEQ_LENGTH = 4096
MAX_NEW_TOKENS = 1024

# System prompt — MUST match training data exactly (same as THL Student)
SYSTEM_PROMPT = (
    "You are a forecasting assistant with knowledge through December 2023. "
    "When given context about events in 2024, analyze the information carefully:\n"
    "1. Identify what the context tells you\n"
    "2. Connect it to patterns you know from before 2024\n"
    "3. Reason through the causal chain step by step\n"
    "4. Give a calibrated prediction with probability estimates\n"
    "Be honest about uncertainty. Show your reasoning clearly."
)

# Angle name to index mapping
ANGLE_MAP = {
    "Structural/Mechanism": 0, "Economic/Incentives": 1,
    "Political/Social": 2, "Base Rates/Precedents": 3, "Temporal/Pacing": 4
}


def load_traces():
    """Load the 75 trace-based prompts from the SFT progress file."""
    with open(TRACES_PATH) as f:
        data = json.load(f)
    traces = data["traces"]
    print(f"  Loaded {len(traces)} traces from {TRACES_PATH.name}")
    return traces


def load_ground_truth():
    """Load ground truth from exam data for output metadata."""
    if not EXAM_PATH.exists():
        return {}
    with open(EXAM_PATH) as f:
        exam = json.load(f)
    gt = {}
    for q in exam:
        key = (q["event_id"], q["angle_idx"])
        gt[key] = q.get("ground_truth", {})
    return gt


def main():
    print("=" * 60)
    print("THL 2025 Frontier Test - Base Llama Baseline (No LoRA)")
    print("=" * 60)

    # Load test traces and ground truth
    print(f"\n[1/4] Loading test data")
    traces = load_traces()
    ground_truth = load_ground_truth()

    # Load model using transformers + bitsandbytes (no unsloth)
    print(f"\n[2/4] Loading base model (transformers + bitsandbytes)...")
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    import torch

    quantization_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
    )

    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
    model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL,
        quantization_config=quantization_config,
        device_map="auto",
        torch_dtype=torch.bfloat16,
        attn_implementation="sdpa",
    )
    model.eval()
    print("  Base model ready (no THL fine-tuning)")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Run inference
    print(f"\n[3/4] Running inference on {len(traces)} traces")
    results = []

    for i, trace in enumerate(traces):
        event_name = trace["event"]
        angle = trace["angle"]
        angle_idx = ANGLE_MAP.get(angle, trace.get("variation", -1))

        # Same prompt as Gemini and THL Student received
        user_prompt = trace["messages"][0]["content"]
        reference = trace["messages"][1]["content"]

        print(f"  [{i+1}/{len(traces)}] {event_name} - {angle}...", end=" ", flush=True)

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ]

        inputs = tokenizer.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            return_tensors="pt"
        ).to("cuda")

        outputs = model.generate(
            input_ids=inputs,
            max_new_tokens=MAX_NEW_TOKENS,
            temperature=1.0,
            do_sample=False,
            pad_token_id=tokenizer.eos_token_id,
            use_cache=True,
        )

        response = tokenizer.decode(outputs[0], skip_special_tokens=True)
        generated = response.split("assistant")[-1].strip()

        gt_key = (trace["id"], angle_idx)
        gt = ground_truth.get(gt_key, {})

        result = {
            "event_id": trace["id"],
            "event_name": event_name,
            "angle": angle,
            "angle_idx": angle_idx,
            "model": "llama-3.3-70b-base",
            "prompt": user_prompt,
            "generated": generated,
            "reference": reference,
            "ground_truth": gt,
            "timestamp": datetime.now().isoformat()
        }
        results.append(result)
        print(f"done ({len(generated)} chars)")

    # Save results
    print(f"\n[4/4] Saving results to {OUTPUT_FILE}")
    with open(OUTPUT_FILE, 'w') as f:
        for r in results:
            f.write(json.dumps(r) + "\n")

    print(f"\n" + "=" * 60)
    print(f"BASELINE COMPLETE - {len(results)} predictions")
    print(f"Output: {OUTPUT_FILE}")
    print("=" * 60)


if __name__ == "__main__":
    main()
