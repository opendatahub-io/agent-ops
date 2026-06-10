FROM registry.access.redhat.com/ubi9/python-312:latest

WORKDIR /opt/app-root

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml .
COPY src ./src
RUN uv pip install --python /opt/app-root/bin/python3 --no-cache .

ENV PYTHONUNBUFFERED=1

EXPOSE 8001
CMD ["python3", "-m", "banking_agent"]
