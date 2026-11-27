# Private access with Tailscale

Tailscale builds a private network between your devices. The app is **not on the public
internet at all**, and only people you invite can reach it. Each person installs the Tailscale
app (Android, iPhone, Windows, macOS) and logs in once. The free plan covers small teams.

## 1. Install on the device running the app

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Open the link it prints and log in. The device appears at <https://login.tailscale.com/admin/machines>.
Rename it to e.g. `kostol`.

## 2. HTTPS address (recommended)

In the admin console enable **DNS → MagicDNS** and **HTTPS Certificates**. Then on the device:

```bash
sudo tailscale serve --bg 8000
```

The app is now at `https://kostol.<your-tailnet>.ts.net`. Put that in `.env`:

```
BASE_URL=https://kostol.<your-tailnet>.ts.net
SECURE_COOKIES=1
```

and run `docker compose up -d`.

## 3. Invite people

Admin console → **Users → Invite users**. They install Tailscale on their phone, accept, and
open the address above.

## Calendar feeds and Tailscale

Google Calendar and Outlook.com fetch subscribed calendars **from their own servers**, which are
not in your Tailscale network, so they can't reach the feed. Phone calendar apps (iPhone,
Android with a CalDAV/ICS app) fetch from the phone itself and work while Tailscale is on.
If Google Calendar sync matters, use Cloudflare Tunnel (optionally with Access + a bypass for
`/calendar/*`), or expose only the calendar path with `tailscale funnel`.
