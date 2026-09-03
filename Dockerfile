FROM ghcr.io/astral-sh/uv:0.12.9 AS uv

FROM python:3.12-slim

COPY --from=uv /uv /uvx /bin/
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

RUN useradd --uid 10001 --no-create-home --home /nonexistent --shell /usr/sbin/nologin gateway
USER 10001:10001

EXPOSE 8082 8083
CMD ["/app/.venv/bin/python", "-m", "feishu_dify_gateway"]
