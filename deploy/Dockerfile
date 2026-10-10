# Read-only dashboard image. Build context = repo root:
#   python -m nfip export-powerbi            # produces exports/powerbi/*.parquet
#   docker build -f deploy/Dockerfile -t nfip-dashboard .
FROM python:3.12-slim
WORKDIR /app
COPY deploy/requirements-app.txt .
RUN pip install --no-cache-dir -r requirements-app.txt
COPY app/ app/
COPY exports/powerbi/ exports/powerbi/
ENV NFIP_EXPORT_DIR=/app/exports/powerbi
EXPOSE 8501
USER nobody
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health')"
CMD ["streamlit", "run", "app/dashboard.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true", "--browser.gatherUsageStats=false"]
