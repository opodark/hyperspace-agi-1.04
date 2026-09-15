FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git nodejs npm \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 11000 sandbox

WORKDIR /app
COPY sandbox/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY sandbox/runner.py /app/runner.py
COPY . /seed

RUN mkdir -p /exchange/jobs /exchange/results /workspaces \
    && chown -R sandbox:sandbox /exchange /workspaces \
    && chmod -R a-w /seed

USER sandbox
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

CMD ["python", "/app/runner.py"]
