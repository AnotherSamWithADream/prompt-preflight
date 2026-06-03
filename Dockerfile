# Containerized enhancing proxy (API backend). Build the wheel, then run a slim image.
#
#   docker build -t prompt-preflight .
#   docker run --rm -p 8788:8788 -e ANTHROPIC_API_KEY=sk-... prompt-preflight
#
# Point Claude Code at it:  ANTHROPIC_BASE_URL=http://localhost:8788 claude
# The proxy enhances each client's main prompt with Haiku (API backend) and forwards the
# client's own Authorization header upstream, so it can serve multiple users.

FROM python:3.12-slim AS build
WORKDIR /src
COPY . .
RUN python -m pip install --no-cache-dir build && python -m build --wheel

FROM python:3.12-slim
LABEL org.opencontainers.image.title="prompt-preflight" \
      org.opencontainers.image.description="Local enhancing proxy that rewrites prompts before a stronger model sees them" \
      org.opencontainers.image.licenses="MIT"

RUN useradd --create-home --uid 10001 app
COPY --from=build /src/dist/*.whl /tmp/
RUN python -m pip install --no-cache-dir /tmp/*.whl anthropic && rm -f /tmp/*.whl

USER app
ENV PROMPT_ENHANCER_BACKEND=api \
    PROMPT_ENHANCER_PROXY_HOST=0.0.0.0 \
    PROMPT_ENHANCER_PROXY_PORT=8788 \
    PROMPT_ENHANCER_ALLOW_PUBLIC_BIND=1
EXPOSE 8788

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8788/healthz', timeout=2).status==200 else 1)"

ENTRYPOINT ["enhance", "--serve-only"]
