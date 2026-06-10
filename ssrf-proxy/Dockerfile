FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser
COPY --chown=appuser:appuser proxy.py /app/proxy.py

EXPOSE 8888
USER appuser
CMD ["python", "/app/proxy.py"]
