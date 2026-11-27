# Public access with Cloudflare Tunnel

Cloudflare Tunnel gives the app a normal HTTPS address (e.g. `https://inventar.yourparish.sk`)
without opening any port on the church router. The device makes an outgoing connection to
Cloudflare, and Cloudflare forwards visitors to it. It's free.

You need a domain whose DNS is managed by Cloudflare (a free Cloudflare account is enough).

## 1. Create the tunnel

1. Log in to <https://one.dash.cloudflare.com> → **Networks → Tunnels** → **Create a tunnel**.
2. Choose **Cloudflared**, name it e.g. `church-inventory`.
3. On the "Install connector" screen choose **Docker** and copy only the **token** (the long
   string after `--token`).
4. Put it in `.env`:
   ```
   TUNNEL_TOKEN=eyJhIjoi...
   ```
5. In the tunnel's **Public Hostname** tab add:
   - Subdomain: `inventar`, Domain: `yourparish.sk`
   - Service: **HTTP**, URL: `app:8000`

## 2. Start it

```bash
docker compose --profile tunnel up -d
```

## 3. Tell the app its address

In `.env`:

```
BASE_URL=https://inventar.yourparish.sk
SECURE_COOKIES=1
```

then `docker compose up -d` again. QR codes, emails and calendar links now use the public address.

## Optional: an extra lock with Cloudflare Access

In **Access → Applications** you can add a rule so only people with an email address you list
(or from your parish domain) reach the login page at all. They confirm with a one-time code by
email. This is a good second layer for an app that holds audit evidence.

**Important:** if you use Access, add a **Bypass** policy for the path `/calendar/*`. Otherwise
Google/Outlook can't fetch the calendar feeds. Those links are already protected by their own
secret token.

## Without Docker for cloudflared

If you prefer to install `cloudflared` directly on the device, use **Service URL
`http://localhost:8000`** instead of `app:8000`.
