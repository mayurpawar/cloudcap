# CloudCap — the governance hub app (dashboard + agent fleet), for Cloud Run.
# Runs as the least-privilege RUNTIME service account provisioned by terraform/fleet.
FROM python:3.12-slim

WORKDIR /app

# Deps first (layer cache). Mock mode needs none; these enable live GCP + auth + Gemini.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cloud Run sets $PORT; serve.py binds 0.0.0.0:$PORT when PORT is present.
ENV PORT=8080
EXPOSE 8080

# The web app IS the entrypoint; it runs the fleet on demand + on schedule.
CMD ["python", "-m", "webui.serve"]
