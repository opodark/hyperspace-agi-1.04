FROM python:3.12-slim

WORKDIR /app

COPY control-plane/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY control-plane/main.py .
COPY control-plane/dashboard.html .
COPY control-plane/connectors ./connectors

COPY shared ./shared

ENV PYTHONPATH=/app:/app/shared

EXPOSE 8085

CMD ["python", "main.py"]
