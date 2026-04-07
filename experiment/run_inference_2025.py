#!/usr/bin/env python3
"""
THL 2025 Frontier Test - Inference Script
==========================================
Loads the fine-tuned Llama 3.3 70B + LoRA adapter and runs inference
on 2025 test events using the same trace-based prompts as the Gemini baseline.

Uses the exact system prompt and conversational user prompts from the
training data generation pipeline (test_2025_sft.progress.json).

Usage (requires GPU):
    python run_inference_2025.py

Output:
    experiment/output/predictions_2025.jsonl
"""

import os
import json
from pathlib import Path
from datetime import datetime

# =============================================================================
# CONFIGURATION
# =============================================================================

# Paths
ADAPTER_PATH = Path(__file__).parent / "model_output" / "lora_adapter"
TRACES_PATH = Path(__file__).parent / "data" / "test_2025_sft.progress.json"
EXAM_PATH = Path(__file__).parent / "data" / "test_2025_exam.json"
OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_FILE = OUTPUT_DIR / "predictions_2025.jsonl"

# Model settings
BASE_MODEL = "unsloth/Llama-3.3-70B-Instruct-bnb-4bit"
MAX_SEQ_LENGTH = 4096
MAX_NEW_TOKENS = 1024

# System prompt — MUST match training data exactly
SYSTEM_PROMPT = (
    "You are a forecasting assistant with knowledge through December 2023. "
    "When given context about events in 2024, analyze the information carefully:\n"
    "1. Identify what the context tells you\n"
    "2. Connect it to patterns you know from before 2024\n"
    "3. Reason through the causal chain step by step\n"
    "4. Give a calibrated prediction with probability estimates\n"
    "Be honest about uncertainty. Show your reasoning clearly."
)


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


# Angle name to index mapping
ANGLE_MAP = {
    "Structural/Mechanism": 0, "Economic/Incentives": 1,
    "Political/Social": 2, "Base Rates/Precedents": 3, "Temporal/Pacing": 4
}


def main():
    print("=" * 60)
    print("THL 2025 Frontier Test - Inference (Trace-Based Prompts)")
    print("=" * 60)

    # Check for adapter
    if not ADAPTER_PATH.exists():
        print(f"ERROR: Adapter not found at {ADAPTER_PATH}")
        print("Please ensure the LoRA adapter is downloaded from GCS.")
        return

    # Load test traces and ground truth
    print(f"\n[1/4] Loading test data")
    traces = load_traces()
    ground_truth = load_ground_truth()

    # Load model using transformers + bitsandbytes (same as baseline script)
    # Avoids unsloth's fast inference kernels which have a RoPE shape mismatch bug
    print(f"\n[2/4] Loading model and adapter (transformers + peft)...")
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from peft import PeftModel
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

    # Load LoRA adapter
    print(f"  Loading adapter from {ADAPTER_PATH}")
    model = PeftModel.from_pretrained(model, str(ADAPTER_PATH))
    model.eval()
    print("  Model ready for inference (base + LoRA)")

    # Create output directory
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Run inference
    print(f"\n[3/4] Running inference on {len(traces)} traces")
    results = []

    for i, trace in enumerate(traces):
        event_name = trace["event"]
        angle = trace["angle"]
        angle_idx = ANGLE_MAP.get(angle, trace.get("variation", -1))

        # Extract the user prompt from the trace (same as Gemini received)
        user_prompt = trace["messages"][0]["content"]
        reference = trace["messages"][1]["content"]  # Teacher's reference response

        print(f"  [{i+1}/{len(traces)}] {event_name} - {angle}...", end=" ", flush=True)

        # Build messages with the correct training system prompt
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ]

        # Tokenize
        inputs = tokenizer.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            return_tensors="pt"
        ).to("cuda")

        # Generate
        outputs = model.generate(
            input_ids=inputs,
            max_new_tokens=MAX_NEW_TOKENS,
            temperature=0.7,
            do_sample=True,
            pad_token_id=tokenizer.eos_token_id,
        )

        # Decode
        response = tokenizer.decode(outputs[0], skip_special_tokens=True)
        # Extract just the assistant's response
        generated = response.split("assistant")[-1].strip()

        # Look up ground truth
        gt_key = (trace["id"], angle_idx)
        gt = ground_truth.get(gt_key, {})

        result = {
            "event_id": trace["id"],
            "event_name": event_name,
            "angle": angle,
            "angle_idx": angle_idx,
            "model": "thl-student-llama-3.3-70b",
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
    print("INFERENCE COMPLETE")
    print(f"Generated {len(results)} predictions")
    print(f"Output: {OUTPUT_FILE}")
    print("=" * 60)


if __name__ == "__main__":
    main()
