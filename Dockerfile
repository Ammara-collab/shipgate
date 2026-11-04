FROM python:3.12-slim

# Docker-in-Docker is NOT used: on the Vultr VM the host Docker socket is
# mounted so the app can spawn fresh sibling containers per run.
RUN pip install --no-cache-dir Flask playwright \
    && playwright install --with-deps chromium

WORKDIR /app
COPY . /app

ENV PYTHONDONTWRITEBYTECODE=1
EXPOSE 5000
CMD ["python", "app.py"]
