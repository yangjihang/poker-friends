FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --upgrade pip && \
    pip install "fastapi>=0.115" "uvicorn[standard]>=0.32" "pokerkit>=0.5.4" \
                "sqlalchemy[asyncio]>=2.0.35" "asyncpg>=0.30" "alembic>=1.14" \
                "pydantic>=2.9" "pydantic-settings>=2.6" "bcrypt>=4.2" \
                "pyjwt>=2.10" "python-multipart>=0.0.12" "httpx>=0.28"

COPY app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
