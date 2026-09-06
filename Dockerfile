FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY ai_review ./ai_review
RUN pip wheel --no-deps --wheel-dir /wheels .

FROM python:3.12-slim
ARG VCS_REF=unknown
LABEL org.opencontainers.image.revision=$VCS_REF
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels
WORKDIR /review
ENTRYPOINT ["ai-review"]
