FROM python:3.12-slim

WORKDIR /app
COPY orchestrator/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY orchestrator/ ./orchestrator/

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/healthz').status==200 else 1)"

CMD ["uvicorn", "orchestrator.app:app", "--host", "0.0.0.0", "--port", "8080"]
