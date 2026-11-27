# Bol Ke Order

**Voice-first ordering for India's kirana shops.** A customer sends a voice note in Hindi, Hinglish, Kannada,
Tamil or English, or taps items from photos. The shop owner gets a priced order slip from their own rate list,
approves it with one tap, and the customer hears a reply in their own language. Udhaar (credit) goes into a
digital khata automatically.

Built for the Scaler × Wispr Flow hackathon "India Runs on Voice" (Small business track).

## What it does

| For the customer (`/order`) | For the shop owner (`/`) |
|---|---|
| Tap the mic and speak the order, with a language picker | Order slips with items, prices, total, delivery time and credit |
| Or tap items from a photo grid with − / + and a cart | Answer questions, approve swaps, or **Add & use** an unlisted item |
| Answer the shop's question by tapping a photo ("Fortune or Saffola?") | Accept & reply: a voice reply in the customer's language |
| "Same as my usual list" | Packing checklist (saved), Packed → Delivered |
| Hears the shop's reply | Khata (who owes what), Stock & prices, 📷 item photos |
| | 🔊 Spoken daily summary (Hindi, Kannada, English) |

Plus a landing page at `/welcome`. Both apps install on a phone as a home-screen app (PWA).

## Rules the code enforces

- The AI only reads the customer's words. **Prices, items and money always come from the shop's rate list.**
- Vague words ("woh wala tel") use the customer's last order, or become a question. Nothing is guessed.
- Nothing reaches the customer until the owner taps **Ask customer** or **Accept & reply**.
- Orders with open questions, or with no items, can't be accepted. Accepting twice never charges credit twice.
- A customer can only pick from the options the shop offered, and only on their own order.

## Run it

```bash
pip install -r requirements.txt
export NVIDIA_API_KEY=...   # free: https://build.nvidia.com
export GROQ_API_KEY=...     # free: https://console.groq.com
python server.py            # http://127.0.0.1:8790
```

- Shop dashboard: http://127.0.0.1:8790/
- Customer page: http://127.0.0.1:8790/order
- Landing page: http://127.0.0.1:8790/welcome

On Windows PowerShell use `$env:NVIDIA_API_KEY="..."`. Without a Groq key, speech falls back to offline
faster-whisper (`small` model, which must already be downloaded). The microphone and the phone install
need HTTPS (or localhost).

## How it works

| Step | Module | Notes |
|---|---|---|
| Audio → text | `bko/ears.py` | Groq whisper-large-v3 first, offline faster-whisper as backup |
| Text → order lines | `bko/brain.py` | NVIDIA Nemotron; handles self-corrections and number words in 4 languages |
| Lines → priced slip | `bko/slip.py`, `bko/catalog.py` | Rate-list prices only; history, questions, swaps; tap-to-order without AI |
| Storage | `bko/store.py` | SQLite: orders, customers, khata ledger, packing ticks |
| Replies | `bko/mouth.py` | Owner-approved text + Edge TTS voice; spoken daily summary |
| Web + phone app | `server.py`, `static/` | FastAPI, `{ok, data, error}` responses, PWA manifests + service worker |

## Tests

```bash
python -m pytest tests -q     # 103 tests
```

Tests use their own copy of the shop data (`tests/fixtures/`), so editing the live rate list can't break them.
`scripts/make_samples.py` makes synthetic sample voice notes and `scripts/make_icons.py` draws the app icons.

## Status (27 Nov 2026)

Working and tested in the browser: voice and tap ordering, questions with photo choices, owner approval,
voice replies, khata, stock and photos, the spoken summary, and the phone-installable app.
Not built yet: WhatsApp connection, login for multiple shops, HTTPS hosting, and a pilot with real shops.
Most voice tests so far used computer-generated voices. Prices in `data/rate_list.json` are demo values.
