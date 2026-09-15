FROM python:3.12-slim

WORKDIR /app

COPY federation-gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY federation-gateway/main.py .
COPY shared ./shared

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app:/app/shared

EXPOSE 8095

CMD ["python", "main.py"]
