# Bolashak 2026 monitor 🤖🎓

Watches the Bolashak scholarship-recipients page, and when a new list appears it
**downloads the PDF, searches it for your name, and tells you on Telegram if you
won**.

- Page being watched: <https://bolashak.gov.kz/ru/o-stipendii/obladateli-stipendii>
- Runs in the cloud on **GitHub Actions** (free) every 3 hours — works even when
  your laptop is off.
- One dependency only: `pypdf` (for reading the PDFs).

---

## How it works

`monitor.py` downloads the page, extracts every "Обладатели стипендии от … года"
link, and compares it to the last known set stored in `state.json`. When a new
list appears it downloads that PDF, reads its text, and searches for your name
(**surname + first name as adjacent whole words**, e.g. `Аманбай Алмас`). Then it
messages you via the Telegram Bot API:

- 🎉🎉🎉 **CONGRATULATIONS, YOU WON!** — your name was found, with the exact line
  from the list (e.g. `72. Аманбай Алмас Маратұлы`) and the PDF link.
- 🎓 **New 2026 list published** — but your name was not found in it.
- 📄 **New list added** — a non-2026 list appeared (your name not found).

The matching is strict and matches **only `Аманбай Алмас`** — your surname
immediately followed by your first name. It will **not** be fooled by:

- a patronymic like `Алмасовна`,
- a different person whose first name is `Алмас` (e.g. `Жумабеков Алмас`),
- a different person who shares your surname (e.g. `Аманбай Нурлан`),
- reversed word order (`Алмас Аманбай`).

The first run just records what's already there and sends a one-time
"monitor started" confirmation — it does **not** spam you with the old lists.

---

## Setup (about 10 minutes)

### 1. Create your Telegram bot and get the token

1. In Telegram, open a chat with **[@BotFather](https://t.me/BotFather)**.
2. Send `/newbot`.
3. Give it a **name** (e.g. `Bolashak Watch`) and a **username** ending in `bot`
   (e.g. `bolashak_watch_bot`).
4. BotFather replies with a token like
   `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`. **Copy it** — this is your
   `TELEGRAM_BOT_TOKEN`.

### 2. Get your chat ID

1. Open a chat with your new bot and tap **Start** (or send it any message like
   `hi`). This is required — bots can't message you until you message them first.
2. Easiest way: open a chat with **[@userinfobot](https://t.me/userinfobot)** and
   it replies with your numeric **Id** — that's your `TELEGRAM_CHAT_ID`.
3. Alternative: visit this URL in a browser (paste your token in):
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
   and read `result[].message.chat.id` from the JSON.

### 3. Put this project on GitHub

Create a new (private is fine) repository and push these files:

```bash
git init
git add .
git commit -m "Bolashak 2026 monitor"
git branch -M main
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

### 4. Add your secrets

In the GitHub repo: **Settings → Secrets and variables → Actions → New
repository secret**. Add two:

| Name                  | Value                              |
| --------------------- | ---------------------------------- |
| `TELEGRAM_BOT_TOKEN`  | the token from BotFather           |
| `TELEGRAM_CHAT_ID`    | your numeric chat id               |

### 5. Turn it on and test

1. Go to the **Actions** tab. If prompted, click **"I understand my workflows,
   enable them"**.
2. Open **"Bolashak 2026 monitor"** → **Run workflow** (manual trigger).
3. You should get a Telegram message: *"✅ Bolashak monitor started…"*.

That's it. From now on it checks every 3 hours and pings you when a 2026 list
appears.

---

## Configuration

Set these as repo secrets (sensitive) or edit `TARGET_YEAR` in
[`.github/workflows/monitor.yml`](.github/workflows/monitor.yml):

| Variable             | Default        | Meaning                                            |
| -------------------- | -------------- | -------------------------------------------------- |
| `TELEGRAM_BOT_TOKEN` | —              | required                                           |
| `TELEGRAM_CHAT_ID`   | —              | required                                           |
| `TARGET_YEAR`        | `2026`         | the year to highlight                              |
| `WATCH_SURNAME`      | `Аманбай`      | surname to search for in each new PDF              |
| `WATCH_FIRSTNAME`    | `Алмас`        | first name to search for                           |
| `NOTIFY_ANY_NEW`     | `1`            | also notify about non-target new lists (`0` = off) |
| `STATE_FILE`         | `state.json`   | where the snapshot is stored                       |

**Change the name it searches for:** edit `WATCH_SURNAME` / `WATCH_FIRSTNAME` in
[`.github/workflows/monitor.yml`](.github/workflows/monitor.yml).

**Change how often it checks:** edit the `cron` line in the workflow. It uses
UTC. Examples: `0 */6 * * *` = every 6h, `0 8 * * *` = once daily at 08:00 UTC.

---

## Run it locally (optional)

```powershell
pip install -r requirements.txt
$env:PYTHONUTF8 = "1"          # so Cyrillic prints correctly on Windows
$env:TELEGRAM_BOT_TOKEN = "123:abc"
$env:TELEGRAM_CHAT_ID = "111222333"
python monitor.py            # one check
python monitor.py --test     # just send a test message
```

Without the two env vars, the script prints the messages to the console instead
of sending them — handy for trying it out.

---

## Notes

- **GitHub disables scheduled workflows after 60 days with no repo commits.**
  This monitor commits `state.json` (with a daily timestamp) on its first run
  each day, which counts as activity and keeps the schedule alive.
- Scheduled runs are best-effort and can be delayed by a few minutes under load.
- If the page's HTML layout changes and the script suddenly parses **0**
  entries, it logs a warning and leaves the saved state untouched rather than
  sending false alerts.
- Today's lists are normal text PDFs, so the name search works. If a future list
  is ever published as a **scanned image**, the bot can't read the text — it will
  still tell you a new list appeared and ask you to check it manually.
