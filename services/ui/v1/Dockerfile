FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN pip install --no-cache-dir \
    fastapi \
    "uvicorn[standard]" \
    prometheus_client

COPY app /app/app

EXPOSE 8080

CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8080"]

