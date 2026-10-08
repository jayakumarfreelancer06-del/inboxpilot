FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY inboxpilot ./inboxpilot
COPY evals ./evals
COPY tests ./tests
COPY pytest.ini .
COPY data/samples ./data/samples
RUN useradd -m app && mkdir -p /app/data /app/secrets && chown -R app /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
CMD ["uvicorn", "inboxpilot.api:app", "--host", "0.0.0.0", "--port", "8000"]
