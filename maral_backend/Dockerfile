# syntax=docker/dockerfile:1
# One image, several commands: web (gunicorn/runserver), worker, beat, flower.
#   docker build --target development -t maral-backend:dev .
#   docker build -t maral-backend:prod .          # last stage = production
ARG PYTHON_VERSION=3.12

# ---- runtime base: Python + unixODBC + Microsoft ODBC Driver 18 for SQL Server -------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/opt/venv/bin:$PATH"
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates unixodbc \
 && curl -fsSL -o /tmp/packages-microsoft-prod.deb https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb \
 && dpkg -i /tmp/packages-microsoft-prod.deb && rm /tmp/packages-microsoft-prod.deb \
 && apt-get update \
 && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
 && apt-get purge -y curl && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin maral
WORKDIR /app

# ---- build stage: compilers live only here -------------------------------------------------
FROM base AS build-base
RUN apt-get update && apt-get install -y --no-install-recommends build-essential unixodbc-dev \
 && rm -rf /var/lib/apt/lists/* \
 && python -m venv /opt/venv
COPY requirements/ requirements/

FROM build-base AS deps-development
RUN pip install -r requirements/dev.txt

FROM build-base AS deps-production
RUN pip install -r requirements/production.txt

# ---- development: bind-mount the source (see docker-compose.yml) ---------------------------
FROM base AS development
COPY --from=deps-development /opt/venv /opt/venv
COPY --chown=maral:maral . /app
USER maral
ENV DJANGO_SETTINGS_MODULE=config.settings.development
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD ["python", "scripts/healthcheck.py"]
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

# ---- production -----------------------------------------------------------------------------
FROM base AS production
COPY --from=deps-production /opt/venv /opt/venv
COPY --chown=maral:maral . /app
ENV DJANGO_SETTINGS_MODULE=config.settings.production
# collectstatic needs settings to import but touches no database; these values exist only for this RUN.
RUN SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(64))')" ALLOWED_HOSTS=localhost \
    python manage.py collectstatic --noinput && chown -R maral:maral /app/staticfiles
USER maral
EXPOSE 8000
# The probe sends the first ALLOWED_HOSTS entry as Host header so Django's host validation passes.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD ["python", "scripts/healthcheck.py"]
# Concurrency comes from WEB_CONCURRENCY (read by gunicorn). Access logging is done by our middleware.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--worker-tmp-dir", "/dev/shm", "--timeout", "30", "--error-logfile", "-"]
