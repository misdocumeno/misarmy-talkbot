# Dev: Poetry. Prod image: in-project venv from Poetry, run with plain python (no Poetry at runtime).
FROM python:3.12-slim-bookworm AS builder

RUN pip install --no-cache-dir poetry==2.2.1

WORKDIR /app
COPY pyproject.toml poetry.lock ./
COPY src ./src

RUN poetry config virtualenvs.in-project true \
    && poetry install --only main --no-root --no-interaction

FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m bot

WORKDIR /home/bot

RUN mkdir -p /home/bot/config

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/home/bot/src
ENV PATH=/home/bot/.venv/bin:$PATH

COPY --from=builder /app/.venv /home/bot/.venv
COPY --from=builder /app/src/misarmy_talkbot /home/bot/src/misarmy_talkbot

USER bot

ENTRYPOINT ["python", "-m", "misarmy_talkbot"]
CMD []
