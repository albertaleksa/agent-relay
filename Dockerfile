FROM ghcr.io/astral-sh/uv:0.12.17 AS uv

FROM python:3.11-slim

COPY --from=uv /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY main.py worker.py database.py storage.py schemas.py errors.py dashboard.py dashboard.html ./

RUN useradd --create-home --uid 10001 relay \
    && chown -R relay:relay /app

USER relay

EXPOSE 8000

CMD ["/app/.venv/bin/uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
