FROM python:3.12-slim AS base

WORKDIR /app

COPY pyproject.toml ./
ARG BACKEND_EXTRAS=""
RUN pip install --no-cache-dir ".${BACKEND_EXTRAS}" uvicorn

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
