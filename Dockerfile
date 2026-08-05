FROM python:3.12-slim

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN python -m venv $VIRTUAL_ENV

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install --with-deps chromium

RUN groupadd --system appuser && useradd --system --no-create-home --gid appuser appuser

WORKDIR /app
COPY . .

RUN mkdir -p /app/logs /app/screenshots \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["python", "-m", "app.bot"]
