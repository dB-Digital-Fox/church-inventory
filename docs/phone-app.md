# Phone app (quick check, works offline)

The app can be installed on a phone like a normal app. It's a web app (PWA), so it comes from your own server and there's no app store and nothing to pay.

## Install it

1. On the phone, open your Church Inventory address and log in. Use the address through Cloudflare Tunnel or Tailscale, so it's `https://`. The camera and offline mode need a secure connection.
2. **Android (Chrome):** menu ⋮ → **Install app** or **Add to Home screen**.
   **iPhone (Safari):** Share → **Add to Home Screen**.
3. The icon opens **Quick check** directly. The full app is in the ☰ menu.

## Record a check

1. Tap **Scan QR label** and point the camera at the label. If the camera isn't available, type the number printed under the QR code (#12).
2. Check the condition and the location, and add a note if something is wrong.
3. Tap **Save check**.

Each check is written to the item's page (**Checks**) and to the audit log. If the condition or location changed, the item is updated too, and the change shows in its history as before → after.

## Without signal

Cellars, attics and bell towers often have no signal. That's fine:

- The quick-check page and the list of items are kept on the phone, so scanning and saving still work.
- Saved checks wait under **Waiting to be sent**. They go out by themselves as soon as the phone is online again, or when you tap **Send now**.
- Each check has its own id, so a check is never recorded twice, even if the connection drops in the middle.
- The time recorded is when you saved the check on the phone, not when it was sent.
- If your login expires while you're offline, nothing is lost. Log in again and the waiting checks are sent.

Other pages always load from the server. Nothing else is stored on the phone. Logging out clears the stored page and item list. On a shared phone, checks that are still waiting belong to the person who saved them and are sent only after that person logs in.

## The yearly stocktaking

1. Print labels for everything: **Reports → Inventory list → QR labels for everything**.
2. Volunteers walk through the church, hall and storage rooms and scan each label.
3. Download **Reports → Inventory list → Excel**. The *Last checked* column shows what was seen this year and by whom; items without a date are the ones to look for.

Auditors can open the app but cannot record checks.
