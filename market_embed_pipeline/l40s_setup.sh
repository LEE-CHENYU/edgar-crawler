#!/bin/bash
# L40S setup: ensure pip, torch, transformers, and pre-download the e5 model.
# curl-only transfer pipeline needs no gcs libs. Run over held-open ssh.
set -x
cd "$HOME"
mkdir -p embed/out
export DEBIAN_FRONTEND=noninteractive
python3 -m pip --version >/dev/null 2>&1 || { sudo apt-get update -y; sudo apt-get install -y python3-pip; }
python3 -c "import torch" 2>/dev/null || python3 -m pip install --quiet torch transformers
python3 -c "import transformers" 2>/dev/null || python3 -m pip install --quiet transformers
python3 - <<'PY'
from transformers import AutoModel, AutoTokenizer
AutoTokenizer.from_pretrained("intfloat/multilingual-e5-large", use_fast=True)
AutoModel.from_pretrained("intfloat/multilingual-e5-large")
print("MODEL_READY")
PY
python3 -c "import torch; print('CUDA', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"
echo "L40S_SETUP_DONE $(date)"
