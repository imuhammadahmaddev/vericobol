FROM python:3.12-slim

# No keys or endpoints here. LLM_BASE_URL, LLM_API_KEY, LLM_MODEL and
# FRONTEND_URL are supplied at runtime through the environment.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    COBC_PATH=cobc

RUN apt-get update \
    && apt-get install -y --no-install-recommends gnucobol \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Layout: /app/app, /app/tests, /app/mcp_server, /app/tools, /app/cobol_samples
# (including preset_bundles.json). .dockerignore excludes .hypothesis, .env,
# __pycache__ and .pytest_cache. `pytest` runs from /app, also when ./backend
# is bind-mounted over /app.
COPY app ./app
COPY tests ./tests
COPY mcp_server ./mcp_server
COPY tools ./tools
COPY cobol_samples ./cobol_samples
COPY pytest.ini ./pytest.ini

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
