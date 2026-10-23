FROM python:3.12-slim
WORKDIR /app
COPY server.py templates.py ./
COPY static ./static
RUN pip install --no-cache-dir fastapi uvicorn
# Brain: OPENROUTER_API_KEY env var (direct API call) — set in Railway dashboard.
# Optional: FERRO_BRAIN_MODEL (default z-ai/glm-5.3)
ENV PORT=8811
EXPOSE 8811
CMD ["sh", "-c", "python3 -m uvicorn server:app --host 0.0.0.0 --port ${PORT:-8811}"]