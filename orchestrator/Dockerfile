FROM python@sha256:bf503bb2243c5aad0aa951544dd60d165f992646441d35dea90893703fc26251

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY orchestrator/requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser \
    && install -d -o appuser -g appuser /var/lib/thorondor
COPY --chown=appuser:appuser --chmod=0555 orchestrator/ ./orchestrator/
COPY --chown=appuser:appuser --chmod=0444 thorondor_contracts.py ./thorondor_contracts.py

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/livez').status==200 else 1)"

USER appuser
CMD ["uvicorn", "orchestrator.app:app", "--host", "0.0.0.0", "--port", "8080"]
