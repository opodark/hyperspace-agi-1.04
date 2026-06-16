FROM python:3.9-slim

# Build context = root del progetto (docker-compose: build.context: .)

WORKDIR /app

# Modulo condiviso identità
COPY shared/ /app/shared/

# Node
COPY node/requirements.txt /app/node/requirements.txt
RUN pip install --no-cache-dir -r /app/node/requirements.txt

COPY node/ /app/node/

# Directory persistenza identità (montata come volume dal compose)
RUN mkdir -p /app/data

ENV DATA_DIR=/app/data
ENV PYTHONPATH=/app

WORKDIR /app/node

CMD ["python", "main.py"]
