# Dev-only image (IDE / agent). Production uses the repo-root Dockerfile.
FROM mcr.microsoft.com/devcontainers/python:2-3.12-bookworm

# Base image ships a Yarn apt repo; its signing key often breaks apt-get update.
RUN find /etc/apt/sources.list.d -maxdepth 1 -name '*yarn*' -delete

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir poetry==2.2.1

ENV POETRY_VIRTUALENVS_IN_PROJECT=1
