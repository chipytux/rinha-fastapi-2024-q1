FROM cimg/python:3.12.2 as poetry_export
WORKDIR /tmp/

COPY pyproject.toml poetry.lock ./

RUN poetry export --without dev -f requirements.txt --output requirements.txt


FROM python:3.12-slim as final
WORKDIR /app/

COPY --from=poetry_export /tmp/requirements.txt requirements.txt


ENV PYTHONUNBUFFERED=1 \
	PYTHONDONTWRITEBYTCODE=1 \
	PIP_NO_CACHE_DIR=off \
	PIP_DEFAULT_TIMEOUT=100 \
    PYTHONPATH=/app

COPY backend /app/backend

RUN pip install -r requirements.txt --no-cache-dir && rm requirements.txt
