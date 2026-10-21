FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY server.py templates.py ./
COPY static ./static
RUN pip install --no-cache-dir fastapi uvicorn
# Hermes agent brain — install CLI (needs HERMES_HOME mounted/injected at runtime)
RUN curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash || true
ENV PORT=8811
EXPOSE 8811
CMD ["python3", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8811"]