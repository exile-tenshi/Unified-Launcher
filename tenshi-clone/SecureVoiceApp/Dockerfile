FROM python:3.10-slim
WORKDIR /app
COPY server.py clamav_scan.py mailbox_crypto.py hub_dsl.py ai_providers.py premium_marketplace.py ./
EXPOSE 6000
EXPOSE 6001
RUN pip install --no-cache-dir redis cryptography requests
CMD ["python", "-u", "server.py"]
