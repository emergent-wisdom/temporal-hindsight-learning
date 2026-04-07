# task.py
# THE WORKER SCRIPT
# -----------------
# This runs INSIDE the cloud container.
# Adapted for Temporal Hindsight Learning data format.

import os
import json
import torch
from unsloth import FastLanguageModel
from trl import SFTTrainer, SFTConfig
from datasets import load_dataset
from google.cloud import storage

# ==========================================
# 1. SETUP & CREDENTIALS
# ==========================================
print("=" * 60)
print("THL Worker - Temporal Hindsight Learning")
print("=" * 60)

# Login to HF Programmatically
from huggingface_hub import login
login(token=os.environ["HF_TOKEN"])

# ==========================================
# 2. PHASE 1: SFT (Hindsight Distillation)
# ==========================================
print("\n[Phase 1] Loading Llama 3.3 70B...")

max_seq_length = 4096  # Our traces are ~5k chars, this is sufficient
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name="unsloth/Llama-3.3-70B-Instruct-bnb-4bit",
    max_seq_length=max_seq_length,
    dtype=None,
    load_in_4bit=True,
)

print("[Phase 1] Adding LoRA adapters...")
# LoRA config from official Unsloth examples
model = FastLanguageModel.get_peft_model(
    model,
    r=16,
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                    "gate_proj", "up_proj", "down_proj"],
    lora_alpha=16,              # Official: alpha = r
    lora_dropout=0,             # Official: 0 dropout
    bias="none",
    use_gradient_checkpointing="unsloth",
    random_state=3407,          # Official seed
    max_seq_length=max_seq_length,
)

# Count params
trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
total = sum(p.numel() for p in model.parameters())
print(f"  Trainable: {trainable:,} / {total:,} ({100*trainable/total:.2f}%)")

# Load training data (Vertex copies local files to container)
print("\n[Phase 1] Loading training data...")
dataset = load_dataset("json", data_files="train_sft.jsonl", split="train")
print(f"  Loaded {len(dataset)} training examples")

print("\n[Phase 1] Starting SFT training...")
# Use SFTConfig (current recommended pattern, TrainingArguments is deprecated)
# See: https://huggingface.co/docs/trl/en/sft_trainer
trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=dataset,
    args=SFTConfig(
        # SFT-specific args
        dataset_text_field="text",
        max_seq_length=max_seq_length,
        packing=False,
        # Training args
        per_device_train_batch_size=2,
        gradient_accumulation_steps=4,
        warmup_steps=10,
        max_steps=150,  # ~2.4 epochs over 505 examples
        learning_rate=2e-4,
        fp16=not torch.cuda.is_bf16_supported(),
        bf16=torch.cuda.is_bf16_supported(),
        logging_steps=5,
        save_steps=50,
        output_dir="outputs",
        optim="adamw_8bit",
        seed=3407,
        report_to="none",
    ),
)

trainer_stats = trainer.train()
print(f"\n[Phase 1] SFT Complete!")
print(f"  Steps: {trainer_stats.global_step}")
print(f"  Loss: {trainer_stats.training_loss:.4f}")

# ==========================================
# 3. PHASE 2: EVALUATION (Optional RFT)
# ==========================================
print("\n[Phase 2] Running evaluation on held-out set...")

FastLanguageModel.for_inference(model)

# Load eval data
with open("eval_sft.jsonl", "r") as f:
    eval_examples = [json.loads(line) for line in f]

print(f"  Loaded {len(eval_examples)} eval examples")

# Test on 5 samples
results = []
for i, ex in enumerate(eval_examples[:5]):
    # Extract prompt (up to assistant header)
    full_text = ex['text']
    assistant_marker = "<|start_header_id|>assistant<|end_header_id|>"
    prompt_end = full_text.find(assistant_marker) + len(assistant_marker)
    prompt = full_text[:prompt_end] + "\n\n"

    # Generate
    inputs = tokenizer([prompt], return_tensors="pt").to("cuda")
    outputs = model.generate(
        **inputs,
        max_new_tokens=512,
        temperature=0.7,
        do_sample=True,
        pad_token_id=tokenizer.eos_token_id,
    )

    response = tokenizer.decode(outputs[0], skip_special_tokens=True)
    generated = response[len(tokenizer.decode(inputs.input_ids[0], skip_special_tokens=True)):]

    results.append({
        "event_id": ex['event_id'],
        "angle": ex['angle'],
        "generated": generated[:500]  # Truncate for logging
    })
    print(f"  [{i+1}/5] Event {ex['event_id']} ({ex['angle'][:20]}...) - Generated {len(generated)} chars")

# Save eval results
with open("eval_results.json", "w") as f:
    json.dump(results, f, indent=2)

# ==========================================
# 4. SAVE & UPLOAD TO GCS
# ==========================================
print("\n[Phase 3] Saving model...")
model.save_pretrained("lora_model")
tokenizer.save_pretrained("lora_model")

print("[Phase 3] Uploading to GCS...")
bucket_name = os.environ["GCS_OUTPUT_BUCKET"]
storage_client = storage.Client()
bucket = storage_client.bucket(bucket_name)

def upload_folder(local_path, gcs_path):
    for root, _, files in os.walk(local_path):
        for file in files:
            local_file = os.path.join(root, file)
            remote_path = os.path.join(gcs_path, os.path.relpath(local_file, local_path))
            blob = bucket.blob(remote_path)
            blob.upload_from_filename(local_file)
            print(f"  Uploaded: {remote_path}")

upload_folder("lora_model", "model_output/lora_adapter")
upload_folder("outputs", "model_output/checkpoints")

# Upload eval results
blob = bucket.blob("model_output/eval_results.json")
blob.upload_from_filename("eval_results.json")
print("  Uploaded: eval_results.json")

print("\n" + "=" * 60)
print("THL TRAINING COMPLETE")
print(f"Model saved to: gs://{bucket_name}/model_output/")
print("=" * 60)
