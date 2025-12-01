from fastapi import FastAPI, Response, status
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
import time

app = FastAPI(title="Sample API v1")

REQUEST_COUNT = Counter(
    "sample_api_requests_total",
    "Total HTTP requests",
    ["endpoint", "method", "status_code"],
)

REQUEST_LATENCY = Histogram(
    "sample_api_request_latency_seconds",
    "Request latency",
    ["endpoint", "method"],
)


def track_request(endpoint: str, method: str, status_code: int, start_time: float) -> None:
    duration = time.time() - start_time
    REQUEST_COUNT.labels(endpoint=endpoint, method=method, status_code=status_code).inc()
    REQUEST_LATENCY.labels(endpoint=endpoint, method=method).observe(duration)


@app.get("/health")
def health() -> dict:
    start = time.time()
    status_code = status.HTTP_200_OK
    track_request("/health", "GET", status_code, start)
    return {"status": "ok", "service": "api-v1"}


@app.get("/slow")
def slow(delay_ms: int = 1500) -> dict:
    start = time.time()
    time.sleep(max(delay_ms, 0) / 1000.0)
    status_code = status.HTTP_200_OK
    track_request("/slow", "GET", status_code, start)
    return {"status": "ok", "delay_ms": delay_ms}


@app.get("/error")
def error() -> Response:
    start = time.time()
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    track_request("/error", "GET", status_code, start)
    return Response(
        content='{"error": "simulated error"}',
        status_code=status_code,
        media_type="application/json",
    )


@app.get("/metrics")
def metrics() -> Response:
    data = generate_latest()  # type: ignore[arg-type]
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)

