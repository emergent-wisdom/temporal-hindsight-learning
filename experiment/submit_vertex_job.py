"""
Submit THL Training Job to Vertex AI
====================================
This script runs LOCALLY to submit a training job to Google Cloud.
The actual training happens on a cloud GPU via task.py.

Usage:
    python submit_vertex_job.py

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
    pass  # dotenv not installed, rely on environment variables

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
BUCKET_NAME = os.environ.get("GCS_BUCKET", "thl-training-bucket")

# Region must support A100 80GB (a2-ultragpu)
# Options: us-central1, us-east4, europe-west4, asia-southeast1
REGION = os.environ.get("GCP_REGION", "us-central1")

# Job settings
JOB_NAME = f"thl-llama33-70b-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

# GPU Configuration for Llama 3.3 70B
# - 70B in 4-bit QLoRA needs ~41GB VRAM (fits on 1x A100 80GB)
# - Use a2-ultragpu-1g for cost efficiency, or a2-ultragpu-4g for speed
# - a2-highgpu-1g (40GB) is NOT enough for 70B
MACHINE_TYPE = "a2-ultragpu-1g"  # 1x A100 80GB (~$5/hr)
ACCELERATOR_TYPE = "NVIDIA_A100_80GB"
ACCELERATOR_COUNT = 1

# Container image - Google Vertex AI PyTorch 2.4 image (latest available)
# We will install unsloth at runtime
CONTAINER_IMAGE = "us-docker.pkg.dev/vertex-ai/training/pytorch-gpu.2-4.py310:latest"

# Local paths
EXPERIMENT_DIR = Path(__file__).parent
DATA_FILES = [
    EXPERIMENT_DIR / "data" / "train_sft.jsonl",
    EXPERIMENT_DIR / "data" / "eval_sft.jsonl",
]
WORKER_SCRIPT = EXPERIMENT_DIR / "task.py"

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


def verify_files_exist():
    """Check that all required files exist locally."""
    missing = []
    for f in DATA_FILES + [WORKER_SCRIPT]:
        if not f.exists():
            missing.append(str(f))
    if missing:
        print("ERROR: Missing required files:")
        for m in missing:
            print(f"  - {m}")
        sys.exit(1)


def check_gcloud_auth():
    """Verify gcloud authentication is set up."""
    try:
        client = storage.Client(project=PROJECT_ID)
        # Try to list buckets to verify auth
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

def main():
    print("=" * 60)
    print("THL Training Job Submission")
    print("=" * 60)

    # Validate configuration
    if PROJECT_ID == "your-project-id":
        print("\nERROR: Please set GCP_PROJECT_ID environment variable")
        print("  export GCP_PROJECT_ID=your-actual-project-id")
        sys.exit(1)

    hf_token = os.environ.get("HF_TOKEN", "")
    if not hf_token or not hf_token.startswith("hf_"):
        print("\nERROR: HF_TOKEN required for Llama 3.3 access")
        print("  Get token at: https://huggingface.co/settings/tokens")
        print("  Then: export HF_TOKEN=hf_xxxxx")
        sys.exit(1)

    print(f"\nConfiguration:")
    print(f"  Project: {PROJECT_ID}")
    print(f"  Region: {REGION}")
    print(f"  Bucket: {BUCKET_NAME}")
    print(f"  Machine: {MACHINE_TYPE} ({ACCELERATOR_COUNT}x A100)")
    print(f"  Job Name: {JOB_NAME}")

    # Verify files exist
    print(f"\n[1/4] Verifying local files...")
    verify_files_exist()
    print("  All files found!")

    # Check authentication
    print(f"\n[2/4] Checking Google Cloud authentication...")
    if not check_gcloud_auth():
        sys.exit(1)
    print("  Authentication OK!")

    # Upload files to GCS
    print(f"\n[3/4] Uploading files to GCS...")
    staging_prefix = f"staging/{JOB_NAME}"

    data_uris = []
    for data_file in DATA_FILES:
        uri = upload_to_gcs(
            data_file,
            BUCKET_NAME,
            f"{staging_prefix}/{data_file.name}"
        )
        data_uris.append(uri)

    worker_uri = upload_to_gcs(
        WORKER_SCRIPT,
        BUCKET_NAME,
        f"{staging_prefix}/task.py"
    )

    # Submit job
    print(f"\n[4/4] Submitting Vertex AI CustomJob...")

    aiplatform.init(project=PROJECT_ID, location=REGION)

    # Worker pool spec
    # Using Google's PyTorch 2.5 container, installing Unsloth at runtime
    # See: https://github.com/unslothai/unsloth for install options
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
                    # Google PyTorch container needs Unsloth installed
                    f"""
                    set -ex

                    echo "=== THL Training Job Started ==="
                    nvidia-smi

                    echo "Installing dependencies..."
                    # Container has PyTorch 2.4 + CUDA 12.2 pre-installed
                    # Install unsloth (no extras = no flash-attn build)
                    pip install unsloth
                    pip install trl peft accelerate bitsandbytes transformers datasets huggingface_hub

                    echo "Installing GCS tools..."
                    pip install -q google-cloud-storage

                    echo "Downloading data from GCS..."
                    python -c "
from google.cloud import storage
client = storage.Client()
bucket = client.bucket('{BUCKET_NAME}')
bucket.blob('{staging_prefix}/train_sft.jsonl').download_to_filename('train_sft.jsonl')
bucket.blob('{staging_prefix}/eval_sft.jsonl').download_to_filename('eval_sft.jsonl')
bucket.blob('{staging_prefix}/task.py').download_to_filename('task.py')
print('Downloaded all files')
"

                    echo "Logging into Hugging Face..."
                    python -c "import os; from huggingface_hub import login; login(token=os.environ['HF_TOKEN'])"

                    echo "Starting training..."
                    python task.py

                    echo "=== Training Complete ==="
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

    job = aiplatform.CustomJob(
        display_name=JOB_NAME,
        worker_pool_specs=worker_pool_specs,
        staging_bucket=f"gs://{BUCKET_NAME}",
    )

    print(f"\n  Submitting job: {JOB_NAME}")
    print("  This will take a moment...")

    # Submit and don't wait (async)
    job.submit()

    print("\n" + "=" * 60)
    print("JOB SUBMITTED SUCCESSFULLY")
    print("=" * 60)
    print(f"\nJob Name: {JOB_NAME}")
    print(f"Job Resource: {job.resource_name}")
    print(f"\nMonitor at:")
    print(f"  https://console.cloud.google.com/vertex-ai/training/custom-jobs?project={PROJECT_ID}")
    print(f"\nOutput will be saved to:")
    print(f"  gs://{BUCKET_NAME}/model_output/")
    print("\nEstimated cost: ~$5-6/hour for A100 80GB")
    print("Training ~150 steps should complete in 1-2 hours.")


if __name__ == "__main__":
    main()
