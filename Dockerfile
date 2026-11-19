FROM node:24.18.0-bookworm-slim AS frontend
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --include=dev --no-audit
COPY web/ ./
RUN npm test && npm run build

FROM python:3.11.18-slim-bookworm
ENV PUBLIC_DEMO_MODE=1 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY ["02_项目代码/requirements.txt", "config/dependency_constraints.txt", "/tmp/"]
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt -c /tmp/dependency_constraints.txt
COPY ["02_项目代码/", "02_项目代码/"]
COPY config/ config/
COPY contracts/ contracts/
COPY demo_data/ demo_data/
COPY --from=frontend /build/web/dist web/dist/
RUN mkdir -p /app/outputs && chown 10001:10001 /app/outputs
USER 10001:10001
EXPOSE 8080
CMD ["sh", "-c", "exec env -u DEEPSEEK_API_KEY PUBLIC_DEMO_MODE=1 python -X utf8 -m uvicorn --app-dir '02_项目代码' http_api:app --host 0.0.0.0 --port \"${PORT:-8080}\""]
