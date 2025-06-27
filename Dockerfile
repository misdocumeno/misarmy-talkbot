FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y ffmpeg && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN useradd -m bot

USER bot

WORKDIR /home/bot

RUN mkdir /home/bot/config

ENV PATH="$PATH:/home/bot/.local/bin"

ENV PYTHON_ENV="prod"

RUN pip install poetry==1.8.3

COPY pyproject.toml poetry.lock* ./

RUN poetry install --no-interaction --no-root --no-dev

COPY misarmy_talkbot/ misarmy_talkbot/

ENTRYPOINT [ "poetry", "run", "python", "-m", "misarmy_talkbot" ]