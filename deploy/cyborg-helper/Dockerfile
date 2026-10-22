FROM node:22-bookworm-slim AS node
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 TMPDIR=/work/tmp HOME=/work/home XDG_CACHE_HOME=/work/cache HF_HUB_OFFLINE=1
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr tesseract-ocr-eng imagemagick fonts-dejavu-core libgomp1 git ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm
RUN pip install --no-cache-dir model2vec==0.10.0 'ocrmypdf==17.13.0' pikepdf==10.17.0 pillow==13.0.0
COPY quartz /opt/quartz
WORKDIR /opt/quartz
RUN mkdir -p /work/home /work/tmp /work/cache && git config --global url."https://github.com/".insteadOf ssh://git@github.com/ && git config --global --add url."https://github.com/".insteadOf git@github.com: && npm ci --no-audit --no-fund && npm cache clean --force
COPY worker.py /opt/helper/worker.py
COPY pdf_inventory.py /opt/helper/pdf_inventory.py
COPY searchable_pdf.py /opt/helper/pdf_local.py
WORKDIR /work
RUN pip install --no-cache-dir sqlite-vec==0.1.9
USER 1000:1000
EXPOSE 9020
CMD ["python","/opt/helper/worker.py"]
