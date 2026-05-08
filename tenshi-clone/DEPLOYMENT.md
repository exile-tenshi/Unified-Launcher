# Tenshi Hub Digital Deployment Guide

To satisfy the request that **"everything now needs to be not run on my pc but digitally"**, the server backend has been prepared for standard Cloud hosting. You can now host `server.py` on a cloud VPS (Virtual Private Server) like DigitalOcean, Linode, AWS, or your preferred hosting provider so it runs 24/7.

## Prerequisites
1. A Linux VPS (e.g., Ubuntu).
2. Docker and Docker Compose installed on the VPS.
3. Access to your Domain Settings (e.g., porkbun `tenshi.lol` DNS).

## Deployment Steps
1. **Upload the Server Files**: Transfer the `SecureVoiceApp` directory to your VPS using SFTP or Git.
2. **Setup Persistence**: Ensure empty JSON files exist for database persistence before running:
   ```bash
   touch server_registry.json
   touch servers.json
   ```
3. **Start the Server**: Open your VPS terminal, navigate to the `SecureVoiceApp` directory, and run:
   ```bash
   docker-compose up -d
   ```
   This will securely build the lightweight Python container and start running `server.py` on ports 6000 and 6001 in the background forever.

4. **Update Client Connection**: In `client.py`, change `SERVER_IP = "127.0.0.1"` to your actual VPS Public IP address.
   *(Optional: You can map a custom subdomain like `play.tenshi.lol` using an A-Record in Porkbun pointing to your VPS IP, and set `SERVER_IP = "play.tenshi.lol"`).*

5. **SMTP Configuration (Emails)**: To allow users to send external emails from Tenshi directly to users on Gmail or Yahoo, edit the `SMTP_CONFIG` section at the top of `server.py` with your real Email API credentials (e.g. from Porkbun or SendGrid), and restart the container:
   ```bash
   docker-compose restart
   ```

Your entire messaging routing infrastructure will now be fully digital and self-hosted on the cloud!
