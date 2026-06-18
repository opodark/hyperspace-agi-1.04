FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY node/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ ./shared/
COPY node/main.py .
COPY node/ollama_proxy.py .
COPY node/__init__.py .
EXPOSE 8084
EXPOSE 11435
# Avvia entrambi: node API (8084) e ollama-proxy (11435)
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port 8084 & uvicorn ollama_proxy:app --host 0.0.0.0 --port 11435"]
