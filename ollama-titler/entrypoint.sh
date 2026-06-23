#!/bin/sh
# Avvia Ollama e pull automatico di qwen2:0.5b al primo avvio
ollama serve &
OLLAMA_PID=$!

echo "[titler] Attendo Ollama..."
until curl -s http://localhost:11434/api/tags > /dev/null 2>&1; do
  sleep 1
done

echo "[titler] Pull qwen2:0.5b..."
ollama pull qwen2:0.5b

echo "[titler] Pronto — modello caricato"
wait $OLLAMA_PID
