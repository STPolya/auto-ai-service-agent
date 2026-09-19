# Auto AI Service Agent

An extensible portfolio project for an AI-powered customer support and booking
assistant for the fictional AutoCare car service. The project is being developed
incrementally.

## Current MVP

A minimal asynchronous Telegram bot built with aiogram 3 and python-dotenv.
It runs locally using long polling and responds to `/start` with a welcome
message and four reply keyboard buttons:

- 🤖 Describe a problem
- 📅 Book a service
- 🔧 Services & prices
- 📋 My appointments

The buttons are placeholders and do not have handlers yet. Only `/start` works.

## Planned future features

- AI car issue consultation
- PostgreSQL database
- Appointment booking
- Gemini integration
- RAG knowledge base
- Human handoff
- CRM/admin dashboard
- Voice messages
- Analytics

None of these future features are implemented in this step.

## Local setup

Use Python 3.10 or newer and create a Telegram bot through @BotFather to obtain
your own token. Run the following from the project root.

Windows PowerShell (activation is not required):

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

macOS / Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` locally and fill in `TELEGRAM_BOT_TOKEN` with your token. Never commit
this file or share the token. An existing environment variable takes precedence
over `.env`. Missing or malformed tokens cause startup to exit with a clear error.

Start the bot on Windows:

```powershell
.\.venv\Scripts\python.exe main.py
```

Or on macOS / Linux:

```bash
.venv/bin/python main.py
```

Open your bot in Telegram and send `/start`. Stop it with `Ctrl+C`.
An internet connection is required. Run only one polling process per bot token.

## Structure and routing

`main.py` loads the token through `app/config/settings.py`, creates the bot and
dispatcher, registers the start router, and starts long polling. The
`CommandStart()` filter in `app/bot/handlers/start.py` routes `/start` to the
asynchronous `handle_start` handler. The handler sends the welcome message with
the reply keyboard from `app/bot/keyboards/main_menu.py`.

Change `WELCOME_MESSAGE` in the start handler to edit the greeting and
`MENU_LABELS` in the keyboard module to edit button text. Add future handlers as
separate router modules and register them in `main.py`.
