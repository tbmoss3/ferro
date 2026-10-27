FROM python:3.12-slim
WORKDIR /app
COPY server.py templates.py cad_kernel.py ./
COPY static ./static
# OCCT/build123d headless system deps (libGL etc.) + FastAPI app deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglu1-mesa libglib2.0-0 libxrender1 libxext6 libsm6 libice6 \
    && rm -rf /var/lib/apt/lists/*
# FastAPI app + CAD kernel deps (build123d pulls the OpenCASCADE/OCCT wheel, ~150MB)
RUN pip install --no-cache-dir fastapi uvicorn build123d trimesh matplotlib numpy ezdxf shapely pymupdf
# Brain: OPENROUTER_API_KEY env var (direct API call) — set in Railway dashboard.
# Optional: FERRO_BRAIN_MODEL (default z-ai/glm-5.3)
ENV PORT=8811 FERRO_CAD_PY=/usr/local/bin/python
EXPOSE 8811
CMD ["sh", "-c", "python3 -m uvicorn server:app --host 0.0.0.0 --port ${PORT:-8811}"]