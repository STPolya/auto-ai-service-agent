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

The database foundation uses PostgreSQL hosted on Supabase, SQLAlchemy 2 typed
models, psycopg 3, and Alembic migrations. Supabase is used only as PostgreSQL;
there is no Supabase SDK. The schema contains users, vehicles, services, and
appointments. On `/start`, the bot creates or updates the sender's user profile
by Telegram ID before displaying the welcome message and menu. Other tables
are not used by bot flows yet.

## Planned future features

- AI car issue consultation
- Appointment booking
- Gemini integration
- RAG knowledge base
- Human handoff
- CRM/admin dashboard
- Voice messages
- Analytics

These future features are not implemented yet.

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
If `.env` already exists, keep it; do not overwrite it with the example.

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
asynchronous `handle_start` handler. The handler calls
`app/services/user_service.py` through `asyncio.to_thread`, then sends the welcome
message with the reply keyboard from `app/bot/keyboards/main_menu.py`.
The service inserts missing users using PostgreSQL conflict handling, locks the
matching row, and updates changed profile fields in one transaction. The unique
Telegram ID index prevents duplicate users. Missing usernames and first names
are stored as null. Database failures produce a friendly retry message and a
server log without exception details; the welcome message is sent only after
synchronization succeeds.

Change `WELCOME_MESSAGE` in the start handler to edit the greeting and
`MENU_LABELS` in the keyboard module to edit button text. Add future handlers as
separate router modules and register them in `main.py`.

## Database development

Set `DATABASE_URL` privately in your existing `.env` or environment using the
PostgreSQL connection URL supplied by Supabase. The example file deliberately
contains empty placeholders. Standard `postgresql://` URLs and explicit
`postgresql+psycopg://` URLs both select psycopg 3. URL-encode special characters
in passwords and retain the provider's TLS connection parameters. Never print
the URL or include it in `alembic.ini`.

Database configuration is required only when creating an engine/session factory
or running migrations. Imports work without `DATABASE_URL`, but `/start` now
requires a configured, reachable database with the initial migration applied.
No tables are created automatically at startup.

Install dependencies, inspect the migration history, and run offline checks:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m alembic heads
.\.venv\Scripts\python.exe -m alembic history --verbose
.\.venv\Scripts\python.exe -m alembic show head
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The tests disable `.env` loading, compile PostgreSQL migration SQL in memory,
load Alembic metadata offline, and compare the migration with the models using
an in-memory SQLite database. User service and handler tests verify profile
synchronization, duplicate prevention, rollback, worker-thread execution, and
safe failure responses. They do not connect to Supabase. SQLite checks do
not verify PostgreSQL permissions, connectivity, or actual server behavior.

Review `alembic/versions/20260919_0001_initial_schema.py` before applying it.
Only after review, point `DATABASE_URL` at the intended development database and
apply the migration yourself (this command changes that database):

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

For future schema changes, `alembic revision --autogenerate -m "describe change"`
compares the registered models with a live database; review every generated
migration. All models are registered through `app/database/models.py` and
`Base.metadata` in `alembic/env.py`.

### Sessions

`get_session_factory()` in `app/database/session.py` lazily caches an engine and
session factory. Creating them does not connect; the first database operation
checks out a connection. Use one session per unit of work:

```python
from app.database.session import get_session_factory

SessionFactory = get_session_factory()
with SessionFactory.begin() as session:
    # Future database operations belong here.
    pass
```

The context commits on success, rolls back on failure, and closes the session,
returning its connection to the pool. `with SessionFactory() as session` only
closes the session; writes require an explicit commit. Do not share sessions
between threads or tasks. The `/start` handler uses `asyncio.to_thread` so each
synchronous user transaction runs outside the async event loop. Call `get_engine().dispose()` during
shutdown of a future component that uses the database.

SQL echo is disabled and SQL parameter values are hidden in SQLAlchemy errors.
Alembic suppresses driver error details. Future callers should not log raw
connection objects, URLs, or driver exceptions, which can expose connection
details. No application database logging is configured.

### Python compatibility

Use Python 3.10+ with the pinned dependencies. `psycopg[binary]` supplies the
driver and libpq without requiring a local compiler or PostgreSQL installation.
Python 3.14 requires compatible binary wheels for your OS/architecture; if pip
cannot find one, use a supported standard CPython build or Python 3.13. The
offline checks can be run on your interpreter before configuring a database.

### Verify user synchronization locally

After the offline tests pass, start the bot with
`.\.venv\Scripts\python.exe main.py` and send `/start` twice in Telegram.
Both requests should show the existing welcome/menu; the configured database
should contain one user row for your Telegram ID. Change your Telegram profile
and send `/start` again to verify the same row is updated. This manual check
writes your actual profile to the configured database; the automated tests do not.
No new migration is needed for this integration.
