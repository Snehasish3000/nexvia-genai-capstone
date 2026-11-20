FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt /tmp/ui-requirements.txt
RUN pip install --no-cache-dir -r /tmp/ui-requirements.txt
COPY . /app

EXPOSE 8501
HEALTHCHECK --interval=20s --timeout=5s --start-period=15s --retries=5 \
  CMD-SHELL python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/_stcore/health' % os.getenv('PORT','8501'), timeout=3)"
CMD ["sh", "-c", "streamlit run app.py --server.address=0.0.0.0 --server.port=${PORT:-8501} --server.headless=true"]
