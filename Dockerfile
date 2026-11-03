FROM python:3.12-slim

LABEL org.opencontainers.image.title="garmin-gateway" \
      org.opencontainers.image.description="Garmin gateway: MCP server (official Personal API reads, Garmin Connect workout writes) and read-only REST API"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GARMIN_TOKEN_STORE=/data/garmin_tokens.json

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src /app

# /data holds the garminconnect token file, rewritten by garminconnect when it refreshes the tokens.
RUN useradd --system --no-create-home --uid 10001 app \
 && mkdir /data \
 && chown 10001:10001 /data \
 && chmod 700 /data
VOLUME /data
USER app

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:%s/health'%os.environ.get('PORT','8001'),timeout=3)" || exit 1

CMD ["python", "-m", "garmin_gateway"]
