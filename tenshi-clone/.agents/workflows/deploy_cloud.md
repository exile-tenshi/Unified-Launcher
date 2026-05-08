---
description: How to deploy Tenshi Hub to Vultr (V2.2 Ultra Performance)
---

This workflow automates the deployment of the Tenshi Python backend to a dedicated Vultr VPS with Upstash Redis and SSL protection via Cloudflare.

### Prerequisites
1.  **Vultr VPS**: A Debian 12 instance ($6/mo).
2.  **Upstash Redis**: A database URL and Password.
3.  **SSH Access**: You must be able to SSH into your Vultr box.

### 1. Install Docker on Vultr
Run these commands on your VPS terminal:
```bash
sudo apt update && sudo apt install -y docker.io docker-compose
```

### 2. Configure Environment Variables
Create a `.env` file on your VPS in the Tenshi directory:
```bash
nano .env
```
Add your Redis URL:
```text
UPSTASH_REDIS_URL=redis://default:yourpassword@your-upstash-endpoint.upstash.io:32415
```

### 3. Deploy
// turbo
Run this command from the `SecureVoiceApp` directory on the VPS:
```bash
docker-compose up -d --build
```

### 4. Verify
// turbo
Check the logs to ensure Redis is connected:
```bash
docker logs -f tenshi-backend
```
Look for: `--- UPSTASH REDIS CONNECTED ---`
