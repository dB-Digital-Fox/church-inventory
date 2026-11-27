# WhatsApp notifications

The app uses Meta's official **WhatsApp Cloud API**. Unofficial "WhatsApp Web" bots break
WhatsApp's terms and get numbers banned, so they are not used.

## What it needs

- A **Meta Business account** (free; <https://business.facebook.com>). Business verification is
  needed for higher sending limits, but small volumes usually work before verification.
- A **phone number that is not already used in the normal WhatsApp app**. A cheap prepaid SIM
  works. It becomes the "church" sender.
- **Message templates**: WhatsApp allows a business to start a conversation only with a
  pre-approved template. The app needs just one.
- **Cost**: Meta charges per message for "utility" templates. In the EU it's a few cents per
  message (check Meta's current price list). For a parish this is usually well under a few euros a month.

## Setup

1. Go to <https://developers.facebook.com> → **My Apps → Create App** → type **Business** →
   add the **WhatsApp** product.
2. Under **WhatsApp → API Setup**, add your phone number and verify it by SMS. Note the
   **Phone number ID**.
3. Create a **permanent access token**: Business Settings → **System users** → add a system user
   (Admin) → **Generate token** for your app with the permissions `whatsapp_business_messaging`
   and `whatsapp_business_management`.
4. Create a template: **WhatsApp Manager → Message templates → Create**
   - Category: **Utility**
   - Name: `church_notice`
   - Language: Slovak (and/or English)
   - Body: `{{1}}`  (one variable; the app puts the whole notification text in it)
   Wait for approval (usually minutes).
5. In `.env`:
   ```
   WHATSAPP_TOKEN=EAAG...
   WHATSAPP_PHONE_NUMBER_ID=1234567890
   WHATSAPP_TEMPLATE=church_notice
   WHATSAPP_TEMPLATE_LANG=sk
   ```
6. `docker compose up -d`, then **Administration → Send me a test notification**.

Each person enters their number in **My profile** (international format, e.g. `+421 900 123 456`)
and ticks "Send me notifications on WhatsApp".

If you leave `WHATSAPP_TEMPLATE` empty, the app sends plain text messages. WhatsApp only
delivers those to people who messaged the church number in the last 24 hours, so a template is
strongly recommended.
