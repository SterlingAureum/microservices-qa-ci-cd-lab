FROM python:3.11-slim

WORKDIR /runner

COPY qa_runner.py /runner/

RUN pip install --no-cache-dir \
    requests \
    pyyaml

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "/runner/qa_runner.py"]

