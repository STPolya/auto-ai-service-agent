# Auto AI Service Agent

An extensible portfolio project for an AI-powered customer support and booking
assistant for the fictional AutoCare car service. The project is being developed
incrementally.

## Current MVP

A minimal asynchronous Telegram bot built with aiogram 3 and python-dotenv.
It runs locally using long polling and responds to `/start` with a welcome
message and five reply keyboard buttons:

- 🤖 Описать проблему
- 📅 Записаться на сервис
- 🔧 Услуги и цены
- 📋 Мои записи
- 🚗 Мои автомобили

`/start` and all five menu buttons work, including the conversational
`🤖 Описать проблему` AI assistant.

The database foundation uses PostgreSQL hosted on Supabase, SQLAlchemy 2 typed
models, psycopg 3, and Alembic migrations. Supabase is used only as PostgreSQL;
there is no Supabase SDK. The schema contains users, vehicles, services,
appointments, conversations/messages, and knowledge chunks. On `/start`, the bot creates or updates the sender's user profile
by Telegram ID before displaying the welcome message and menu. The service
catalog reads active services from PostgreSQL. Users can list and add their own
vehicles in private chat, book a service, and view upcoming appointments.

## Planned future features

- Further improvements to multi-turn AI consultation
- Real appointment capacity and mechanic availability
- Hybrid/pgvector retrieval for the existing lexical knowledge base
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

The `🔧 Услуги и цены` button calls `app/services/service_catalog.py` in a
worker thread using `asyncio.to_thread`. It loads active services ordered by ID,
closes the session, and displays each service's name, EUR price, duration, and
optional description. Prices retain Decimal precision and show two decimal
places; missing prices show `Цена по запросу`. Empty catalogs and database
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
`🔧 Услуги и цены`. Automated seed tests use an isolated in-memory SQLite
database; they never insert services into Supabase.

### Vehicle management

In a private chat, `🚗 Мои автомобили` lists only the sender's vehicles, ordered by
ID, and provides an inline `➕ Добавить автомобиль` button even when the list is empty.
The add flow asks for brand, model, year, and optional license plate. Use `/skip`
at the plate step or `/cancel` at any step. Brand/model are trimmed and must be
1–100 characters. Year must be a four-digit integer between 1886 and the current
UTC year plus one. Plate text is trimmed, limited to 64 characters, and has no
country-specific validation.

Draft values live only in aiogram's in-memory FSM (`brand`, `model`, `year`,
`license_plate`); restarting the bot discards unfinished forms. `/cancel` clears
the draft without saving. Opening Мои автомобили also clears an unfinished form.
During entry, finish the current step or use `/cancel` before navigating away.
The dispatcher serializes updates per conversation so repeated final replies
cannot save the same draft twice. Saving clears the draft before database work;
on failure, check Мои автомобили before starting again because a connection failure
can leave the commit result uncertain. Completed vehicles remain in PostgreSQL.

The vehicle service reuses `sync_user` to ensure the sender exists, resolves the
owner by Telegram ID, and never accepts a database user ID from Telegram. All
database work runs through `asyncio.to_thread`. No new migration is required.

Manual verification (writes your vehicle to the configured database):

1. Run the offline tests and start the bot using the commands above.
2. Send `/start`, press `🚗 Мои автомобили`, then `➕ Добавить автомобиль`.
3. Enter `Toyota`, `Corolla`, an invalid year to check validation, then `2020`.
4. Send `/skip` or enter a plate. Confirm the saved vehicle appears in Мои автомобили.
5. Start another entry and send `/cancel`; verify no additional vehicle appears.
6. From a different Telegram account, verify the first account's vehicles are hidden.

Automated vehicle tests use an isolated in-memory database and mocked Telegram
responses; they never create vehicles in Supabase.

### Appointment booking

In private chat, press `📅 Записаться на сервис`, select one of your vehicles, then an
active service. If you have no vehicles, the bot offers the existing add-vehicle
flow. Enter a date as `DD.MM.YYYY`, choose a time using inline buttons, review the
vehicle/service/price/date/time summary, and press `✅ Подтвердить`. An Appointment
is inserted only after confirmation, with status `scheduled` and no problem
description. `/cancel` during booking or `❌ Отменить` on the review screen clears
the draft without saving. Vehicle `/cancel` continues to work independently.

Dates and times use the explicit `Europe/Moscow` timezone via standard-library
`zoneinfo`, centralized in `app/services/booking_rules.py`; the display label is
`МСК`. Dates must be real, today or later,
and no more than 90 days ahead (inclusive). Starts are allowed every 30 minutes
from 09:00 through 17:30; 18:00 is closing time, not a valid start. Today's starts
must still be in the future. The same rules are checked again on confirmation.
These MVP rules apply every day and constrain start times only; they do not
model weekends, holidays, service-duration capacity, or mechanic schedules.
Real availability is a future feature. The customer interface uses neutral time
options and says a manager will contact the customer to confirm the booking and
details; it does not display technical capacity/MVP disclaimers.

The time keyboard has 18 half-hour options, excluding passed times for today.
If no times remain today, the FSM returns to the date step. A `📅 Другая дата`
button also lets the user change dates. Every time callback is validated again;
changing the date rotates the draft token so earlier buttons cannot select a
time for a different date. Typed times do not advance the conversation.

`📋 Мои записи` shows only the sender's upcoming appointments, ordered by
date/time and then ID, with vehicle, service, Moscow date/time, and status.
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
error the draft is cleared, and the bot asks users to check Мои записи
before retrying because a connection failure can make the commit outcome unclear.

Manual verification after running the offline tests:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q app scripts tests main.py
.\.venv\Scripts\python.exe main.py
```

1. Send `/start` and ensure your account has a vehicle and the catalog has an active service.
2. Press `📅 Записаться на сервис` and select your vehicle and a service.
3. Enter an upcoming date within 90 days and select `14:30` using its button.
4. Review and cancel once; verify Мои записи has no new entry.
5. Repeat and confirm; verify one entry in `📋 Мои записи` at the Moscow time.
6. Check from another Telegram account that the vehicle and appointment remain private.

Manual confirmation writes to the configured database. Automated tests use only
isolated SQLite databases and mocks with fixed dates; they never create
appointments in Supabase. Real PostgreSQL connectivity is not exercised by them.

### Gemini diagnostic assistant

Press `🤖 Описать проблему` in a private chat and describe the symptoms in one
text message (1–3000 characters). The description is sent to the Gemini API.
The response is in Russian, with `Возможные причины`, `Вопросы`, and
`Рекомендация` sections. Afterwards the bot offers the existing booking menu;
it never creates an appointment automatically. `/cancel` while waiting for a
description clears the diagnostic FSM without making an API call.

Set `GEMINI_API_KEY` privately in your existing `.env` or environment. The key
is loaded through `get_gemini_api_key()` in the existing settings module only
when diagnostics are requested. Do not overwrite your existing `.env` with the
example, and never commit or share the key. Without this variable the other
features still work; diagnostic requests receive a friendly unavailable message.

The new pinned dependency `google-genai==2.24.0` is Google's official Gemini
Python SDK. See the [SDK documentation](https://googleapis.github.io/python-genai/).
The client uses concrete Gemini models: `gemini-3.8-flash` as primary and
`gemini-3.5-flash` as fallback, without moving aliases. Model names are centralized in
`app/ai/client.py`. Each call uses a context-managed synchronous client with a
30-second HTTP timeout per attempt and a bounded JSON response. No tools or chat sessions
are enabled. `asyncio.to_thread()` keeps SDK work off the Telegram event loop.

Temporary API status codes 408, 429, 500, 502, 503, and 504 trigger at most two
additional attempts, after 1 and 2 seconds. SDK retries are explicitly disabled
to prevent nested retries. Only a final HTTP 503 after primary retries activates
the fallback, which uses the same retry policy and request configuration.
Authentication, invalid requests, transport errors,
and invalid output are not retried. Warning logs contain only fixed failure
categories, numeric HTTP status when available, and attempt counts; exception
text, keys, and request contents are omitted. Persistent outages still produce
the existing friendly unavailable response.

The small JSON schema is retained to keep the three Russian sections predictable
and bounded. Invalid or empty output fails safely without model fallback;
the schema is not a guarantee of diagnostic accuracy or provider availability.

Flow: Telegram handler → conversation service → PostgreSQL history → AI service
→ Gemini client → Gemini API. The system
prompt and response schema live in `app/ai/prompts.py`. The service validates
the response and formats short Russian sections as plain text. The prompt asks
for possible causes and clarifying questions, avoids certainty and dangerous
mechanical instructions, and prioritizes professional help for brakes, steering,
smoke, or fuel leaks and emergency help for immediate danger. AI output is
preliminary guidance, not a guaranteed diagnosis; prompt rules are not a formal
safety guarantee.

Diagnostic conversations and user/assistant messages are persisted in PostgreSQL,
the source of truth. Each press of `🤖 Описать проблему` creates a new conversation;
follow-up messages reuse its ID in the active FSM. `/cancel`, navigation to another
section, or a process restart clears the active conversation without deleting history.
There is no automatic resumption. Recent context is bounded by
`MAX_CONTEXT_MESSAGES = 10`: the latest persisted entries are sent oldest first,
including the current question exactly once. Gemini receives native user/model
turns; no SDK chat session is stored. Ownership is checked on every read/write.

The user message is committed before calling Gemini. Only a successful, validated
assistant answer is saved afterwards. Provider failure preserves the question,
sends the existing friendly Russian error, and keeps the conversation usable.
Error messages are never saved as assistant advice. Short database transactions
and provider work run in a worker thread; no transaction stays open during Gemini
I/O. There is no vector database, voice, or fine-tuning.

Review `alembic/versions/20260920_0002_conversation_history.py` (revision
`20260920_0002`) before manually applying migrations with the command above.
It adds `conversations` and `messages`; no migration is applied automatically.

Install and verify locally:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q app scripts tests main.py
.\.venv\Scripts\python.exe main.py
```

After setting your key locally, press `🤖 Описать проблему`, send a car symptom,
and check the three Russian sections and booking suggestion. Answer a follow-up
question directly and verify that the previous symptoms remain in context.
Send `/cancel`, then reopen diagnostics to start a separate conversation.
Manual diagnostic requests call
Gemini and may consume API quota; automated tests mock the SDK and never call
Gemini or Supabase. Review and manually apply the conversation migration before
using this feature.

### AutoCare knowledge base (lexical RAG)

Flow: Telegram → conversation service → recent conversation history + RAG retriever
→ PostgreSQL knowledge base → AI service → Gemini. Telegram handlers contain no
retrieval logic. Conversation history and reference knowledge remain separate;
retrieved chunks are never inserted into conversation messages.

Original Russian documents live in `knowledge_base/`. Markdown headings define
chunks, whitespace is normalized, and long sections split deterministically at
word boundaries (maximum 1800 characters). Filename, title/section and category
(filename stem) are preserved. Ingestion is explicit; bot startup never loads data.

After reviewing and manually applying revision `20260920_0003`
(`alembic/versions/20260920_0003_knowledge_chunks.py`), ingest the configured database:

```powershell
.\.venv\Scripts\python.exe -m scripts.ingest_knowledge
```

This command writes to the database selected by `DATABASE_URL`. It synchronizes
each supplied source atomically: unchanged sources retain IDs and active flags;
changed sources replace their old chunks and become active. An empty source clears
its chunks. Missing files leave their existing database sources untouched (to retire
a source, supply an empty file explicitly). Concurrent manual ingestions serialize
with a PostgreSQL transaction lock. No ingestion or migration runs automatically.

Retrieval uses Russian PostgreSQL full-text search over title and content, a partial
GIN index for active chunks, and a source index for ingestion. The current user
message supplies the query; punctuation is removed, words are joined with OR for
recall, and PostgreSQL performs stemming/stop-word handling. Matching chunks sort
by `ts_rank_cd` descending, then ID ascending. `MAX_RAG_CHUNKS = 4` caps results;
the separate conversation window stays at 10 messages. Lexical search does not
understand all synonyms; a future pgvector/hybrid retriever can replace or augment
this boundary without changing Telegram handlers. No embeddings are implemented.
See the [PostgreSQL text-search documentation](https://www.postgresql.org/docs/17/textsearch-controls.html).

The AI receives reference items once, separately from user/model conversation turns.
Prompt rules treat them as reference data rather than instructions, prefer supplied
AutoCare facts, preserve safety guidance, and avoid exposing retrieval internals.
No matches is normal. Database/search failure logs a fixed sanitized warning and
continues without knowledge; unrelated programming errors are not silently hidden.

Tests use SQLite for ingestion, mocks for providers, and PostgreSQL SQL compilation.
When local PostgreSQL server binaries are installed, `test_rag_postgresql.py` also
creates a disposable loopback-only cluster to verify Russian stemming, ranking,
active filtering, limits and ingestion idempotency. It never reads `DATABASE_URL`
or `.env`, never uses Supabase, and stops/removes its temporary cluster afterwards.
Without those binaries only these local-server tests are skipped.

### Russian Telegram UI and welcome image

All application-owned Telegram UI is in Russian: menus, prompts, validation,
cancellation, empty/error states, booking review, and success messages. Database
status values remain unchanged; presentation maps them to Russian labels.

The five original English seed names/descriptions are translated for display in
`app/bot/presentation.py`, reused by the catalog, booking, and appointment list.
The seed script and persisted rows remain unchanged, preserving their unique
names and preventing duplicates. **No reseeding or data migration is needed.**
Custom database names/descriptions are preserved rather than guessed; enter any
future custom catalog content in Russian or extend the explicit presentation map.

Optionally place your own welcome image at `assets/welcome.png` in the project
root. `WELCOME_IMAGE_PATH` in `app/bot/handlers/start.py` resolves it independently
of the working directory. `/start` sends the image with the complete Russian
welcome text as its caption and the main menu, without a second copy of the text.
If the file is absent, `/start` sends the same text and menu normally. No image
has been generated, downloaded, or supplied by the application.

Existing PostgreSQL timestamps remain the same instants; they are now displayed
in Moscow time rather than Amsterdam time. No stored appointments are rewritten.
