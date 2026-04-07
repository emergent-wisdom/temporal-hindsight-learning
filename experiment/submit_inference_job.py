"""
Submit THL 2025 Inference Job to Vertex AI
==========================================
This script runs LOCALLY to submit an inference job to Google Cloud.
The actual inference happens on a cloud GPU.

Uses trace-based prompts (same questions Gemini answered) for fair comparison.

Usage:
    python submit_inference_job.py              # THL Student (with LoRA)
    python submit_inference_job.py --baseline   # Base Llama (no LoRA)
    python submit_inference_job.py --both       # Submit both jobs

Requirements:
    pip install google-cloud-aiplatform google-cloud-storage
    gcloud auth application-default login
"""

import os
import sys
from pathlib import Path
from datetime import datetime

# Load .env file if available
try:
    from dotenv import load_dotenv
    local_env = Path(__file__).parent / ".env"
    if local_env.exists():
        load_dotenv(local_env)
except ImportError:
    pass

# Check for required packages
try:
    from google.cloud import aiplatform, storage
except ImportError:
    print("Missing required packages. Install with:")
    print("  pip install google-cloud-aiplatform google-cloud-storage python-dotenv")
    sys.exit(1)

# =============================================================================
# CONFIGURATION
# =============================================================================

# Google Cloud settings
PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "your-project-id")
BUCKET_NAME = os.environ.get("GCS_BUCKET", "thl-training-henrik")

# Region must support A100 80GB (a2-ultragpu)
REGION = os.environ.get("GCP_REGION", "us-central1")

# GPU Configuration - same as training
MACHINE_TYPE = "a2-ultragpu-1g"  # 1x A100 80GB
ACCELERATOR_TYPE = "NVIDIA_A100_80GB"
ACCELERATOR_COUNT = 1

# Container image
CONTAINER_IMAGE = "us-docker.pkg.dev/vertex-ai/training/pytorch-gpu.2-4.py310:latest"

# Local paths
EXPERIMENT_DIR = Path(__file__).parent
INFERENCE_SCRIPT_THL = EXPERIMENT_DIR / "run_inference_2025.py"
INFERENCE_SCRIPT_BASELINE = EXPERIMENT_DIR / "run_inference_baseline.py"

# Data files — trace-based prompts (same as Gemini received)
TRACES_DATA = EXPERIMENT_DIR / "data" / "test_2025_sft.progress.json"
EXAM_DATA = EXPERIMENT_DIR / "data" / "test_2025_exam.json"

# GCS paths for the trained adapter
ADAPTER_GCS_PATH = "model_output/lora_adapter"

# Parse mode from args
MODE = "thl"  # default
if "--baseline" in sys.argv:
    MODE = "baseline"
elif "--both" in sys.argv:
    MODE = "both"

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def upload_to_gcs(local_path: Path, bucket_name: str, gcs_path: str) -> str:
    """Upload a local file to GCS and return the gs:// URI."""
    client = storage.Client(project=PROJECT_ID)
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(gcs_path)
    blob.upload_from_filename(str(local_path))
    uri = f"gs://{bucket_name}/{gcs_path}"
    print(f"  Uploaded: {local_path.name} -> {uri}")
    return uri


def verify_files_exist(script_file: Path):
    """Check that all required files exist locally."""
    required = [script_file, TRACES_DATA]
    if EXAM_DATA.exists():
        required.append(EXAM_DATA)
    missing = [str(f) for f in required if not f.exists()]
    if missing:
        print("ERROR: Missing required files:")
        for m in missing:
            print(f"  - {m}")
        sys.exit(1)


def verify_adapter_exists():
    """Check that the LoRA adapter exists in GCS."""
    client = storage.Client(project=PROJECT_ID)
    bucket = client.bucket(BUCKET_NAME)
    blob = bucket.blob(f"{ADAPTER_GCS_PATH}/adapter_model.safetensors")
    if not blob.exists():
        print(f"ERROR: LoRA adapter not found at gs://{BUCKET_NAME}/{ADAPTER_GCS_PATH}/")
        print("Please ensure training completed and adapter was saved.")
        sys.exit(1)
    print(f"  Adapter found at gs://{BUCKET_NAME}/{ADAPTER_GCS_PATH}/")


def check_gcloud_auth():
    """Verify gcloud authentication is set up."""
    try:
        client = storage.Client(project=PROJECT_ID)
        list(client.list_buckets(max_results=1))
        return True
    except Exception as e:
        print(f"ERROR: Google Cloud authentication failed: {e}")
        print("\nPlease run:")
        print("  gcloud auth application-default login")
        print("  gcloud config set project YOUR_PROJECT_ID")
        return False


# =============================================================================
# MAIN
# =============================================================================

def submit_job(mode: str):
    """Submit a single inference job. mode is 'thl' or 'baseline'."""
    is_baseline = (mode == "baseline")
    job_label = "Baseline (No LoRA)" if is_baseline else "THL Student (LoRA)"
    job_name = f"thl-{'baseline' if is_baseline else 'inference'}-2025-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    script_file = INFERENCE_SCRIPT_BASELINE if is_baseline else INFERENCE_SCRIPT_THL
    script_name = script_file.name

    print(f"\n{'=' * 60}")
    print(f"Submitting: {job_label}")
    print(f"Job: {job_name}")
    print(f"{'=' * 60}")

    # Verify local files
    verify_files_exist(script_file)

    staging_prefix = f"staging/{job_name}"

    # Upload trace-based test data (same prompts Gemini answered)
    upload_to_gcs(TRACES_DATA, BUCKET_NAME, f"{staging_prefix}/test_2025_sft.progress.json")
    if EXAM_DATA.exists():
        upload_to_gcs(EXAM_DATA, BUCKET_NAME, f"{staging_prefix}/test_2025_exam.json")
    upload_to_gcs(script_file, BUCKET_NAME, f"{staging_prefix}/{script_name}")

    # Build the download commands
    download_data_cmd = f"""
bucket.blob('{staging_prefix}/test_2025_sft.progress.json').download_to_filename('data/test_2025_sft.progress.json')
try:
    bucket.blob('{staging_prefix}/test_2025_exam.json').download_to_filename('data/test_2025_exam.json')
except Exception:
    print('No exam data (ground truth will be empty)')
bucket.blob('{staging_prefix}/{script_name}').download_to_filename('{script_name}')
"""

    if not is_baseline:
        download_data_cmd += f"""
adapter_prefix = '{ADAPTER_GCS_PATH}/'
blobs = list(bucket.list_blobs(prefix=adapter_prefix))
for blob in blobs:
    if blob.name.endswith('/'):
        continue
    local_path = 'model_output/lora_adapter/' + blob.name.replace(adapter_prefix, '')
    print(f'Downloading {{blob.name}} -> {{local_path}}')
    blob.download_to_filename(local_path)
"""

    download_data_cmd += "\nprint('Downloaded all files')"

    worker_pool_specs = [
        {
            "machine_spec": {
                "machine_type": MACHINE_TYPE,
                "accelerator_type": ACCELERATOR_TYPE,
                "accelerator_count": ACCELERATOR_COUNT,
            },
            "replica_count": 1,
            "container_spec": {
                "image_uri": CONTAINER_IMAGE,
                "command": ["bash", "-c"],
                "args": [
                    f"""
                    set -ex

                    echo "=== {job_label} Job Started ==="
                    nvidia-smi

                    echo "Installing dependencies..."
                    pip install "accelerate>=0.26" "bitsandbytes==0.43.3" "transformers>=4.40,<5" "huggingface_hub>=0.20" "peft>=0.18"
                    pip install -q google-cloud-storage

                    echo "Creating directories..."
                    mkdir -p data model_output/lora_adapter output

                    echo "Downloading data and scripts from GCS..."
                    python -c "
from google.cloud import storage
client = storage.Client()
bucket = client.bucket('{BUCKET_NAME}')
{download_data_cmd}
"

                    echo "Logging into Hugging Face..."
                    python -c "import os; from huggingface_hub import login; login(token=os.environ['HF_TOKEN'])"

                    echo "Starting inference..."
                    python {script_name}

                    echo "Uploading results to GCS..."
                    python -c "
from google.cloud import storage
from pathlib import Path
client = storage.Client()
bucket = client.bucket('{BUCKET_NAME}')

output_dir = Path('output')
for f in output_dir.glob('*'):
    gcs_path = f'inference_output/{{f.name}}'
    print(f'Uploading {{f}} -> gs://{BUCKET_NAME}/{{gcs_path}}')
    bucket.blob(gcs_path).upload_from_filename(str(f))
"

                    echo "=== Inference Complete ==="
                    """
                ],
                "env": [
                    {"name": "HF_TOKEN", "value": os.environ.get("HF_TOKEN", "")},
                    {"name": "GCS_OUTPUT_BUCKET", "value": BUCKET_NAME},
                    {"name": "TRANSFORMERS_CACHE", "value": "/root/.cache/huggingface"},
                ],
            },
        }
    ]

    aiplatform.init(project=PROJECT_ID, location=REGION)

    job = aiplatform.CustomJob(
        display_name=job_name,
        worker_pool_specs=worker_pool_specs,
        staging_bucket=f"gs://{BUCKET_NAME}",
    )

    print(f"  Submitting...")
    job.submit()

    print(f"  Job: {job.resource_name}")
    print(f"  Monitor: https://console.cloud.google.com/vertex-ai/training/custom-jobs?project={PROJECT_ID}")
    return job


def main():
    print("=" * 60)
    print("THL 2025 Inference Job Submission (Trace-Based Prompts)")
    print(f"Mode: {MODE}")
    print("=" * 60)

    # Validate configuration
    if PROJECT_ID == "your-project-id":
        print("\nERROR: Please set GCP_PROJECT_ID environment variable")
        sys.exit(1)

    hf_token = os.environ.get("HF_TOKEN", "")
    if not hf_token or not hf_token.startswith("hf_"):
        print("\nERROR: HF_TOKEN required for Llama 3.3 access")
        sys.exit(1)

    print(f"\n  Project: {PROJECT_ID}")
    print(f"  Region: {REGION}")
    print(f"  Machine: {MACHINE_TYPE} ({ACCELERATOR_COUNT}x A100)")
    print(f"  Test data: {TRACES_DATA.name} (same prompts as Gemini baseline)")

    # Check authentication
    print(f"\nChecking Google Cloud authentication...")
    if not check_gcloud_auth():
        sys.exit(1)
    print("  OK!")

    # Verify adapter for THL mode
    if MODE in ("thl", "both"):
        print(f"\nVerifying LoRA adapter in GCS...")
        verify_adapter_exists()

    # Submit jobs
    if MODE == "both":
        print("\nSubmitting BOTH jobs (THL + Baseline)...")
        submit_job("thl")
        submit_job("baseline")
    else:
        submit_job(MODE)

    print(f"\n{'=' * 60}")
    print("DONE")
    print(f"Output will be at: gs://{BUCKET_NAME}/inference_output/")
    print(f"Estimated cost: ~$5-8 per job (A100 80GB, ~30-60 min)")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
