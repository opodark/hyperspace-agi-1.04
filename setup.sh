#!/bin/bash
set -e

echo "Setting up HyperSpace 1.04..."

if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        cp .env.example .env
        echo ".env creato da .env.example"
    else
        echo "[warn] .env.example non trovato, crea .env manualmente prima di avviare lo stack"
    fi
else
    echo ".env già presente, non sovrascritto"
fi

npm install
echo "HyperSpace directories initialized."
echo "HIP schema and Intent Router implemented for 1.04."

echo "Avvio stack: docker compose up -d --build"
docker compose up -d --build

echo ""
echo "HyperSpace AGI avviato"
echo "Dashboard: http://localhost:8085/dashboard"
echo "Node API:  http://localhost:8084/status"
