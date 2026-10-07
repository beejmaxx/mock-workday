FROM ghcr.io/astral-sh/uv:0.12.5 AS uv
FROM python:3.12-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev && useradd --create-home service
USER service
EXPOSE 8080 8081
CMD ["sh", "-c", ".venv/bin/python -m mock_workday.bootstrap && exec .venv/bin/python -m mock_workday.app"]
