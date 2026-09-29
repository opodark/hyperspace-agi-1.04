#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

REPO="${HYPERSPACE_REPO:-/Users/opo/hyperspace-agi-1.04}"
KOHYA_DIR="${KOHYA_DIR:-$REPO/data/training/tools/sd-scripts}"
DATASET_DIR="${DATASET_DIR:-$REPO/data/training/anna}"
OUTPUT_DIR="${OUTPUT_DIR:-$REPO/data/training/output}"
BASE_MODEL="${BASE_MODEL:-/Users/opo/ComfyUI-Shared/models/checkpoints/CyberRealisticPony_V18.0_F16.safetensors}"
OUTPUT_NAME="${OUTPUT_NAME:-anna_identity_sdxl}"

"$REPO/.venv/bin/python" "$REPO/integrations/training/validate_dataset.py" "$DATASET_DIR"
[[ -f "$BASE_MODEL" ]] || { echo "checkpoint assente: $BASE_MODEL" >&2; exit 2; }
[[ -f "$KOHYA_DIR/sdxl_train_network.py" ]] || {
  echo "kohya assente: esegui integrations/training/install-kohya.sh" >&2
  exit 2
}
mkdir -p "$OUTPUT_DIR"
exec "$KOHYA_DIR/.venv/bin/accelerate" launch "$KOHYA_DIR/sdxl_train_network.py" \
  --config_file "$REPO/integrations/training/kohya-sdxl-lora.toml" \
  --pretrained_model_name_or_path "$BASE_MODEL" \
  --train_data_dir "$DATASET_DIR" \
  --output_dir "$OUTPUT_DIR" \
  --output_name "$OUTPUT_NAME" \
  --sample_prompts "$REPO/integrations/training/sample_prompts.txt"

