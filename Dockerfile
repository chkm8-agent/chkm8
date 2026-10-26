# chkm8 container images. Build with deploy/build.sh (multi-arch, pushes to GHCR).
#   target "app":    monitor + trade Jobs (slim)
#   target "report": app + Chromium for the PnL PNG
# Code lives in /app (read-only at runtime); mutable state in /data (PVC); runs as uid 10001.

ARG PY=3.12

FROM python:${PY}-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    CHKM8_DATA=/data CHKM8_LOGS=- CHKM8_REPORTS=/data/reports \
    CHKM8_ACCOUNT_JSON=/app/lighter/account.json TZ=America/Los_Angeles
RUN apt-get update && apt-get install -y --no-install-recommends tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY deploy/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
 # the SDK bundles signer libs for every OS/arch; keep only this platform's
 && SIG=$(python -c "import lighter,os;print(os.path.dirname(lighter.__file__)+'/signers')") \
 && ARCH=$(dpkg --print-architecture) \
 && find "$SIG" -type f ! -name "lighter-signer-linux-${ARCH}.*" ! -name README.md -delete
RUN useradd --uid 10001 --create-home chkm8 && mkdir -p /data && chown chkm8 /data
WORKDIR /app
COPY chkm8env.py xpost.py mailer.py ./
COPY lighter/monitor.py lighter/trade.py lighter/report.py lighter/paper_scanner.py lighter/paper_mr.py lighter/account.json lighter/
USER 10001
CMD ["python", "lighter/monitor.py"]

FROM app AS report
USER root
RUN apt-get update && apt-get install -y --no-install-recommends chromium fonts-dejavu-core fonts-noto-color-emoji \
 && rm -rf /var/lib/apt/lists/*
ENV CHKM8_BROWSER=/usr/bin/chromium
USER 10001
CMD ["python", "lighter/report.py"]
