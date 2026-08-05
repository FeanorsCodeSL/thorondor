FROM python:3.13-slim@sha256:bf503bb2243c5aad0aa951544dd60d165f992646441d35dea90893703fc26251

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser
COPY --chown=appuser:appuser chunking/ ./chunking/

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz').status==200 else 1)"

USER appuser
CMD ["uvicorn", "chunking.app:app", "--host", "0.0.0.0", "--port", "8000"]
