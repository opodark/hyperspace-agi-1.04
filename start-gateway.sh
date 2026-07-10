#!/bin/bash
# HyperSpace AGI — Start Unified Gateway (Caddy + ngrok)

echo "🚀 Avvio Gateway HyperSpace (Caddy + ngrok)..."

# Avvia Caddy
caddy run --config Caddyfile --adapter caddyfile & 
CADDY_PID=$!

sleep 2

# Avvia ngrok
echo "🌐 Avvio ngrok su porta 8085..."
ngrok http 8085 --log=stdout &

echo "✅ Gateway avviato!"
echo "Control Plane URL: http://localhost:8085"
echo "Usa l'URL pubblico di ngrok in Lovable."

wait