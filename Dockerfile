FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Europe/Berlin
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Optional: Browser für das automatische Anschreiben (docker build --build-arg WITH_BROWSER=1 .)
ARG WITH_BROWSER=0
RUN if [ "$WITH_BROWSER" = "1" ]; then pip install --no-cache-dir playwright && playwright install --with-deps chromium; fi

COPY ps5_scanner ./ps5_scanner
CMD ["python", "-m", "ps5_scanner"]
