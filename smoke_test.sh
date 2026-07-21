#!/usr/bin/env bash
set -euo pipefail

BASE=${BASE:-http://localhost:8085}
MODEL=${MODEL:-qwen3-35b-uncensored}

echo "Using BASE=$BASE MODEL=$MODEL"

echo "\n1) GET /mesh/nodes"
resp1=$(curl -sS "$BASE/mesh/nodes")
echo "$resp1" | jq .
if echo "$resp1" | grep -qi ngrok; then
  echo "ERROR: found ngrok in /mesh/nodes output" >&2
  exit 2
fi


echo "\n2) GET /nodes/active"
resp2=$(curl -sS "$BASE/nodes/active")
echo "$resp2" | jq .
if echo "$resp2" | grep -qi ngrok; then
  echo "ERROR: found ngrok in /nodes/active output" >&2
  exit 2
fi


echo "\n3) GET /v1/models"
resp3=$(curl -sS "$BASE/v1/models")
echo "$resp3" | jq .


echo "\n4) POST /v1/chat/completions (non-stream)"
# read -d '' ritorna sempre exit 1 a fine heredoc (nessun byte nullo da trovare):
# con 'set -e' interromperebbe lo script anche se PAYLOAD e' stato letto bene.
read -r -d '' PAYLOAD <<EOF || true
{"messages":[{"role":"user","content":"Smoke test: rispondi con OK"}],"model":"$MODEL","stream":false}
EOF

resp4=$(curl -sS -X POST "$BASE/v1/chat/completions" -H "Content-Type: application/json" -d "$PAYLOAD")
echo "$resp4" | jq .

if echo "$resp4" | grep -qi error; then
  echo "WARN: response contains 'error' field — inspect output above" >&2
fi

echo "\nSmoke test completed. If no 'ngrok' occurrences and responses look valid, routing is updated." 
