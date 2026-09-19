# Auto AI Service Agent

An extensible portfolio project for an AI-powered customer support and booking
assistant for the fictional AutoCare car service. The project is being developed
incrementally.

## Current MVP

A minimal asynchronous Telegram bot built with aiogram 3 and python-dotenv.
It runs locally using long polling and responds to `/start` with a welcome
message and five reply keyboard buttons:

- 🤖 Describe a problem
- 📅 Book a service
- 🔧 Services & prices
- 🚗 My vehicles
- 📋 My appointments

`/start`, `🔧 Services & prices`, `🚗 My vehicles`, `📅 Book a service`, and
`📋 My appointments` work. `🤖 Describe a problem` remains a placeholder.

The database foundation uses PostgreSQL hosted on Supabase, SQLAlchemy 2 typed
models, psycopg 3, and Alembic migrations. Supabase is used only as PostgreSQL;
there is no Supabase SDK. The schema contains users, vehicles, services, and
appointments. On `/start`, the bot creates or updates the sender's user profile
by Telegram ID before displaying the welcome message and menu. The service
catalog reads active services from PostgreSQL. Users can list and add their own
vehicles in private chat, book a service, and view upcoming appointments.

## Planned future features

- AI car issue consultation
- Real appointment capacity and mechanic availability
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

### Service catalog and demo seed

The `🔧 Services & prices` button calls `app/services/service_catalog.py` in a
worker thread using `asyncio.to_thread`. It loads active services ordered by ID,
closes the session, and displays each service's name, EUR price, duration, and
optional description. Prices retain Decimal precision and show two decimal
places; missing prices show `Price on request`. Empty catalogs and database
outages produce friendly responses without exposing exception details.

Review `scripts/seed_services.py`, then manually run this command from the
project root to insert the five fictional demo services into the database
configured by `DATABASE_URL`:

```powershell
.\.venv\Scripts\python.exe -m scripts.seed_services
```

This command writes to the configured database. It uses the existing session
factory and a single transaction. The unique service name constraint and
`ON CONFLICT DO NOTHING` prevent duplicates, including on repeated runs.
Existing rows, prices, descriptions, and active flags are preserved. Seeding
does not run when importing the module or starting the bot and is not part of
an Alembic migration. No new migration or dependency is needed.

Offline checks and local startup:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q app scripts tests main.py
.\.venv\Scripts\python.exe main.py
```

After manually seeding and starting the bot, send `/start` and press
`🔧 Services & prices`. Automated seed tests use an isolated in-memory SQLite
database; they never insert services into Supabase.

### Vehicle management

In a private chat, `🚗 My vehicles` lists only the sender's vehicles, ordered by
ID, and provides an inline `➕ Add vehicle` button even when the list is empty.
The add flow asks for brand, model, year, and optional license plate. Use `/skip`
at the plate step or `/cancel` at any step. Brand/model are trimmed and must be
1–100 characters. Year must be a four-digit integer between 1886 and the current
UTC year plus one. Plate text is trimmed, limited to 64 characters, and has no
country-specific validation.

Draft values live only in aiogram's in-memory FSM (`brand`, `model`, `year`,
`license_plate`); restarting the bot discards unfinished forms. `/cancel` clears
the draft without saving. Opening My vehicles also clears an unfinished form.
During entry, finish the current step or use `/cancel` before navigating away.
The dispatcher serializes updates per conversation so repeated final replies
cannot save the same draft twice. Saving clears the draft before database work;
on failure, check My vehicles before starting again because a connection failure
can leave the commit result uncertain. Completed vehicles remain in PostgreSQL.

The vehicle service reuses `sync_user` to ensure the sender exists, resolves the
owner by Telegram ID, and never accepts a database user ID from Telegram. All
database work runs through `asyncio.to_thread`. No new migration is required.

Manual verification (writes your vehicle to the configured database):

1. Run the offline tests and start the bot using the commands above.
2. Send `/start`, press `🚗 My vehicles`, then `➕ Add vehicle`.
3. Enter `Toyota`, `Corolla`, an invalid year to check validation, then `2020`.
4. Send `/skip` or enter a plate. Confirm the saved vehicle appears in My vehicles.
5. Start another entry and send `/cancel`; verify no additional vehicle appears.
6. From a different Telegram account, verify the first account's vehicles are hidden.

Automated vehicle tests use an isolated in-memory database and mocked Telegram
responses; they never create vehicles in Supabase.

### Appointment booking

In private chat, press `📅 Book a service`, select one of your vehicles, then an
active service. If you have no vehicles, the bot offers the existing add-vehicle
flow. Enter a date as `DD.MM.YYYY`, followed by a time as `HH:MM`, review the
vehicle/service/price/date/time summary, and press `✅ Confirm`. An Appointment
is inserted only after confirmation, with status `scheduled` and no problem
description. `/cancel` during booking or `❌ Cancel` on the review screen clears
the draft without saving. Vehicle `/cancel` continues to work independently.

Dates and times use the explicit `Europe/Amsterdam` timezone via standard-library
`zoneinfo`, including daylight-saving changes. Dates must be real, today or later,
and no more than 90 days ahead (inclusive). Starts are allowed every 30 minutes
from 09:00 through 17:30; 18:00 is closing time, not a valid start. Today's starts
must still be in the future. The same rules are checked again on confirmation.
These MVP rules apply every day and constrain start times only; they do not
model weekends, holidays, service-duration capacity, or mechanic schedules.
No free slot is guaranteed. Real availability is a future feature.

`📋 My appointments` shows only the sender's upcoming appointments, ordered by
date/time and then ID, with vehicle, service, Amsterdam date/time, and status.
Opening this list abandons any unfinished draft. Appointment cancellation and
rescheduling are not implemented.

The focused appointment service owns all database queries and verifies the
Telegram user's vehicle ownership and the service's active status on review and
again inside the save transaction. It can be reused by a future API without
Telegram types. Handlers run synchronous database operations in worker threads.
Drafts remain in memory; restarting the bot discards them. No migration is needed.

Each booking draft has a callback token. Stale tokens and out-of-order buttons
are rejected. Existing per-conversation event isolation plus clearing FSM state
before confirmation I/O prevents repeated confirm callbacks from saving the
same in-memory draft twice. This is not distributed or restart-safe idempotency;
stronger persistence/database guarantees will be needed later. After a save
error the draft is cleared, and the bot asks users to check My appointments
before retrying because a connection failure can make the commit outcome unclear.

Manual verification after running the offline tests:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q app scripts tests main.py
.\.venv\Scripts\python.exe main.py
```

1. Send `/start` and ensure your account has a vehicle and the catalog has an active service.
2. Press `📅 Book a service` and select your vehicle and a service.
3. Enter an upcoming date within 90 days, try `14:15` to check rejection, then `14:30`.
4. Review and cancel once; verify My appointments has no new entry.
5. Repeat and confirm; verify one entry in `📋 My appointments` at the Amsterdam time.
6. Check from another Telegram account that the vehicle and appointment remain private.

Manual confirmation writes to the configured database. Automated tests use only
isolated SQLite databases and mocks with fixed dates; they never create
appointments in Supabase. Real PostgreSQL connectivity is not exercised by them.
