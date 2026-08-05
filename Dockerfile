FROM python:3.12-slim

USER root

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN pip install --no-cache-dir playwright==1.62.0 \
    && playwright install --with-deps chromium-headless-shell \
    && rm -rf /var/lib/apt/lists/* /root/.cache

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && rm -rf /usr/local/lib/python3.12/site-packages/pip \
              /usr/local/lib/python3.12/site-packages/pip-*.dist-info \
              /usr/local/lib/python3.12/site-packages/setuptools \
              /usr/local/lib/python3.12/site-packages/setuptools-*.dist-info \
              /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.12 \
    && find /usr/local/lib/python3.12/site-packages -type d -name __pycache__ -prune -exec rm -rf {} +

COPY . .

RUN mkdir -p /app/logs /app/screenshots \
    && useradd --system --uid 1000 --no-create-home appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["python", "-m", "app.bot"]
