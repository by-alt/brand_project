# Brand Health Tracker — container image.
# Single image used for BOTH roles (pipeline run + dashboard); docker-compose picks the
# command per service. Slim base keeps it small; the data volume is mounted at runtime.

FROM python:3.11-slim

# Don't write .pyc files; flush logs straight to the console (better for `docker logs`).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first so this layer is cached unless requirements.txt changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the project in.
COPY . .

# The SQLite database lives on a mounted volume so data survives container restarts.
VOLUME ["/app/data"]

# Default command runs the full pipeline once. docker-compose overrides this for the
# dashboard service and for a scheduled (cron-style) pipeline service.
CMD ["python", "scripts/run_all.py"]
