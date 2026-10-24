# ---- Étape de construction : python-gssapi se compile contre MIT Kerberos
FROM python:3.12-slim AS build
RUN apt-get update && apt-get install -y --no-install-recommends gcc libkrb5-dev \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r /tmp/requirements.txt

# ---- Image finale
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    FRONTEND_DIR=/app/frontend \
    KRB5_KTNAME=/etc/krb5/http.keytab \
    KRB5_CONFIG=/etc/krb5.conf
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends libgssapi-krb5-2 krb5-user \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels
WORKDIR /app
COPY backend/app /app/backend/app
COPY frontend /app/frontend
RUN useradd --uid 10001 --home-dir /app --shell /usr/sbin/nologin assistant \
    && mkdir -p /data /etc/krb5 && chown assistant /data
USER assistant
WORKDIR /app/backend
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=4s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
