# Phase J (Docker/VPS): the `api` service image.
#
# Editable install, not a built wheel: app/core/config.py resolves
# config/default.yaml and app/tools/{amazon,keepa}/provider.py resolve
# their fixtures via Path(__file__).resolve().parents[N] relative to the
# repo root, not a packaged data path. That only works when the source
# tree stays intact next to the installed package — `pip install -e .`
# is what preserves that, matching exactly how the README already tells
# a human to set this project up locally (`pip install -e ".[dev]"`).
# This is a real, load-bearing reason, not a shortcut: a `pip install .`
# (built wheel) here would boot and then fail to find config/ at runtime.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY . .
RUN pip install -e . && chmod +x scripts/docker-entrypoint-api.sh

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=5 \
    CMD curl -f http://localhost:8000/health || exit 1

ENTRYPOINT ["scripts/docker-entrypoint-api.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
