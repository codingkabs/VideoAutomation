# vauto: web app, worker and Telegram bot in one image.
# Build:  docker compose build        Run: see docker-compose.yml and README > Run it 24/7
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY videoautomation ./videoautomation

# EXTRAS=all adds automatic subtitles (large) and browser automation.
ARG EXTRAS=web,claude,s3
RUN pip install --no-cache-dir ".[${EXTRAS}]"

# Settings live in /config/.env (edited from the web app's Setup tab);
# renders, the queue, tokens and the outbox live in /data.
ENV VAUTO_HOME=/data PYTHONUNBUFFERED=1
WORKDIR /config
VOLUME ["/data"]
EXPOSE 8765

CMD ["vauto", "web", "--host", "0.0.0.0", "--port", "8765"]
