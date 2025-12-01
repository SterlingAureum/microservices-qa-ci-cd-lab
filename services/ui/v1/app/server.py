from fastapi import FastAPI, Response, status
from fastapi.responses import HTMLResponse
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
import time

app = FastAPI(title="Sample UI v1")

REQUEST_COUNT = Counter(
    "sample_ui_requests_total",
    "Total HTTP requests",
    ["endpoint", "method", "status_code"],
)

REQUEST_LATENCY = Histogram(
    "sample_ui_request_latency_seconds",
    "Request latency",
    ["endpoint", "method"],
)


def track_request(endpoint: str, method: str, status_code: int, start_time: float) -> None:
    duration = time.time() - start_time
    REQUEST_COUNT.labels(endpoint=endpoint, method=method, status_code=status_code).inc()
    REQUEST_LATENCY.labels(endpoint=endpoint, method=method).observe(duration)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    start = time.time()
    status_code = status.HTTP_200_OK
    track_request("/", "GET", status_code, start)
    return """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>Sample UI v1</title>
  </head>
  <body>
    <h1>Sample UI v1</h1>
    <p>This is a minimal demo frontend service used for QA pipeline experiments.</p>
  </body>
</html>
"""


@app.get("/health")
def health() -> dict:
    start = time.time()
    status_code = status.HTTP_200_OK
    track_request("/health", "GET", status_code, start)
    return {"status": "ok", "service": "ui-v1"}


@app.get("/error")
def error() -> Response:
    start = time.time()
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    track_request("/error", "GET", status_code, start)
    return Response(
        content='{"error": "simulated ui error"}',
        status_code=status_code,
        media_type="application/json",
    )


@app.get("/metrics")
def metrics() -> Response:
    data = generate_latest()  # type: ignore[arg-type]
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)

