# Tenshi Hub: Porkbun & Cloud Backend Deployment

Your domain **tenshi.lol** is officially managed by Porkbun. Right now, Porkbun is successfully routing visitors to the jaw-dropping Dark Mode Discord UI we built!

However, **Porkbun is a Domain Registrar**, meaning it only routes names. It **does not** physically provide a full-scale Virtual Private Server (Linux box) capable of running raw Python daemons (`server.py`) 24/7 in the background over custom ports (like `8080` and `6000`).

To get the backend fully online so the Web Interface stops falling back to Mockup Data, follow these exact 3 steps!

## Step 1: Upload the Python Brain to a Free Cloud Host
You need a cloud service designed specifically for raw Python applications. The best free ones are **Render.com** or **Railway.app**.

1. Create a free account on [Render](https://render.com).
2. Create a "New Web Service".
3. Upload this entire `Cloud_Server_Build` folder (which I specifically packaged to contain only the scripts the server needs to run, fully isolated from your desktop Game code).
4. Set the Start Command to: `python server.py`.
5. Render will execute it and give you a live free IP Address!

## Step 2: Link Porkbun to the New Render IP
Once Render tells you "Server is live on IP: `192.168.x.x`"...

1. Log into your **Porkbun** Dashboard.
2. Manage your `tenshi.lol` DNS Records.
3. Add a new **A-Record**.
   - Host/Name: `api` (This creates `api.tenshi.lol`).
   - Answer/IP: `<Paste your Render IP here>`.
4. Click Save! Porkbun now knows exactly where your new cloud backend is.

## Step 3: Tell the Client Code to look at the Cloud!
Now that `api.tenshi.lol` connects directly to your cloud brain...

1. Open `d:\New folder (2)\Tenshi\TenshiWeb\webapp.js`.
2. Change line 1 from:
   `const apiUrl = "http://127.0.0.1:8080/";`
   to:
   `const apiUrl = "http://api.tenshi.lol:8080/";`
3. Because you updated the web source code, push your changes to the `main` branch — Cloudflare Pages will automatically redeploy `TenshiWeb/`.

**You are completely finished.** Your native Windows App and your Web App will now talk securely over the internet through your Porkbun domain.
