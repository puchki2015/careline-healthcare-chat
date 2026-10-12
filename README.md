# Careline

A modern, patient-centric healthcare app.

- **Frontend:** Vite + React (JavaScript)
- **Backend:** FastAPI (Python)
- **Database, Auth and Storage:** Supabase (Postgres, Auth, Storage)

**Phase 1** (this document): patient sign-up and login, view profile attributes, update an attribute by key, and delete the patient account entirely.
**Phase 2** (later): doctor availability and appointment booking.

---

## Table of contents

1. [Phase 1 scope](#1-phase-1-scope)
2. [Architecture](#2-architecture)
3. [Data model](#3-data-model)
4. [API design](#4-api-design)
5. [Project structure](#5-project-structure)
6. [Prerequisites](#6-prerequisites)
7. [Milestone 0: Setup](#milestone-0-setup)
8. [Milestone 1: Database and auth](#milestone-1-database-and-auth-supabase)
9. [Milestone 2: Backend](#milestone-2-backend-fastapi)
10. [Milestone 3: Frontend](#milestone-3-frontend-vite--react)
11. [Milestone 4: Testing](#milestone-4-testing)
12. [Milestone 5: Security and compliance review](#milestone-5-security-and-compliance-review)
13. [Milestone 6: Deployment and CI/CD](#milestone-6-deployment-and-cicd)
14. [Timeline](#timeline)
15. [Risks and open questions](#risks-and-open-questions)
16. [Phase 2 readiness](#phase-2-readiness)
17. [Troubleshooting](#troubleshooting)
18. [Project completion summary](#project-completion-summary)
19. [Architecture explained step by step](#architecture-explained-step-by-step)
20. [Interview preparation](#interview-preparation)

---

## 1. Phase 1 scope

**In scope**

- Patient sign-up, login, logout and session refresh (Supabase Auth)
- A chat-based patient experience powered by a LangGraph orchestrator
- Natural-language questions about the authenticated patient's profile
- Natural-language profile update requests with validation and explicit confirmation
- View patient attributes (for example email, phone)
- Update a single patient attribute by key
- Delete the patient account permanently

**Out of scope (Phase 2 and later)**

- Doctor entities, availability and appointment booking
- Notifications, payments, file uploads (unless you decide otherwise)

**Definition of done**

A patient can sign up, log in, view and edit their own profile, and permanently delete their account. Data isolation is enforced at both the API level and the database level (Row Level Security). The code is tested and deployable.

---

## 2. Architecture

For the implemented application, see the [detailed architecture diagrams and request flows](docs/architecture.md),
including frontend/backend/Supabase boundaries, the confirmation sequence, and all six chat tables.
The original overview below predates the administrator and appointment extensions.

```
React (Vite)  --supabase-js-->  Supabase Auth (signup / login / session)
     |
     +-- Bearer JWT -->  FastAPI chat endpoint --> LangGraph orchestrator
                              |                         |
                              |                         +--> patient tools
                              |                                  |
                              +--> deterministic services <------+
                                             |
                                             v
                                  Supabase Postgres (RLS) / Storage
```

Key decisions:

- **Auth is handled by Supabase on the frontend.** React uses `supabase-js` for sign-up, login, logout and session refresh. FastAPI never handles passwords.
- **FastAPI verifies every Supabase JWT through the project's JWKS endpoint** and uses the
  validated `sub` claim as the patient ID. Accepted algorithms are explicitly restricted to
  `ES256` and `RS256`.
- **The patient ID always comes from the token, never from the URL or request body.** This prevents one patient from reading or changing another patient's data.
- **Two database access modes:** normal reads and updates use the user's JWT, so Row Level Security (RLS) acts as a second safety net. Account deletion needs the **service-role key**, which stays on the backend only.
- **LangGraph orchestrates; deterministic services enforce rules.** The model may decide which
  patient tool is appropriate, but it never receives database credentials, chooses a patient ID or
  constructs unrestricted database operations.
- **Reads may execute immediately; writes require confirmation.** A natural-language update is
  parsed and validated first, shown back to the patient as a proposed change, and executed only
  after explicit approval. Permanent account deletion is not exposed as an autonomous model action.
- **Phase 2 groundwork:** a `patients` table keyed to `auth.users`, so `doctors` and `appointments` can be added later without refactoring.



In plain English
Suppose your architecture looks like this:
Patient
   │
   │ Login
   ▼
Supabase Auth
   │
   │ returns JWT access token
   ▼
Frontend / App
   │
   │ Authorization: Bearer <JWT>
   ▼
FastAPI Backend
   │
   ├── 1. Verify JWT signature using Supabase JWKS
   ├── 2. Check algorithm is ES256 or RS256
   ├── 3. Check token claims (expiry, etc.)
   └── 4. Read "sub"
             │
             ▼
        Patient ID

A JWT (JSON Web Token) is essentially a digitally signed credential. After a user authenticates with Supabase, the frontend receives an access token and sends it to FastAPI with requests, typically:
Authorization: Bearer eyJhbGciOi...

FastAPI should not simply decode that token and trust what it says. An attacker could create a fake token claiming to be another patient.
That is where the JWKS endpoint comes in. JWKS means JSON Web Key Set. Supabase publishes the public cryptographic keys associated with your project. FastAPI obtains the appropriate public key and uses it to verify that the JWT was genuinely signed by the expected Supabase authentication system.
Conceptually:
JWT says:
{
    "sub": "patient-123",
    "exp": 1791300000,
    ...
}

        ↓

FastAPI asks:

"Is this JWT's signature valid
using the trusted Supabase public key?"

        ↓

      YES
        ↓

Trust the validated claims

The important part of your sentence is "validated sub claim."
sub means subject. In a Supabase access token, it identifies the authenticated user. So after FastAPI has successfully verified the token, your application takes:
payload["sub"]


and treats that value as the patient's authenticated identity.
For example:
Verified JWT
────────────────────────────
sub:  9f2a8c1e-...
role: authenticated
exp:  1791300000
────────────────────────────
          │
          ▼
patient_id = "9f2a8c1e-..."

Then an endpoint might effectively behave like:
@app.get("/medical-records")def get_records(current_patient = Depends(authenticate)):    patient_id = current_patient["sub"]    return get_records_for(patient_id)


This is much safer than allowing the client to say:
GET /medical-records?patient_id=123

and blindly trusting 123. Instead, the backend derives the patient's identity from the cryptographically verified token.
What does ES256 / RS256 mean?
Those are JWT signature algorithms.
ES256 uses elliptic-curve cryptography, while RS256 uses RSA cryptography.
Your statement says:
Accepted algorithms are explicitly restricted to ES256 and RS256.

That means the backend doesn't accept whatever algorithm the incoming JWT happens to request. It has an allowlist such as:
algorithms = ["ES256", "RS256"]


So a token claiming to use an unexpected algorithm is rejected.
The whole sentence, rewritten simply
When a patient sends a request to FastAPI, FastAPI verifies that their Supabase access token is authentic using Supabase's published public keys. It only permits tokens signed with ES256 or RS256. Once the token is successfully verified, FastAPI takes the token's sub value as the authenticated user's ID and uses that ID as the patient ID.

One important nuance: signature verification alone isn't the entire authentication check. A robust implementation should also validate relevant claims such as token expiration (exp) and the expected issuer (iss), and where applicable audience (aud).
I can also create an image explaining the Supabase → JWT → JWKS → FastAPI → Patient ID authentication flow.

![alt text](<FastAPI JWT Verification Flow.png>)

is JWKS same as anon key?





 






No. JWKS and the Supabase anon key are different things.
Think of them this way:
Supabase Project
│
├── anon / publishable key
│      ↓
│   Used by your frontend to access Supabase
│
└── JWKS public keys
       ↓
    Used by FastAPI to VERIFY JWT signatures

The anon key (or newer publishable key) identifies/authorizes your client when calling Supabase APIs. It is designed to be used client-side, with actual data access controlled by things such as Row Level Security.
JWKS, on the other hand, is a set of public cryptographic keys. FastAPI can retrieve those keys from your Supabase project's JWKS endpoint and use them to answer:
"Was this JWT really signed by the authentication system I trust?"

So in your architecture:
Patient logs in
      ↓
Supabase Auth
      ↓
JWT access token
      ↓
Frontend sends JWT
      ↓
FastAPI
      │
      ├── fetches JWKS public key
      ├── verifies JWT signature
      ├── validates token
      └── extracts sub
               ↓
           patient_id

There is also a third Supabase credential you should keep distinct: the service-role/secret key. That is privileged and must stay on trusted backend infrastructure.
So the simple distinction is:
Anon/publishable key → client accesses Supabase
JWT → proves which authenticated user is making the request
JWKS → FastAPI verifies that JWT
Service-role/secret key → privileged backend access

---

## 3. Data model

### `patients` table

| Column | Type | Notes |
|---|---|---|
| `id` | uuid, PK | FK to `auth.users(id)`, `ON DELETE CASCADE` |
| `email` | text | Copied from auth at signup |
| `full_name` | text | |
| `phone` | text | Validated format (E.164 recommended) |
| `date_of_birth` | date | Must not be in the future |
| `gender` | text | |
| `address` | jsonb | line1, city, region, postal code, country |
| `emergency_contact` | jsonb | name, phone, relationship |
| `created_at` | timestamptz | Default `now()` |
| `updated_at` | timestamptz | Maintained by trigger |

### Database objects

- A trigger on `auth.users` insert that auto-creates the matching `patients` row
- An `updated_at` trigger
- RLS policies allow `SELECT` and whitelisted-column `UPDATE` only where `auth.uid() = id`
- No client-side `INSERT` or `DELETE`: the signup trigger creates profiles, and account
  deletion goes through the backend's Supabase Admin API

### Editable-field whitelist

Editable through the update endpoint: `full_name`, `phone`, `date_of_birth`, `gender`, `address`, `emergency_contact`.

Not editable through the update endpoint: `id`, `created_at`, `updated_at`, and `email`. Email changes should go through Supabase Auth (`supabase.auth.updateUser`), which triggers re-verification.

---

## 4. API design

Base path: `/api/v1`. All endpoints (except `/health`) require `Authorization: Bearer <access_token>`.

| # | Method and path | Purpose | Responses |
|---|---|---|---|
| 1 | `GET /patients/me` | Return all attributes. Optional `?fields=email,phone` returns a subset. | 200, 401, 404 |
| 2 | `PATCH /patients/me/attributes/{key}` with body `{"value": ...}` | Update one attribute by key. | 200, 400 (key not editable), 401, 422 (invalid value) |
| 3 | `DELETE /patients/me` | Permanently delete the patient. | 204, 401 |
| - | `GET /auth/verify` | Verify the supplied Supabase access token without returning identity details. | 200, 401 |
| - | `GET /health` | Health check for deployment. | 200 |
| - | `POST /chat/sessions` | Create a persistent conversation session. | 201, 401, 500, 503 |
| - | `GET /chat/sessions` | List the patient's recent sessions with bounded pagination. | 200, 400, 401, 500, 503 |
| - | `GET /chat/sessions/{session_id}/messages` | Read one owned session's ordered messages. | 200, 400, 401, 404, 500, 503 |
| - | `POST /chat/sessions/{session_id}/close` | Idempotently close an active session. | 200, 401, 404, 409, 500, 503 |
| - | `POST /chat` | Persist and run one LangGraph turn inside an active session. | 200, 400, 401, 404, 409, 422, 500, 503 |

Design notes:

- `/me` is used instead of `/patients/{id}` to remove a whole class of authorization bugs.
- Per-key validation lives in Pydantic: a mapping from each key to its validator or type (phone regex, date of birth not in the future, and so on).
- **Delete flow, in order:** (1) delete the patient's Storage files if any, (2) delete the auth user through the Supabase Admin API, which cascades to the `patients` row, (3) return 204.
- **Open decision:** hard delete versus soft delete with a retention window. Healthcare data often has legal retention rules. Phase 1 implements hard delete, structured so soft delete can replace it later.

---

## 5. Project structure

```
careline/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── deps.py              (auth, patient/admin clients, repositories)
│   │   ├── core/                (config.py, security.py, supabase.py)
│   │   ├── api/v1/              (auth, patients, chat, chat_sessions, health)
│   │   ├── agents/              (classifier, graph, prompts, pending actions)
│   │   ├── repositories/        (chat_history_repository.py)
│   │   ├── schemas/             (auth, patient, chat, chat_history)
│   │   └── services/            (patient, chat_history, chat_turn services)
│   ├── tests/
│   ├── pyproject.toml
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── lib/                 (supabaseClient.js, apiClient.js)
│   │   ├── auth/                (AuthProvider, ProtectedRoute)
│   │   ├── pages/               (Login, Signup, Profile)
│   │   ├── components/
│   │   └── hooks/               (usePatient.js)
│   └── .env.example
├── supabase/
│   ├── config.toml
│   └── migrations/              (SQL files)
├── .gitignore
└── README.md
```

---

## 6. Prerequisites

| Tool | Version | Check |
|---|---|---|
| Node.js | 20 or newer | `node -v` |
| npm | comes with Node | `npm -v` |
| Python | 3.11 or newer | `python --version` |
| Git | any recent | `git --version` |
| Supabase account | free tier is fine | https://supabase.com |

> **Windows tip:** if your project lives inside OneDrive, syncing can interfere with `node_modules/` and `.venv/`. If you hit odd file-lock errors, pause OneDrive sync or move the project to a non-synced folder such as `C:\dev\careline`.

---

# Milestone 0: Setup

**Goal:** a clean monorepo where the backend, frontend and Supabase CLI all run, with secrets kept out of Git.
**Estimated time:** about half a day.

Run all commands from the **repo root** (`careline/`) unless stated otherwise.

### Step 0.1: Create the monorepo

```bash
mkdir careline
cd careline
git init
mkdir backend frontend
```

### Step 0.2: Add a root `.gitignore`

Create `careline/.gitignore`:

```gitignore
# env and secrets
.env
.env.*
!.env.example

# python
__pycache__/
*.pyc
.venv/
.pytest_cache/
.ruff_cache/

# node
node_modules/
dist/

# editor / OS
.vscode/
.idea/
.DS_Store

# supabase local
supabase/.temp/
```

Make a first commit now so later changes are tracked:

```bash
git add .
git commit -m "chore: initial repo skeleton"
```

### Step 0.3: Create the Supabase project

1. Go to https://supabase.com, create an organization, and click **New project**.
2. Name it `careline-dev`.
3. Choose the region closest to your users. For Canada, a Canadian region such as `ca-central-1` also helps with data-residency questions later.
4. Save the database password in a password manager.
5. A separate `careline-prod` project can wait until Milestone 6.

### Step 0.4: Collect your keys

In the dashboard open **Project Settings, then API (or API Keys)**.

| Value | Used by | Exposed to the browser? |
|---|---|---|
| Project URL | frontend and backend | Yes |
| Anon / publishable key | frontend and backend | Yes (protected by RLS) |
| Service-role / secret key | **backend only** | **Never** |
| JWT verification info | backend | No |

Newer projects show "publishable" and "secret" keys. Older projects show "anon" and "service_role". They play the same roles.

For JWT verification, open **Project Settings, then JWT Keys**. This project uses asymmetric
signing keys, so the backend verifies tokens through the public JWKS endpoint:
`https://<project-ref>.supabase.co/auth/v1/.well-known/jwks.json`. No shared JWT secret is stored.

**Finding your project ref:** it is the last part of the dashboard URL (`https://supabase.com/dashboard/project/<project-ref>`), the part before `.supabase.co` in your Project URL, or **Project Settings, General, Project ID**.

### Step 0.5: Create the `.env.example` files

These are committed. Real `.env` files are not.

`backend/.env.example`:

```
SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=
FRONTEND_ORIGIN=http://localhost:5173
ENVIRONMENT=development
LOG_LEVEL=INFO
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.4-mini
CHAT_PENDING_ACTION_TTL_SECONDS=600
CHAT_PROMPT_VERSION=patient-router-v1
CHAT_CONTEXT_MESSAGE_LIMIT=20
CHAT_PRIOR_SESSION_LIMIT=3
CHAT_SUMMARY_MESSAGE_LIMIT=30
CHAT_SUMMARY_MAX_CHARS=2000
```

`frontend/.env.example`:

```
VITE_SUPABASE_URL=
VITE_SUPABASE_ANON_KEY=
VITE_API_BASE_URL=http://localhost:8000/api/v1
```

Copy each file to `.env` in the same folder and fill in real values.

> Only variables prefixed with `VITE_` are exposed to the browser. The service-role key must never get that prefix or appear anywhere inside `frontend/`.

### Step 0.6: Scaffold the backend

"Scaffold" means setting up the basic skeleton: folders, config files and a minimal runnable starting point.

```bash
cd backend
python -m venv .venv
```

Activate the virtual environment:

```bash
# macOS / Linux
source .venv/bin/activate

# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# Windows (cmd)
.venv\Scripts\activate
```

> If PowerShell blocks activation, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then try again.

Install dependencies:

```bash
pip install fastapi "uvicorn[standard]" pydantic-settings supabase "pyjwt[crypto]" httpx slowapi
pip install pytest pytest-asyncio ruff
pip freeze > requirements.txt
```

Create `backend/app/__init__.py` (empty) and `backend/app/main.py`:

```python
from fastapi import FastAPI

app = FastAPI(title="Careline API")


@app.get("/health")
def health():
    return {"status": "ok"}
```

Run it:

```bash
uvicorn app.main:app --reload
```

Open http://localhost:8000/docs to confirm the interactive API docs load.

Create `backend/pyproject.toml` to configure the linter (Ruff):

```toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]
```

What the settings mean:

- `line-length = 100`: maximum line width for the formatter and the linter.
- `target-version = "py311"`: tells Ruff you are writing for Python 3.11.
- `E` pycodestyle errors, `F` Pyflakes (real bugs such as unused imports), `I` import sorting, `B` flake8-bugbear (common pitfalls), `UP` pyupgrade (outdated syntax).

Use it:

```bash
ruff check .          # find problems (add --fix to auto-fix many)
ruff format .         # format code consistently (replaces Black)
```

### Step 0.7: Scaffold the frontend

From the repo root:

```bash
npm create vite@latest frontend -- --template react
cd frontend
npm install
npm install @supabase/supabase-js react-router-dom
npm install -D prettier eslint-config-prettier
```

If Vite warns that `frontend` is not empty, choose to ignore existing files or remove the empty folder first.

Create `frontend/.prettierrc`:

```json
{ "singleQuote": true, "semi": true, "printWidth": 100 }
```

Open `frontend/package.json` and add the `format` script inside the existing `"scripts"` block (Vite usually already provides `dev`, `build`, `lint` and `preview`):

```json
"scripts": {
  "dev": "vite",
  "build": "vite build",
  "lint": "eslint .",
  "format": "prettier --write src",
  "preview": "vite preview"
},
```

Every line needs a trailing comma except the last one. Do not duplicate keys that already exist.

Run it:

```bash
npm run dev
```

Confirm the page loads at http://localhost:5173.

### Step 0.8: Initialize the Supabase CLI

Run these from the **repo root**, not from `backend/` or `frontend/`.

```bash
npm install -D supabase       # or install globally (see below)
npx supabase init
npx supabase login
npx supabase link --project-ref <your-project-ref>
```

Notes:

- `npm install -D supabase` creates a small `package.json` at the repo root if there is none. That is fine. Commit it.
- Alternative global installs (then drop the `npx` prefix):
  - macOS / Linux: `brew install supabase/tap/supabase`
  - Windows: `scoop bucket add supabase https://github.com/supabase/scoop-bucket.git` then `scoop install supabase`
- `init` creates `supabase/config.toml`. If you created `supabase/migrations/` by hand, that is fine and will not conflict. Answer `n` if asked about Deno VS Code settings.
- `login` opens your browser to authenticate the CLI.
- `link` connects the local folder to `careline-dev`. It may ask for your database password.

You should end up with:

```
supabase/
├── config.toml
└── migrations/
```

### Step 0.9: Verify and commit

- [ ] `git status` shows no `.env` files tracked
- [ ] Backend `/health` returns `{"status": "ok"}` and `/docs` loads
- [ ] Frontend dev server loads at http://localhost:5173
- [ ] `ruff check .` passes in `backend/`, and `npm run lint` passes in `frontend/`
- [ ] `.env.example` exists in both `backend/` and `frontend/`
- [ ] `supabase link` succeeded
- [ ] The README steps work from a clean clone (try them in a fresh folder)

```bash
git add .
git commit -m "chore: initial monorepo setup"
```

Push to a **private** GitHub repository, because this project will hold health-related code and configuration.

### Milestone 0 pitfalls

- **Committed secrets:** if a service-role key ever lands in Git history, rotate it immediately in the Supabase dashboard. Deleting the file is not enough.
- **CORS confusion later:** the frontend runs on port 5173 and the backend on 8000. `FRONTEND_ORIGIN` must match exactly, including the port.
- **Wrong folder for CLI commands:** running `supabase init` inside `backend/` or `frontend/` creates a second `supabase/` folder in the wrong place.

---

# Milestone 1: Database and auth (Supabase)

**Goal:** the schema, triggers and security rules exist in version-controlled migrations, and are proven to isolate users.
**Estimated time:** about 1 day.

### Step 1.1: Configure authentication

- [ ] In the dashboard open **Authentication, Providers** and enable **Email**.
- [ ] Decide on **email confirmation**: keep it on for production. For local development you may turn it off to move faster, but turn it back on before launch.
- [ ] Set the **Site URL** (`http://localhost:5173` for now) and add redirect URLs under **Authentication, URL Configuration**.
- [ ] Set a password policy (minimum length of at least 8, ideally 10 or more).

### Step 1.2: Create the `patients` table migration

Created in `supabase/migrations/20261006165524_create_patients_table.sql`:

```sql
create table public.patients (
  id                uuid primary key references auth.users (id) on delete cascade,
  email             text,
  full_name         text,
  phone             text,
  date_of_birth     date,
  gender            text,
  address           jsonb not null default '{}'::jsonb,
  emergency_contact jsonb not null default '{}'::jsonb,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),

  constraint patients_phone_format
    check (phone is null or phone ~ '^\+[1-9][0-9]{7,14}$'),
  constraint patients_gender_valid
    check (gender is null or gender in
      ('female', 'male', 'non_binary', 'other', 'prefer_not_to_say'))
);

comment on table public.patients is 'Patient profile, one row per auth user.';
```

### Step 1.3: Create the signup trigger migration

Created in `supabase/migrations/20261006165647_create_patient_on_signup.sql`:

```sql
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.patients (id, email, full_name)
  values (
    new.id,
    new.email,
    new.raw_user_meta_data ->> 'full_name'
  );
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();
```

The trigger prevents a user from having an Auth account without a matching patient profile. The
profile is created inside the same database transaction as signup, using the Auth user's ID and
email. `security definer` lets this narrowly scoped function insert the row while ordinary clients
remain unable to insert arbitrary patient profiles. The empty `search_path` prevents object-name
hijacking, which is why the table is referenced as `public.patients`.

```text
User signs up
  -> Supabase inserts auth.users
  -> on_auth_user_created runs handle_new_user()
  -> public.patients receives the same ID and email
```

### Step 1.4: Create the `updated_at` trigger migration

Created in `supabase/migrations/20261006165722_add_updated_at_trigger.sql`:

```sql
create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger patients_set_updated_at
  before update on public.patients
  for each row execute function public.set_updated_at();
```

### Step 1.5: Enable RLS and write policies

Created in `supabase/migrations/20261006165743_enable_rls_patients.sql`:

```sql
alter table public.patients enable row level security;

revoke all on public.patients from anon;

create policy "patients_select_own"
  on public.patients for select
  to authenticated
  using ((select auth.uid()) = id);

create policy "patients_update_own"
  on public.patients for update
  to authenticated
  using ((select auth.uid()) = id)
  with check ((select auth.uid()) = id);

revoke update on public.patients from authenticated;
grant update (full_name, phone, date_of_birth, gender, address, emergency_contact)
  on public.patients to authenticated;
```

There is deliberately no client `INSERT` policy and no client `DELETE` policy. Profile creation is
owned by the signup trigger. Permanent deletion will be performed by the FastAPI backend through
the Supabase Admin API, which deletes `auth.users` and lets the foreign-key cascade remove the
patient profile.

### Step 1.6: Apply the migrations

```bash
npx supabase db push
```

Check the **Table Editor** and **Authentication** pages in the dashboard to confirm everything appeared.

### Step 1.7: Test RLS manually

- [ ] Create two test users (A and B) through the dashboard or a quick signup.
- [ ] Confirm each user has a `patients` row created automatically.
- [ ] Using a small script with each user's JWT, confirm user A cannot select or update user B's row.
- [ ] Confirm a client cannot insert a row for an arbitrary ID.
- [ ] Confirm a client cannot delete a patient row directly.

Expected results:

| Test | Expected result |
|---|---|
| Sign up user A | Matching `patients` row is created automatically |
| User A selects their own ID | One row returned |
| User A selects user B's ID | No rows returned |
| User A updates their own `phone` | Update succeeds when the value is valid |
| User A updates user B's `phone` | No rows updated |
| User A updates `email`, `id` or timestamps | Permission denied |
| User A inserts or deletes a patient row directly | Permission denied |

### Milestone 1 checklist

- [ ] Email/password auth enabled, redirect URLs and password policy configured
- [x] All migration files exist in `supabase/migrations/`
- [ ] Migrations have been pushed to the intended Supabase project
- [ ] Signup creates a `patients` row automatically
- [ ] RLS isolation verified with two users

---

# Milestone 2: Backend (FastAPI)

**Goal:** three secured endpoints with validation, error handling, logging and rate limiting.
**Estimated time:** 2 to 3 days.

### Step 2.1: App structure and configuration

- [x] Create the folders from [Project structure](#5-project-structure) (each with an `__init__.py`).
- [x] In `core/config.py`, use `pydantic-settings` to load `SUPABASE_URL`, keys, `FRONTEND_ORIGIN`, `ENVIRONMENT` and `LOG_LEVEL` from `.env`.
- [x] Store keys as `SecretStr` values so settings representations cannot expose them.
- [x] In `main.py`, add CORS middleware restricted to `FRONTEND_ORIGIN` only (no wildcard `*`).
- [x] Mount routers under `/api/v1` and keep `/health` as an undocumented deployment alias.
- [x] Add structured JSON startup, shutdown and request logging with safe request IDs and durations.
- [x] Add tests for configuration, secret redaction, health routes, CORS, request IDs and OpenAPI.

Verification: `ruff check .` passes and all 9 backend tests pass.

### Step 2.2: Authentication dependency

In `deps.py`, implement `get_current_user`:

- [x] Read the `Authorization: Bearer` header and reject requests without it (401).
- [x] Verify JWT signatures through the cached Supabase JWKS endpoint.
- [x] Restrict accepted algorithms to `ES256` and `RS256`; reject `none`, HMAC, and other algorithms.
- [x] Require and validate `exp`, `sub`, `aud` and `iss`, including `aud = authenticated` and an exact project issuer.
- [x] Require `sub` to be a valid UUID before using it as the patient ID.
- [x] Return the user ID and token in a frozen `CurrentUser` model, with the token protected by `SecretStr`.
- [x] Return the same generic error envelope and `WWW-Authenticate: Bearer` header for every authentication failure.
- [x] Log only safe authentication outcomes and failure categories—never tokens, claims, keys or patient IDs.
- [x] Add `GET /api/v1/auth/verify` so real tokens can be tested from the OpenAPI **Authorize** button.
- [x] Test valid, expired, malformed, incorrectly signed, wrong-audience, wrong-issuer, missing-claim, invalid-subject, disallowed-algorithm and JWKS-failure cases.

Verification: `ruff check .` passes and all 23 backend tests pass.

#### Step 2.2 explained in simple English

This step added a security guard to the backend.

When a patient signs in through Supabase, Supabase gives the patient an access token. The token is
similar to a temporary digital ID card. The frontend sends it to the backend whenever it calls a
protected endpoint.

```text
Patient sends a Supabase access token
                |
                v
Backend checks the token with Supabase's public signing keys
                |
                v
Backend confirms that the token is genuine, unmodified and not expired
                |
                v
Backend reads the verified patient ID from the token
                |
                v
The request is allowed or rejected
```

The backend checks that:

- Supabase created the token.
- The token belongs to this Supabase project.
- The token has not expired and is already valid.
- The token was issued for an authenticated user.
- The patient ID inside the token is a valid UUID.
- The token uses an approved signing algorithm.

Every authentication failure returns the same generic `401` response. The response does not reveal
whether the token was expired, malformed, signed incorrectly, or issued by another project. This
prevents an attacker from using detailed error messages to learn about the authentication system.

```json
{
  "error": {
    "code": "authentication_required",
    "message": "A valid bearer access token is required.",
    "request_id": "..."
  }
}
```

**What JWKS means:** JWKS is a public list of signing keys published by Supabase. The backend uses
these public keys to confirm that Supabase created a token and that nobody changed it. These public
verification keys are not the private service-role key.

**File responsibilities:**

| File | Simple responsibility |
|---|---|
| `core/security.py` | Checks whether the Supabase token is genuine and valid. |
| `deps.py` | Places the authentication guard in front of protected FastAPI endpoints. |
| `schemas/auth.py` | Holds the verified patient ID and protects the raw token from accidental display. |
| `core/errors.py` | Produces the same safe error response for every authentication failure. |
| `api/v1/auth.py` | Provides a small endpoint for testing authentication before patient endpoints exist. |

`GET /api/v1/auth/verify` does not return the patient ID or any patient information. It only proves
that authentication succeeded:

```json
{
  "authenticated": true
}
```

#### Generate a development access token with Python

Supabase generates an access token when an existing user signs in successfully. The Python client
supports this through `auth.sign_in_with_password()`. Careline includes an interactive development
helper that reads the Supabase URL and anon key from `backend/.env` and hides the password while it
is typed. It never uses the service-role key.

From `backend/`, activate the virtual environment and run:

```powershell
.\.venv\Scripts\Activate.ps1
python -m scripts.get_access_token
```

Enter the email and password of an existing test patient. If email confirmation is enabled, confirm
that user's email first. After a successful login, the helper prints the short-lived access token.

To test the token:

1. Start the backend with `uvicorn app.main:app --reload`.
2. Open `http://127.0.0.1:8000/docs`.
3. Click **Authorize**.
4. Paste only the token, without adding the word `Bearer`.
5. Run `GET /api/v1/auth/verify` and expect `{"authenticated": true}`.

The token is a temporary credential. Do not commit it, place it in `.env`, save it in a file,
include it in screenshots, or share it in messages. Generate another token by signing in again when
it expires. See the official
[Supabase Python sign-in documentation](https://supabase.com/docs/reference/python/auth-signinwithpassword).

#### Token generation and validation file sequence

Token generation and token validation are two separate flows. The generation helper talks to
Supabase Auth to obtain a token. The validation flow sends that token to Careline and proves that it
is genuine before a protected endpoint runs.

**Generation flow:**

```text
Email + hidden password
        |
        v
scripts/get_access_token.py
        |
        +--> core/config.py reads Supabase URL and anon key from backend/.env
        |
        v
Supabase Python client calls Supabase Auth
        |
        v
Supabase returns a session containing an access token
        |
        v
get_access_token.py prints only the access token
```

| Step | File or service | Input | Output |
|---|---|---|---|
| 1 | `scripts/get_access_token.py` | Email typed normally; password typed through a hidden prompt | Sign-in request data held only in memory |
| 2 | `core/config.py` | `SUPABASE_URL` and `SUPABASE_ANON_KEY` from `backend/.env` | Validated URL and protected anon key |
| 3 | Supabase Python client | URL, anon key, email and password | HTTPS request to Supabase Auth |
| 4 | Supabase Auth | Sign-in request | Auth response containing user and session data |
| 5 | `scripts/get_access_token.py` | `response.session.access_token` | Token printed in the terminal for temporary testing |

The local backend JWT verifier is not called during generation. Supabase creates and signs the token.

**Validation flow:**

```text
GET /api/v1/auth/verify with Authorization: Bearer <token>
        |
        v
core/logging.py assigns a safe request ID
        |
        v
api/v1/router.py selects api/v1/auth.py
        |
        v
deps.py extracts the Bearer token and loads request settings
        |
        v
core/security.py obtains the cached Supabase public signing key
        |
        v
PyJWT validates signature, algorithm, expiry, audience, issuer and subject
        |
        v
schemas/auth.py creates CurrentUser with a UUID and protected token
        |
        v
api/v1/auth.py returns {"authenticated": true}
```

| Step | File | Input | Output |
|---|---|---|---|
| 1 | `app/main.py` and `core/logging.py` | HTTP request | Request state containing a UUID request ID |
| 2 | `api/v1/router.py` | `/api/v1/auth/verify` path | Request routed to the authentication endpoint |
| 3 | `api/v1/auth.py` | Routed request | Calls the `CurrentUserDependency` security dependency |
| 4 | `deps.py` | `Authorization: Bearer <token>` and app settings | Raw token passed to the central verifier, or `AuthenticationError` if missing |
| 5 | `core/config.py` | Validated `SUPABASE_URL` | Exact issuer and JWKS endpoint URLs |
| 6 | `core/security.py` | Raw token, expected issuer and cached JWKS public key | Verified claims, or a safe internal failure category |
| 7 | `schemas/auth.py` | Verified UUID from `sub` and the raw token | Frozen `CurrentUser`; token stored as `SecretStr` |
| 8 | `api/v1/auth.py` | Verified `CurrentUser` dependency | `200 {"authenticated": true}` |
| 9 | `core/errors.py` on failure | `AuthenticationError` | Generic `401` error containing the request ID |
| 10 | `core/logging.py` | Final status and duration | Sanitized completion log and `X-Request-ID` header |

No patient-supplied ID is accepted during validation. The trusted patient ID comes only from the
verified token's `sub` claim. The verification endpoint does not return that ID or the token.

##### Concrete example from sign-in to validation

Assume a test patient already exists in Supabase Auth with the email `patient@example.com`. The
values below are examples only; never place a real password or token in documentation.

**1. Start the generator**

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m scripts.get_access_token
```

The helper asks for two inputs:

```text
Test patient email: patient@example.com
Test patient password: [hidden while typing]
```

The email and password exist only in the Python process long enough to send the sign-in request.
They are not written to a project file. The helper combines them with these configuration values:

| Value | Source | Purpose |
|---|---|---|
| `SUPABASE_URL` | `backend/.env` | Identifies the Supabase project receiving the sign-in request. |
| `SUPABASE_ANON_KEY` | `backend/.env` | Identifies the public application client. This is not the patient's token. |
| Email | Interactive prompt | Identifies the test patient attempting to sign in. |
| Password | Hidden interactive prompt | Proves that the person signing in knows the patient's password. |

The helper deliberately does not read or use `SUPABASE_SERVICE_ROLE_KEY` for patient login. The
service-role key bypasses normal authorization and must not be used to imitate an ordinary patient.

**2. Supabase authenticates the patient**

The Supabase Python client calls `supabase.auth.sign_in_with_password()` over HTTPS. Supabase checks
the email, password, confirmation status and account state. If authentication succeeds, Supabase
returns an auth response similar in shape to:

```text
AuthResponse
├── user
└── session
    ├── access_token
    ├── refresh_token
    ├── expires_in
    └── token_type = "bearer"
```

Only `session.access_token` is printed by the helper. The refresh token is not printed because it is
not needed for this manual verification test. In the future frontend, `supabase-js` will manage both
the session and automatic token refresh.

If login fails, no access token is generated. Common causes are an incorrect password, an unknown
user, or an unconfirmed email when confirmation is required.

**3. Send the access token to Careline**

After starting FastAPI, the client sends the token in the HTTP Authorization header:

```http
GET /api/v1/auth/verify HTTP/1.1
Host: 127.0.0.1:8000
Authorization: Bearer eyJ...
```

The `Bearer` prefix describes the authentication scheme. The value after it is the Supabase access
token. When using the Swagger **Authorize** dialog, paste only the token because Swagger adds the
`Bearer` prefix automatically.

**4. FastAPI extracts the credential**

`app/deps.py` uses FastAPI's `HTTPBearer` component to parse the header. At this point the token is
still untrusted. Merely receiving a token does not prove that it is genuine. A missing header, wrong
authentication scheme or empty credential immediately becomes an `AuthenticationError`.

**5. Careline obtains the public verification key**

`app/core/security.py` builds the JWKS URL from the validated Supabase project URL:

```text
<SUPABASE_URL>/auth/v1/.well-known/jwks.json
```

Supabase publishes public signing keys at this endpoint. PyJWT uses the token's key ID (`kid`) only
to select a matching public key. A `kid` value is never trusted as patient identity or authorization
data. JWKS documents are cached for five minutes and previously used keys are cached in memory, so
Careline normally does not call Supabase on every request. Key lookup fails closed: if the required
key cannot be obtained, the protected request is rejected.

**6. Careline validates the JWT**

The token is accepted only when all checks pass:

| Check | Required value | Reason |
|---|---|---|
| Signature | Valid for a public key in this project's JWKS | Proves the token was signed by Supabase and was not modified. |
| Algorithm | `ES256` or `RS256` | Rejects unsigned tokens, HMAC tokens and algorithm-confusion attempts. |
| `exp` | A time in the future | Rejects expired credentials. |
| `aud` | `authenticated` | Confirms the token represents an authenticated Supabase user. |
| `iss` | Exactly `<SUPABASE_URL>/auth/v1` | Rejects tokens created by another Supabase project. |
| `sub` | A valid UUID | Supplies the authenticated patient's trusted ID. |

Claims are used only after signature and claim validation succeeds. The backend never accepts a
patient ID from a URL, query parameter, form field or JSON body as a replacement for `sub`.

**7. Create the internal authenticated-user object**

After successful verification, `app/schemas/auth.py` creates:

```text
CurrentUser(
    id=<verified UUID from sub>,
    access_token=SecretStr("**********")
)
```

The original token is retained because Step 2.3 will forward it to a user-scoped Supabase client so
Postgres RLS can identify the patient. `SecretStr` prevents normal printing and model representations
from exposing the token. Code that genuinely needs the token must explicitly call
`get_secret_value()`.

**8. Complete the endpoint or return a safe error**

On success, the dependency gives `CurrentUser` to `app/api/v1/auth.py`, which returns only:

```json
{
  "authenticated": true
}
```

On any failure, `app/core/errors.py` returns the same generic `401` response. The internal logs can
record a safe category such as `expired` or `invalid_issuer`, but the client cannot see that category.
This prevents the API from becoming a token-diagnostics tool for attackers.

##### Trusted and untrusted data summary

| Data | Trusted? | How it is handled |
|---|---|---|
| Authorization header | No | Parsed, then passed to cryptographic verification. Never logged. |
| JWT header and `kid` | No | Used only to find a candidate public key. |
| JWT claims before verification | No | Never used for identity or authorization. |
| Verified `sub` | Yes | Converted to UUID and used as the authenticated patient ID. |
| Supabase JWKS public key | Yes, when fetched from the configured project | Used to verify the signature; safe to cache. |
| Anon/publishable key | Public application credential | Used for normal client access; database security still relies on RLS. |
| Service-role key | Highly privileged secret | Not used in token generation or verification; reserved for controlled backend administration. |
| Password | Secret | Hidden during entry, sent only to Supabase Auth, never logged or stored by the helper. |
| Access token | Temporary secret | Printed only for manual testing, sent in the Bearer header and wrapped in `SecretStr` internally. |

##### Expected results during manual testing

| Scenario | Expected API result | Safe server log category |
|---|---|---|
| Valid current token from this project | `200 {"authenticated": true}` | `authentication_succeeded` |
| No Authorization header | Generic `401` | `missing_bearer` |
| Expired token | Generic `401` | `expired` |
| Token from another Supabase project | Generic `401` | `invalid_issuer` or signing-key failure |
| Token with changed contents | Generic `401` | `invalid_token` |
| Token with wrong audience | Generic `401` | `invalid_audience` |
| JWKS endpoint temporarily unavailable with no cached key | Generic `401` | `signing_key_unavailable` |

Each response also receives an `X-Request-ID` header. The same ID appears in the safe request logs,
allowing a failed client request to be matched with its server-side events without recording the
token itself.

Authentication logs include safe operational details such as success or failure category, endpoint,
response status, request duration and request ID. They never include the token, Authorization header,
signing keys, patient ID, token claims or patient information.

At this point the backend can securely identify a patient, but it does not read patient data yet.
Step 2.3 creates a user-scoped Supabase connection for normal patient operations and a separate,
protected administrator connection used only for account deletion.

### Step 2.3: Supabase client factories

In `core/supabase.py`:

- [x] Add a **user-scoped client** that forwards the verified user's JWT so RLS applies.
- [x] Create a fresh user client for every request to prevent one patient's identity from leaking into another request.
- [x] Add an **admin client** using the service-role key, reserved for controlled service-layer operations such as account deletion.
- [x] Cache and reuse the admin client instead of reconstructing it for every operation.
- [x] Disable session persistence and automatic token refresh for both server-side clients.
- [x] Add a FastAPI dependency alias for patient-scoped clients.
- [x] Deliberately avoid a route-level admin dependency so privileged access remains in the service layer.
- [x] Never log or return the URL, JWT, anon key, service-role key, authorization headers, client representation or patient ID.
- [x] Test client scope, selected credentials, patient-token forwarding, per-request creation, admin caching, safe failures and log redaction.

Verification: `ruff check .` passes and all 29 backend tests pass.

#### Step 2.3 explained in simple English

The backend now has two different ways to connect to Supabase. They must remain separate because
they have very different permissions.

```text
Verified patient request
        |
        v
Fresh patient-scoped client
        |
        v
Supabase sees the patient's JWT
        |
        v
PostgreSQL RLS allows only that patient's row


Controlled backend service operation
        |
        v
Cached administrator client
        |
        v
Supabase receives the service-role key
        |
        v
Privileged operation such as deleting an Auth user
```

##### Patient-scoped client

`create_user_client()` in `app/core/supabase.py` receives two trusted objects:

| Input | Source | Example shape |
|---|---|---|
| `CurrentUser` | Created only after successful JWT validation in Step 2.2 | `id=UUID(...), access_token=SecretStr(...)` |
| `Settings` | Validated backend environment configuration | Supabase URL and protected anon key |

It unwraps the verified access token only long enough to configure this header internally:

```http
Authorization: Bearer <verified-patient-access-token>
```

It then creates a Supabase client using the anon key. The two credentials serve different purposes:

| Credential | Purpose |
|---|---|
| Anon/publishable key | Identifies the Careline application to Supabase. |
| Patient access token | Identifies the signed-in patient so `auth.uid()` works inside RLS policies. |

The output is a normal Supabase `Client` configured to act as that patient. Future read and update
services will use it like this:

```python
response = patient_client.table("patients").select("*").eq("id", str(current_user.id)).execute()
```

The client is newly created for every request and is never cached. Reusing a patient client could
accidentally carry patient A's token into patient B's request. Per-request creation prevents that
identity crossover.

FastAPI routes will request `UserSupabaseClientDependency` from `app/deps.py`. The dependency first
authenticates the request, then passes its verified `CurrentUser` to `create_user_client()`:

```text
Authorization header
        |
        v
deps.get_current_user()
        |
        v
Verified CurrentUser
        |
        v
deps.get_user_supabase_client()
        |
        v
core.supabase.create_user_client()
        |
        v
Patient-scoped Supabase Client
```

##### Administrator client

`get_admin_client()` uses `SUPABASE_SERVICE_ROLE_KEY` instead of a patient token. This key can bypass
RLS and therefore has much greater privileges.

| Input | Source | Output |
|---|---|---|
| Supabase URL | Validated backend `Settings` | Target project for the admin client |
| Service-role key | Backend `.env` as `SecretStr` | Authorization for Supabase Admin operations |
| Both values together | Passed to the private cached factory | Privileged Supabase `Client` |

The admin client is cached because it has one backend identity rather than a different identity for
each patient. Repeated calls return the same client instance:

```text
First get_admin_client() call  -> construct client -> cache it -> return client
Later get_admin_client() call  -> return cached client
```

There is intentionally no `AdminSupabaseClientDependency` for routes. Future deletion routes will
call a patient service, and only that service will obtain the admin client. This makes it harder for
an ordinary read or update route to accidentally bypass RLS.

##### Sample input and output data

These examples show shapes only. They do not contain real credentials.

**Patient-client input:**

```text
CurrentUser
  id: 0f24d94e-7617-41a6-8a32-1d921fa21852
  access_token: SecretStr("**********")

Settings
  supabase_url: https://example.supabase.co
  supabase_anon_key: SecretStr("**********")
```

**Patient-client internal configuration:**

```text
API key: anon/publishable key
Authorization: Bearer <patient JWT>
auto_refresh_token: false
persist_session: false
```

**Patient-client output:**

```text
Supabase Client scoped to the authenticated patient
```

**Administrator-client input:**

```text
Settings
  supabase_url: https://example.supabase.co
  supabase_service_role_key: SecretStr("**********")
```

**Administrator-client output:**

```text
Cached Supabase Client with service-role privileges
```

No API response is added in Step 2.3. These clients are backend infrastructure that the patient
endpoints will use after schemas and services are implemented.

##### Logging behavior

Client creation logs contain only operational fields. Example successful patient-client log:

```json
{
  "level": "DEBUG",
  "event": "supabase_client_created",
  "request_id": "7b777a5b-0d2a-464a-95c5-e37acbd57c00",
  "client_scope": "user"
}
```

Example safe failure log:

```json
{
  "level": "ERROR",
  "event": "supabase_client_creation_failed",
  "request_id": "7b777a5b-0d2a-464a-95c5-e37acbd57c00",
  "client_scope": "admin",
  "error_type": "RuntimeError"
}
```

The exception message is intentionally omitted because third-party errors can include URLs,
headers or configuration values. The logs never contain:

- Patient access tokens or Authorization headers
- Patient IDs
- Anon/publishable or service-role keys
- Supabase project URLs
- Supabase client object representations
- Patient profile data

##### Failure behavior

If client construction fails, the factory logs the safe scope and exception class, then raises
`SupabaseClientCreationError("user")` or `SupabaseClientCreationError("admin")`. The original library
error remains chained internally for debugging but is not logged or returned to an API client. A
later global service-error handler will convert these internal failures into a safe API response.

### Step 2.4: Schemas and validators

In `schemas/patient.py`:

- [x] Add `PatientOut` as the complete patient response model.
- [x] Add `AttributeUpdate` with the strict body shape `{ "value": <any> }`.
- [x] Reject unknown request-body properties to prevent mass assignment.
- [x] Add `READABLE_FIELDS` and `EDITABLE_KEYS` security whitelists.
- [x] Add a mapping of editable keys to Pydantic validators:
  - `full_name`: non-empty string, length-limited
  - `phone`: E.164 regex
  - `date_of_birth`: valid date between `1900-01-01` and today
  - `gender`: from an allowed list
  - `address` and `emergency_contact`: structured models
- [x] Normalize whitespace, dates, enums and nested models into database-ready JSON values.
- [x] Allow optional attributes to be cleared with `null`, while preventing `full_name` from being cleared.
- [x] Add safe validation logs that record only the field name and error count—not patient values.
- [x] Test every valid value, invalid value, boundary, nested model, null rule and protected field.

Verification: `ruff check .` passes and all 71 backend tests pass.

#### Step 2.4 explained in simple English

This step defines exactly what patient data is allowed to enter and leave the API. It does not read
or update Supabase yet. It creates a strict contract that later routes and services must follow.

```text
PATCH request path chooses an attribute key
                    |
                    v
AttributeUpdate accepts exactly one body property: value
                    |
                    v
EDITABLE_KEYS checks whether the selected field may be changed
                    |
                    v
ATTRIBUTE_ADAPTERS selects that field's Pydantic rules
                    |
                    v
Value is rejected or normalized into database-ready data
```

##### Security whitelists

`READABLE_FIELDS` defines the columns the future GET endpoint may return:

```text
id, email, full_name, phone, date_of_birth, gender, address,
emergency_contact, created_at, updated_at
```

`EDITABLE_KEYS` is intentionally smaller:

```text
full_name, phone, date_of_birth, gender, address, emergency_contact
```

The following fields are readable but cannot be changed through the attribute endpoint:

| Protected field | Reason |
|---|---|
| `id` | Comes from the verified Supabase user identity. |
| `email` | Email changes must go through Supabase Auth and its verification flow. |
| `created_at` | Database-controlled audit timestamp. |
| `updated_at` | Maintained automatically by the database trigger. |

Any field absent from `EDITABLE_KEYS`, including a made-up field such as `password`, is rejected
before a database operation can occur.

##### Request-body model

The future route will look like:

```http
PATCH /api/v1/patients/me/attributes/phone
Content-Type: application/json

{
  "value": "+16045550123"
}
```

The path selects the field and the body supplies only its new value. These bodies are rejected:

```json
{}
```

```json
{
  "value": "Alice",
  "email": "unexpected@example.com"
}
```

Rejecting extra properties prevents a client from trying to update multiple columns through one
request.

##### Field validation and normalization

| Attribute | Accepted input | Normalized output | Rejected examples |
|---|---|---|---|
| `full_name` | Non-empty string, 1–100 characters | Surrounding whitespace removed | `null`, `""`, whitespace-only text, over 100 characters |
| `phone` | E.164 number or `null` | Trimmed string such as `+16045550123` | `6045550123`, `+0123456789`, `phone` |
| `date_of_birth` | ISO date from 1900-01-01 through today, or `null` | ISO `YYYY-MM-DD` string | Future date, date before 1900, malformed date |
| `gender` | Allowed value or `null` | Same allowed string | Any unlisted value |
| `address` | Known structured fields or `null` | Trimmed dictionary; blank optional strings become `null` | Unknown fields or over-length strings |
| `emergency_contact` | Complete structured contact or `null` | Trimmed dictionary | Missing name/phone/relationship, bad phone, unknown fields |

Allowed gender values exactly match the database constraint:

```text
female
male
non_binary
other
prefer_not_to_say
```

##### Sample validation inputs and outputs

**Full name:**

```text
Input key:    full_name
Input value:  "  Alice Patient  "
Output value: "Alice Patient"
```

**Phone:**

```text
Input key:    phone
Input value:  "+16045550123"
Output value: "+16045550123"
```

Optional phone clearing:

```text
Input key:    phone
Input value:  null
Output value: null
```

**Date of birth:**

```text
Input key:    date_of_birth
Input value:  "1990-04-12"
Output value: "1990-04-12"
```

**Address:**

```json
{
  "line1": "  123 Main Street  ",
  "line2": "   ",
  "city": " Vancouver ",
  "region": "BC",
  "postal_code": "V1V 1V1",
  "country": "Canada"
}
```

Normalized output:

```json
{
  "line1": "123 Main Street",
  "line2": null,
  "city": "Vancouver",
  "region": "BC",
  "postal_code": "V1V 1V1",
  "country": "Canada"
}
```

**Emergency contact:**

```json
{
  "name": "Bob Patient",
  "phone": "+16045550124",
  "relationship": "Parent"
}
```

All three properties are required when an emergency-contact object is provided. The entire
attribute may instead be cleared with `null`.

##### Response model

`PatientOut` describes a complete profile returned by the API:

```text
PatientOut
├── id: UUID
├── email: string
├── full_name: string or null
├── phone: string or null
├── date_of_birth: date or null
├── gender: allowed gender value or null
├── address: Address
├── emergency_contact: EmergencyContactOut
├── created_at: timestamp
└── updated_at: timestamp
```

New database profiles contain `{}` for address and emergency contact. Separate output and update
models allow those empty database defaults to be returned safely while still requiring a complete
emergency contact when a patient submits one.

##### Code sequence and outputs

`validate_attribute_value(key, value)` performs these steps:

| Step | Input | Operation | Output |
|---|---|---|---|
| 1 | Attribute key | Look up the key in `ATTRIBUTE_ADAPTERS` | Matching validator or `UnsupportedPatientAttributeError` |
| 2 | Raw patient value | Run the field-specific Pydantic rules | Typed Python value or `ValidationError` |
| 3 | Typed value | Serialize in JSON mode | Database-ready string, dictionary or `null` |
| 4 | Result | Emit a value-free success log | Normalized value returned to the future service |

Dates become ISO strings, enum values become their database strings, and nested Pydantic models
become dictionaries. The function never chooses a database column dynamically unless that column
already exists in the hard-coded adapter map.

##### Logging behavior

Successful validation produces a DEBUG record similar to:

```json
{
  "level": "DEBUG",
  "event": "patient_attribute_validation_succeeded",
  "request_id": "7b777a5b-0d2a-464a-95c5-e37acbd57c00",
  "attribute_key": "phone"
}
```

Failed validation produces a safe WARNING record:

```json
{
  "level": "WARNING",
  "event": "patient_attribute_validation_failed",
  "request_id": "7b777a5b-0d2a-464a-95c5-e37acbd57c00",
  "attribute_key": "phone",
  "validation_error_count": 1
}
```

Logs intentionally exclude the rejected value and Pydantic's complete error representation because
those can contain names, phone numbers, addresses, dates of birth or emergency-contact information.

### Chat-first implementation order

Careline's primary patient interface will be conversational, but the LangGraph orchestrator will be
built on top of deterministic patient services. This order keeps authorization, validation and RLS
enforcement independent from model behavior:

1. **Step 2.5 — Read service and GET endpoint:** implement `patient_service.get_patient()` and
   `GET /api/v1/patients/me`. This becomes the implementation behind the future `get_my_profile`
   agent tool.
2. **Step 2.6 — Update service and PATCH endpoint:** implement
   `patient_service.update_patient_attribute()` and the single-attribute PATCH endpoint. This
   becomes the implementation behind the future `propose_profile_update` and
   `execute_profile_update` tools.
3. **LangGraph chat workflow:** add `POST /api/v1/chat`, typed graph state, intent/tool routing and
   conversation responses. Read tools may execute immediately. Update tools must pause for explicit
   patient confirmation before the deterministic update service runs.
4. **Deletion and hardening:** keep account deletion outside autonomous model execution. Build the
   deterministic delete flow, stricter confirmation, rate limiting, consistent errors and complete
   end-to-end tests.

The agent will run inside the FastAPI application and call Python service functions directly. It
will not make HTTP requests back into its own REST API. The REST endpoints remain useful for tests,
non-chat clients and a clear public contract, while both REST and LangGraph reuse the same service
layer:

```text
REST GET/PATCH routes --------+
                              |
                              v
                    patient_service.py
                              ^
                              |
LangGraph patient tools ------+
```

The authenticated patient ID and JWT are injected from the verified FastAPI request context. They
are not placed in model prompts and cannot be selected or changed by the language model. Chat and
graph logs must not contain access tokens, full prompts, patient attributes or tool results with
patient data. If graph checkpoints are added, their storage, encryption and retention must be
designed explicitly before real patient information is stored.

This follows LangGraph's recommended pattern of separating LLM steps, data steps, action steps and
user-input steps, with graph state containing only information that truly needs to persist. Update
approval will use a human-in-the-loop pause rather than trusting the model to authorize its own
write. See the official
[LangGraph workflow design guidance](https://docs.langchain.com/oss/javascript/langgraph/thinking-in-langgraph).

### Step 2.5: Endpoint 1, get patient attributes

`GET /api/v1/patients/me`

- [x] Add `patient_service.get_patient()` as the deterministic read operation shared by REST and future LangGraph tools.
- [x] Fetch only the row matching the authenticated JWT's `sub` UUID while retaining RLS through the patient-scoped client.
- [x] Support `?fields=email,phone`, including trimming and stable duplicate removal.
- [x] Reject unknown, empty or malformed field selections with a consistent 400 response.
- [x] Return only requested fields while preserving a requested field whose value is `null`.
- [x] Strictly validate complete profiles and field-filtered responses against Pydantic output models.
- [x] Return 404 when no RLS-visible patient row exists.
- [x] Convert Supabase failures and invalid stored records into safe 503 and 500 responses.
- [x] Add operation logs without patient IDs, selected values, database messages or response bodies.
- [x] Document the secured route and all expected responses in OpenAPI.

Verification: `ruff check .` passes and all 92 backend tests pass.

#### Step 2.5 explained in simple English

This step lets an authenticated patient read their profile. The patient never supplies an ID. The
backend takes the trusted patient UUID from the already verified Supabase token.

```text
GET /api/v1/patients/me
        |
        v
JWT authentication creates CurrentUser
        |
        v
Patient-scoped Supabase client carries the same JWT
        |
        v
Patient route validates the optional fields query
        |
        v
patient_service.get_patient() builds a fixed table query
        |
        v
Supabase RLS checks auth.uid() = patients.id
        |
        v
Pydantic validates the returned profile
        |
        v
FastAPI returns the complete or filtered JSON response
```

##### Why both an ID filter and RLS are used

The service builds a query equivalent to:

```python
client.table("patients").select(selected_columns).eq("id", str(current_user.id)).limit(1).execute()
```

There are two authorization boundaries:

1. The explicit ID filter comes from the verified token—not from the URL, body, query string or
   language model.
2. The patient-scoped client forwards the JWT, allowing PostgreSQL RLS to independently enforce
   `auth.uid() = id`.

Even if a future route or agent tool makes a mistake, RLS remains a database-level safety net.

##### Complete-profile request and response

Input:

```http
GET /api/v1/patients/me
Authorization: Bearer <verified Supabase access token>
```

Example output:

```json
{
  "id": "0f24d94e-7617-41a6-8a32-1d921fa21852",
  "email": "patient@example.com",
  "full_name": "Alice Patient",
  "phone": "+16045550123",
  "date_of_birth": "1990-04-12",
  "gender": "female",
  "address": {},
  "emergency_contact": {},
  "created_at": "2026-10-06T10:00:00Z",
  "updated_at": "2026-10-06T10:00:00Z"
}
```

The complete response is validated with `PatientOut`. Required database fields such as `id`,
`email`, `created_at` and `updated_at` must be present and correctly typed.

##### Field-filtered request and response

Input:

```http
GET /api/v1/patients/me?fields=email,phone
Authorization: Bearer <verified Supabase access token>
```

Example output:

```json
{
  "email": "patient@example.com",
  "phone": "+16045550123"
}
```

Only the requested columns are selected from Supabase. Fields are trimmed and duplicates are
removed while preserving their first occurrence:

```text
?fields= email,phone,email
                    |
                    v
("email", "phone")
```

If a requested value is empty in the database, it remains in the response as `null` rather than
being mistaken for an unrequested field:

```json
{
  "email": "patient@example.com",
  "phone": null
}
```

##### Invalid field selection

These examples are rejected before Supabase is queried:

```text
?fields=password
?fields=email,,phone
?fields=email,unknown
```

The response lists the permitted fields but does not echo the untrusted field text:

```json
{
  "error": {
    "code": "invalid_fields",
    "message": "One or more requested fields are invalid. Allowed fields: id, email, full_name, phone, date_of_birth, gender, address, emergency_contact, created_at, updated_at.",
    "request_id": "7b777a5b-0d2a-464a-95c5-e37acbd57c00"
  }
}
```

##### File and data sequence

| Step | File | Input | Output |
|---|---|---|---|
| 1 | `core/logging.py` | HTTP request | Request ID and request timing context |
| 2 | `deps.py` | Bearer token | Verified `CurrentUser` and patient-scoped Supabase client |
| 3 | `api/v1/patients.py` | Optional `fields` string | Validated field tuple |
| 4 | `services/patient_service.py` | Client, JWT-derived UUID and field tuple | RLS-protected Supabase query |
| 5 | Supabase/PostgREST | Table query plus patient JWT | Zero or one visible patient row |
| 6 | `schemas/patient.py` | Returned database dictionary | Validated `PatientAttributesOut` |
| 7 | FastAPI | Validated response model | Complete or filtered JSON response |

##### Error responses

| Condition | Status | Error code |
|---|---:|---|
| Missing or invalid access token | 401 | `authentication_required` |
| Unknown or malformed field selection | 400 | `invalid_fields` |
| No RLS-visible patient profile | 404 | `patient_not_found` |
| Stored row violates the response contract | 500 | `invalid_patient_data` |
| Supabase query cannot complete | 503 | `patient_service_unavailable` |

All error responses include the same request ID placed in the `X-Request-ID` response header.

##### Logging behavior

Successful read example:

```json
{
  "level": "INFO",
  "event": "patient_profile_retrieved",
  "request_id": "7b777a5b-0d2a-464a-95c5-e37acbd57c00",
  "operation": "get_patient",
  "selected_field_count": 2,
  "full_profile": false
}
```

Safe external-failure example:

```json
{
  "level": "ERROR",
  "event": "patient_profile_query_failed",
  "request_id": "7b777a5b-0d2a-464a-95c5-e37acbd57c00",
  "operation": "get_patient",
  "error_type": "APIError"
}
```

Logs exclude the patient UUID, profile values, JWT, Authorization header, selected field names,
Supabase URL, database response, SQL details and original external exception message.

##### Future LangGraph reuse

The future `get_my_profile` tool will call `patient_service.get_patient()` directly with the
request's already verified user and patient-scoped client. It will not make an HTTP request back to
`GET /patients/me` and it will not give the language model access to a patient ID or Supabase client:

```text
Patient: "What phone number do you have for me?"
        |
        v
LangGraph selects get_my_profile(fields=["phone"])
        |
        v
Trusted tool wrapper calls patient_service.get_patient()
        |
        v
Service returns {"phone": "+16045550123"}
        |
        v
Model creates the conversational response
```

### Step 2.6: Endpoint 2, update an attribute by key

`PATCH /api/v1/patients/me/attributes/{key}`

- [x] Add `patient_service.update_patient_attribute()` as the deterministic update operation shared by REST and future LangGraph tools.
- [x] Reject keys not in `EDITABLE_KEYS` with a safe 400 response before querying Supabase.
- [x] Validate and normalize the value with the Step 2.4 field-specific adapters.
- [x] Return a value-free 422 response when the new value or request structure is invalid.
- [x] Update exactly one whitelisted column for the JWT-derived patient UUID.
- [x] Preserve the patient-scoped JWT so PostgreSQL RLS independently authorizes the update.
- [x] Allow optional fields to be cleared with `null`, while preventing `full_name` from being cleared.
- [x] Validate and return the complete updated `PatientOut` profile.
- [x] Return safe 404, 500 and 503 responses for no matching row, invalid returned data and Supabase failure.
- [x] Add value-free logs for success, validation failure, missing profile and external failure.
- [x] Document authentication and every response in OpenAPI.

Verification: `ruff check .` passes and all 110 backend tests pass.

#### Step 2.6 explained in simple English

This endpoint changes one approved profile field at a time. The URL selects the field and the JSON
body supplies its new value:

```http
PATCH /api/v1/patients/me/attributes/phone
Authorization: Bearer <verified Supabase access token>
Content-Type: application/json

{
  "value": "+16045550123"
}
```

The endpoint cannot update multiple fields in one request. It cannot change `id`, `email`, database
timestamps or arbitrary columns.

```text
PATCH request
      |
      v
JWT authentication identifies the patient
      |
      v
AttributeUpdate accepts exactly {"value": ...}
      |
      v
EDITABLE_KEYS approves or rejects the URL key
      |
      v
Step 2.4 validator normalizes the new value
      |
      v
patient_service.update_patient_attribute()
      |
      v
Supabase updates one column where id = verified JWT sub
      |
      v
RLS independently checks auth.uid() = id
      |
      v
PatientOut validates the complete returned row
      |
      v
FastAPI returns the updated profile
```

##### Editable and protected keys

Editable:

```text
full_name
phone
date_of_birth
gender
address
emergency_contact
```

Protected examples:

```text
id
email
created_at
updated_at
password
any unknown field
```

Only a key already present in the hard-coded `ATTRIBUTE_ADAPTERS` map can become the dictionary key
sent to Supabase. This prevents a URL value from becoming an unrestricted database column name.

##### Sample successful update

Input:

```http
PATCH /api/v1/patients/me/attributes/full_name

{
  "value": "  Alice Patient  "
}
```

Internal normalized update:

```json
{
  "full_name": "Alice Patient"
}
```

Example output:

```json
{
  "id": "0f24d94e-7617-41a6-8a32-1d921fa21852",
  "email": "patient@example.com",
  "full_name": "Alice Patient",
  "phone": "+16045550123",
  "date_of_birth": "1990-04-12",
  "gender": "female",
  "address": {},
  "emergency_contact": {},
  "created_at": "2026-10-06T10:00:00Z",
  "updated_at": "2026-10-06T10:30:00Z"
}
```

The database `updated_at` trigger changes the timestamp automatically. The API does not accept an
`updated_at` value from the client.

##### Clearing an optional field

Input:

```http
PATCH /api/v1/patients/me/attributes/phone

{
  "value": null
}
```

Internal update and response field:

```json
{
  "phone": null
}
```

`phone`, `date_of_birth`, `gender`, `address` and `emergency_contact` may be cleared. `full_name`
cannot be `null`, empty or whitespace-only.

##### Invalid value example

Input:

```http
PATCH /api/v1/patients/me/attributes/phone

{
  "value": "604-555-0123"
}
```

Output:

```json
{
  "error": {
    "code": "invalid_attribute_value",
    "message": "The supplied attribute value is invalid.",
    "request_id": "7b777a5b-0d2a-464a-95c5-e37acbd57c00"
  }
}
```

The response deliberately does not repeat the rejected value or expose internal Pydantic details.

##### Protected key example

Input:

```http
PATCH /api/v1/patients/me/attributes/email

{
  "value": "new@example.com"
}
```

Output status: `400`. The database is not queried. Email changes must use Supabase Auth so address
verification cannot be bypassed.

##### File and data sequence

| Step | File | Input | Output |
|---|---|---|---|
| 1 | `core/logging.py` | HTTP request | Request ID and timing context |
| 2 | `deps.py` | Bearer token | Verified `CurrentUser` and patient-scoped client |
| 3 | `api/v1/patients.py` | Path key and `AttributeUpdate` body | Call to the deterministic service |
| 4 | `schemas/patient.py` | Raw key and value | Normalized database-ready value or validation failure |
| 5 | `services/patient_service.py` | Client, JWT-derived UUID, key and normalized value | One-column update query |
| 6 | Supabase/PostgREST | Update plus patient JWT | Zero or one RLS-visible updated row |
| 7 | `schemas/patient.py` | Returned database row | Strict `PatientOut` |
| 8 | FastAPI | Validated patient model | `200` JSON response |

The Supabase query is equivalent to:

```python
client.table("patients").update({key: normalized_value}).eq(
    "id", str(current_user.id)
).execute()
```

The service never accepts the patient UUID from the model, path or request body.

##### Error responses

| Condition | Status | Error code |
|---|---:|---|
| Missing or invalid token | 401 | `authentication_required` |
| Attribute is protected or unknown | 400 | `attribute_not_editable` |
| Body is missing `value` or contains extra properties | 422 | `request_validation_failed` |
| Field-specific value is invalid | 422 | `invalid_attribute_value` |
| No RLS-visible patient row was updated | 404 | `patient_not_found` |
| Returned row violates the response contract | 500 | `invalid_patient_data` |
| Supabase cannot complete the update | 503 | `patient_service_unavailable` |

##### Logging behavior

Successful update example:

```json
{
  "level": "INFO",
  "event": "patient_profile_attribute_updated",
  "request_id": "7b777a5b-0d2a-464a-95c5-e37acbd57c00",
  "operation": "update_patient_attribute",
  "attribute_key": "phone"
}
```

Safe database-failure example:

```json
{
  "level": "ERROR",
  "event": "patient_profile_update_failed",
  "request_id": "7b777a5b-0d2a-464a-95c5-e37acbd57c00",
  "operation": "update_patient_attribute",
  "attribute_key": "phone",
  "error_type": "APIError"
}
```

Logs exclude the patient UUID, old value, new value, returned profile, JWT, Authorization header,
Supabase URL and original database error message.

##### Implemented LangGraph propose-confirm-execute flow

The chat workflow does not call this write service immediately after interpreting a message. It
first validate and show the proposed change to the patient:

```text
Patient: "Change my phone number to +16045550999"
        |
        v
Model extracts key=phone and value=+16045550999
        |
        v
Deterministic validator checks and normalizes the proposal
        |
        v
Assistant: "Change your phone number to +16045550999?"
        |
        v
Backend returns a short-lived, one-time confirmation ID
        |
   yes  |  no
        |------------------> Cancel without database access
        v
Trusted tool wrapper calls update_patient_attribute()
        |
        v
Assistant reports the confirmed result
```

The language model may propose a key and value, but it cannot expand `EDITABLE_KEYS`, choose a
patient ID, bypass validation, approve its own action or obtain the Supabase client.

### Step 2.7: LangGraph patient-profile chat orchestrator

`POST /api/v1/chat`

- [x] Add a typed LangGraph workflow for profile reads, profile-update proposals and unsupported
  requests.
- [x] Use OpenAI structured output for intent and field/value extraction instead of parsing free-form
  model text.
- [x] Keep the verified patient UUID, access token and Supabase client outside prompts and graph
  state.
- [x] Reuse `get_patient()` and `update_patient_attribute()` so REST and chat enforce identical
  field whitelists, validation, RLS and error handling.
- [x] Execute safe reads immediately.
- [x] Validate an update proposal without writing it, then return a cryptographically random action
  ID that expires after 10 minutes by default.
- [x] Bind every pending action to the UUID from the verified JWT and allow it to be used only once.
- [x] Require a second, explicitly typed approval request before performing a database update.
- [x] Allow rejection to cancel the proposal without making a database call.
- [x] Reject expired, unknown, cross-patient and replayed action IDs with one generic safe error.
- [x] Return a fixed boundary response for medical advice, deletion and other unsupported requests.
- [x] Add PHI-safe operational logs and tests that make no paid OpenAI calls.
- [x] Require a patient-owned active conversation and persist every graph turn.
- [x] Prevent duplicate processing with an optional frontend-generated `client_message_id`.
- [x] Load a bounded same-session message window before model classification.
- [x] Store graph runs, sanitized tool events and assistant responses atomically.
- [x] Bind pending confirmations to both the authenticated patient and conversation.

Verification: `ruff check .` passes and all 150 backend tests pass. A live classifier smoke test with
the configured `gpt-5.4-mini` model also returned the expected `read_profile` decision. The warnings
are LangGraph's internal serializer deprecation notice and a local OneDrive pytest-cache permission
warning, not Careline test failures.

#### What the code does, in simple English

The endpoint accepts one of two inputs, and both must name an active `session_id`:

1. A new patient message, such as `What phone number do you have for me?`
2. A machine-readable approval or rejection for a change that was already proposed.

For a new message, the model is only a router. It returns a small structured decision such as:

```json
{
  "intent": "read_profile",
  "fields": ["phone"],
  "key": null,
  "value": null
}
```

LangGraph then chooses a deterministic node. That node checks the model's suggested fields against
the hard-coded allowlist before calling the patient service. The model never receives a database
client and cannot supply a patient ID.

For an update request, the first call only creates a proposal:

```text
message -> model extracts intent/key/value -> application validates value
        -> short-lived pending action -> frontend shows Confirm / Cancel
```

The second call contains the same `session_id`, the returned action ID and a real Boolean value,
`true` or `false`. It does
not ask the model whether words like "yes" count as consent. On approval, the backend checks that
the action belongs to the currently authenticated patient, permanently consumes it, and calls the
existing deterministic update service. On rejection, it consumes the action and stops without a
database update. A proposal created in one conversation cannot be confirmed from another.

Every accepted turn first stores the user-visible input and a `started` run. After the graph
finishes, one database transaction stores the assistant message, sanitized node/tool events, the
validated classifier decision, run timing and the new session timestamp. If the graph fails, the
run is marked `failed` with only a safe error category.

Pending changes are durable. The backend returns a random one-time token to the browser but stores
only its SHA-256 hash in `chat_pending_actions`. The proposal row is written in the same transaction
as the assistant's confirmation-request message. Approval or rejection calls a database function
that locks and consumes that row exactly once. Restarting the API or sending the confirmation to a
different API worker therefore does not lose the proposal. Do not place patient data into LangGraph
checkpoints until checkpoint encryption, access control and deletion policies have been designed.

#### Files called in sequence

```text
POST /api/v1/chat
  -> app/api/v1/chat.py                  authenticates and coordinates one turn
  -> app/services/chat_turn_service.py  atomically stores input + started run
  -> app/repositories/chat_history_repository.py
                                        loads same-session messages and audited prior summaries
  -> app/agents/classifier.py            extracts a typed decision using bounded context
  -> app/agents/patient_graph.py         routes to read, propose, confirm or unsupported
  -> app/agents/pending_actions.py       creates/hashes or consumes a one-time action token
  -> app/services/patient_service.py     performs the allowed read/confirmed update
  -> app/services/chat_turn_service.py  atomically stores output + run + safe tool events
  -> Supabase RPCs/tables                enforce ownership, one-time use, constraints and ordering
```

The supporting contracts are in `app/schemas/chat.py`. Short-lived proposals are managed by
`app/agents/pending_actions.py`. Chat errors are converted to the same safe JSON envelope in
`app/core/errors.py`.

#### Configure the model

Install the updated backend requirements, then add these values to `backend/.env`:

```dotenv
OPENAI_API_KEY=your-development-key
OPENAI_MODEL=gpt-5.4-mini
CHAT_PENDING_ACTION_TTL_SECONDS=600
CHAT_PROMPT_VERSION=patient-router-v1
CHAT_CONTEXT_MESSAGE_LIMIT=20
CHAT_PRIOR_SESSION_LIMIT=3
CHAT_SUMMARY_MESSAGE_LIMIT=30
CHAT_SUMMARY_MAX_CHARS=2000
```

Keep the real key out of Git. The rest of the API can start without it, but a new chat message will
return `503 chat_service_unavailable` until it is set. The default model supports structured output;
the model name remains configurable so it can be changed without editing Python. The backend also
accepts the older `CHAT_MODEL` environment name; if both names are present, `CHAT_MODEL` wins.

The classifier uses strict JSON Schema mode. Its fields are deliberately limited to explicit
provider-compatible types: `intent`, `fields`, nullable `key`, and nullable string `value`. Complex
address or emergency-contact values are returned as JSON-encoded strings, parsed inside the trusted
graph, and then checked by the normal patient validators. Do not replace `value` with an
unconstrained `Any`/`JsonValue`; that produces an empty schema definition which OpenAI rejects with
HTTP 400. Provider failure logs include only safe diagnostic metadata (`status_code`, error `code`
and error `param`), never the prompt or provider response body.

This implementation sends the patient's new message plus at most
`CHAT_CONTEXT_MESSAGE_LIMIT` earlier messages from the same conversation to the configured model
provider for classification. The default is 20 and the validated maximum is 50. System notices are
excluded. A new session can also receive at most `CHAT_PRIOR_SESSION_LIMIT` summaries that were
selected and audited when the session was created. It does not send the stored profile returned by
Supabase back to the model; the backend builds that response deterministically. Set the same-session
limit or prior-session limit to `0` to disable that kind of context. Before using real health
information, complete the required privacy, consent, data residency and provider-contract review for
your jurisdiction.

#### Test the chat read flow

After starting the API and obtaining a test patient's access token, open Swagger at
`http://127.0.0.1:8000/docs` and authorize. First call `POST /api/v1/chat/sessions` with `{}` or an
optional title, then copy its `id`. Generate a new UUID for the frontend action and call
`POST /api/v1/chat`:

```json
{
  "session_id": "624cce1f-7e28-4604-aa8a-10403e5b90de",
  "client_message_id": "a95c41a8-2962-4a4e-bf20-b61581dcdb86",
  "message": "What are my email address and phone number?"
}
```

Example response:

```json
{
  "action": "answer",
  "message": "Here are the requested details from your patient profile.",
  "requires_confirmation": false,
  "pending_action_id": null,
  "data": {
    "email": "patient@example.com",
    "phone": "+16045550123"
  },
  "turn": {
    "session_id": "624cce1f-7e28-4604-aa8a-10403e5b90de",
    "run_id": "ad12308c-cf78-47cf-86e2-16ec5d3413dc",
    "input_message_id": "461d62d3-8df1-40f6-978d-37ddc1c810e2",
    "output_message_id": "bd89aa7d-7c21-49ab-b909-086380ddbc91"
  }
}
```

#### Test the chat update and confirmation flow

First send the requested change:

```json
{
  "session_id": "624cce1f-7e28-4604-aa8a-10403e5b90de",
  "client_message_id": "7f6b13d8-25af-4143-9b1c-ac8b7951e387",
  "message": "Change my phone number to +16045550999"
}
```

The backend validates the number but does **not** update Supabase yet. It returns a proposal:

```json
{
  "action": "confirmation_required",
  "message": "Please confirm the proposed profile change before it is saved.",
  "requires_confirmation": true,
  "pending_action_id": "copy-the-real-value-returned-here",
  "data": {
    "proposed_change": {
      "key": "phone",
      "value": "+16045550999"
    }
  }
}
```

Copy the actual `pending_action_id` into a second call. To approve:

```json
{
  "session_id": "624cce1f-7e28-4604-aa8a-10403e5b90de",
  "client_message_id": "d563466c-060e-4e04-8371-71edc017730c",
  "confirmation": {
    "action_id": "paste-the-returned-action-id",
    "approved": true
  }
}
```

The response has `action: "updated"` and the confirmed profile. To cancel instead, send the same
shape with `"approved": false`; the response has `action: "cancelled"` and Supabase is not changed.
An action ID cannot be approved twice. Each genuinely new request uses a new `client_message_id`;
reuse the same ID only for an HTTP retry. A reused ID returns `409 chat_turn_already_processed`
before the model or profile tool runs.

#### Chat identifiers and `chat_turn_already_processed`

Careline uses several IDs for different jobs. They are not interchangeable:

| ID | Created by | When it changes | Purpose |
|---|---|---|---|
| `session_id` in the request | `POST /api/v1/chat/sessions` | Once per conversation | Groups all messages and runs in one conversation |
| `client_message_id` | Frontend/browser | Every new message, approval or rejection | Idempotency key that prevents processing one user action twice |
| HTTP `request_id` | Backend request middleware | Every HTTP attempt | Correlates the response, application logs and `chat_runs.request_id` |
| `run_id` | PostgreSQL | Every accepted graph turn | Identifies one LangGraph execution |
| `input_message_id` | PostgreSQL | Every accepted graph turn | Identifies the stored user message/confirmation |
| `output_message_id` | PostgreSQL | Every completed graph turn | Identifies the stored assistant response |
| `pending_action_id` | Backend (only its hash is stored in Supabase) | Every update proposal | One-time secret used only to approve or reject that proposal |

The most important rule is:

```text
same conversation  -> keep the same session_id
new user action    -> generate a new client_message_id
HTTP retry         -> keep the original client_message_id
confirmation       -> same session_id, new client_message_id, returned pending_action_id
```

The UUIDs shown in this README are examples only. Swagger does not automatically replace them.
Generate a real new UUID before each new action.

PowerShell:

```powershell
$clientMessageId = [guid]::NewGuid().ToString()
$clientMessageId
```

Python:

```powershell
python -c "import uuid; print(uuid.uuid4())"
```

Browser/TypeScript:

```typescript
const clientMessageId = crypto.randomUUID();
```

Example: two new messages in one conversation use one session UUID but different client UUIDs:

```json
{
  "session_id": "624cce1f-7e28-4604-aa8a-10403e5b90de",
  "client_message_id": "a95c41a8-2962-4a4e-bf20-b61581dcdb86",
  "message": "What is my phone number?"
}
```

```json
{
  "session_id": "624cce1f-7e28-4604-aa8a-10403e5b90de",
  "client_message_id": "39f9cd4a-fec5-48d4-89cc-4de9aef47473",
  "message": "What is my email address?"
}
```

If the second request incorrectly reuses `a95c41a8-2962-4a4e-bf20-b61581dcdb86`, Careline returns:

```json
{
  "error": {
    "code": "chat_turn_already_processed",
    "message": "This client message was already processed. Use a new ID for a new action.",
    "request_id": "9ee13139-4a6e-4e96-964e-2b4e5632a5af"
  }
}
```

This `409 Conflict` is a protection result, not a model failure. PostgreSQL found the same
`(session_id, client_message_id)` before creating a new run. The classifier and patient-profile tool
were not called, the earlier user message was not duplicated, and no update was performed twice.
The response `request_id` is only the correlation ID for this rejected HTTP attempt; it is not a
replacement `client_message_id`.

The current API intentionally does not replay the earlier assistant response when it sees a
duplicate ID. A proposal response may contain a short-lived `pending_action_id`, so blindly replaying
stored output would have unsafe or misleading semantics. The client receives `409` and reconciles
the authoritative session history instead.

For a genuinely new action, generate a new UUID and submit the request again. Keep the existing
`session_id` if the conversation should continue:

```powershell
$body = @{
    session_id = $session.id.ToString()
    client_message_id = [guid]::NewGuid().ToString()
    message = "What is my phone number?"
} | ConvertTo-Json

Invoke-RestMethod `
    -Method Post `
    -Uri "http://127.0.0.1:8000/api/v1/chat" `
    -Headers $headers `
    -ContentType "application/json" `
    -Body $body
```

If an HTTP request timed out and it is unclear whether the backend accepted it, do not immediately
generate a new ID. Retry once with the original `client_message_id`:

- A normal `200` means the original attempt had not been accepted and this attempt completed it.
- `409 chat_turn_already_processed` means the original ID was already stored. Load
  `GET /api/v1/chat/sessions/{session_id}/messages` and reconcile the displayed history instead of
  processing the user action again.
- If history contains the user input but no following assistant message, inspect the associated run
  in the development Supabase SQL editor. A `failed` run records a safe `error_code`; ask the user
  before deliberately retrying the action with a new UUID.

During development, verify a specific client ID with:

```sql
select
  m.id as input_message_id,
  m.session_id,
  m.client_message_id,
  m.sequence_number,
  r.id as run_id,
  r.status as run_status,
  r.output_message_id,
  r.error_code,
  r.created_at,
  r.completed_at
from public.chat_messages as m
left join public.chat_runs as r
  on r.input_message_id = m.id
 and r.session_id = m.session_id
 and r.patient_id = m.patient_id
where m.session_id = 'replace-with-session-uuid'
  and m.client_message_id = 'replace-with-client-message-uuid';
```

Do not delete history rows or modify the unique constraint to bypass this response. For a new
action, the correct fix is always a newly generated `client_message_id`.

#### Logging and privacy behavior

Chat logs record only operational events such as `chat_turn_started`, `chat_intent_classified`,
`chat_turn_completed`, `chat_turn_failed_recorded`,
`chat_update_proposal_created`, `chat_pending_action_consumed`, `chat_update_cancelled`, and the safe
attribute key where useful. They deliberately exclude the message text, extracted value, patient
UUID, access token, action ID, returned profile, model response and external exception text.

#### Manual smoke test for read and update

Use a development/test patient, never a real patient's account. The user must already exist in
Supabase Auth and have a matching `public.patients` row created by the signup trigger.

**1. Start the backend**

From `backend/`:

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload
```

Keep this terminal running. The API is available at `http://127.0.0.1:8000`.

**2. Generate a test-patient token**

Open a second PowerShell terminal in `backend/`:

```powershell
.\.venv\Scripts\Activate.ps1
python -m scripts.get_access_token
```

Enter the test patient's email and password, then copy the printed access token. Treat it like a
temporary password.

**3. Test with Swagger UI**

1. Open `http://127.0.0.1:8000/docs`.
2. Click **Authorize**.
3. Paste only the access token; Swagger adds `Bearer` automatically.
4. Run `GET /api/v1/auth/verify` and expect `{"authenticated": true}`.
5. Run `GET /api/v1/patients/me` and expect the complete profile.
6. Run `GET /api/v1/patients/me` with `fields=email,phone` and expect only those fields.
7. Run `PATCH /api/v1/patients/me/attributes/phone` with the body below:

```json
{
  "value": "+16045550999"
}
```

8. Expect `200` and a complete profile containing the new phone number.
9. Run the filtered GET again and confirm that the new value was persisted.

**4. Repeat the test from PowerShell**

Paste the token into a temporary in-memory variable. It remains visible in this terminal process,
so close the terminal after testing:

```powershell
$token = Read-Host "Paste Supabase access token"
$headers = @{ Authorization = "Bearer $token" }
```

Read the full profile:

```powershell
Invoke-RestMethod `
  -Method Get `
  -Uri "http://127.0.0.1:8000/api/v1/patients/me" `
  -Headers $headers
```

Read selected fields:

```powershell
Invoke-RestMethod `
  -Method Get `
  -Uri "http://127.0.0.1:8000/api/v1/patients/me?fields=email,phone" `
  -Headers $headers
```

Update the phone number:

```powershell
$body = @{ value = "+16045550999" } | ConvertTo-Json

Invoke-RestMethod `
  -Method Patch `
  -Uri "http://127.0.0.1:8000/api/v1/patients/me/attributes/phone" `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $body
```

Read the phone again to prove persistence:

```powershell
Invoke-RestMethod `
  -Method Get `
  -Uri "http://127.0.0.1:8000/api/v1/patients/me?fields=phone" `
  -Headers $headers
```

**5. Test expected failures**

Invalid phone format should return `422 invalid_attribute_value`:

```powershell
$body = @{ value = "604-555-0123" } | ConvertTo-Json
Invoke-RestMethod `
  -Method Patch `
  -Uri "http://127.0.0.1:8000/api/v1/patients/me/attributes/phone" `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $body
```

Protected email update should return `400 attribute_not_editable`:

```powershell
$body = @{ value = "new@example.com" } | ConvertTo-Json
Invoke-RestMethod `
  -Method Patch `
  -Uri "http://127.0.0.1:8000/api/v1/patients/me/attributes/email" `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $body
```

Unknown read field should return `400 invalid_fields`:

```powershell
Invoke-RestMethod `
  -Method Get `
  -Uri "http://127.0.0.1:8000/api/v1/patients/me?fields=password" `
  -Headers $headers
```

PowerShell may display non-2xx responses as command errors. The response body still contains the
Careline error code and request ID. The matching request ID appears in the backend's safe JSON logs.

When finished, remove the in-memory token and stop the server:

```powershell
Remove-Variable token
```

Press `Ctrl+C` in the backend terminal.

### Step 2.8: Durable chat history database and RLS

Migrations:

- `supabase/migrations/20261007120000_create_chat_history_tables.sql`
- `supabase/migrations/20261007160000_add_atomic_chat_turn_functions.sql`
- `supabase/migrations/20261007170000_serialize_chat_session_turns.sql`
- `supabase/migrations/20261007180000_add_durable_pending_actions.sql`
- `supabase/migrations/20261007190000_add_cross_session_memory.sql`

- [x] Add a server-generated UUID for every Careline conversation.
- [x] Store ordered user-visible messages separately from internal graph execution records.
- [x] Store model decisions, graph/tool events, token usage, latency and safe error codes.
- [x] Add durable, expiring confirmation actions that store only a SHA-256 token hash.
- [x] Record which previous sessions were used to bootstrap a new conversation.
- [x] Bind every child row to both `session_id` and `patient_id` with composite foreign keys.
- [x] Cascade all chat data when its patient or parent session is deleted.
- [x] Add constraints for roles, message types, intents, states, JSON shapes and timestamp consistency.
- [x] Add indexes for recent-session lists, message pagination, run inspection and action expiry.
- [x] Enable RLS on all six tables and revoke all anonymous access.
- [x] Allow authenticated patients to select only their own sessions and messages.
- [x] Keep inserts/updates/deletes and all internal audit tables backend-only through `service_role`.

Deployment verification completed on October 7, 2026:

- PostgreSQL parser accepted all 48 migration statements.
- `npx supabase db push --dry-run` identified only this migration as pending.
- The migration was applied successfully to the linked `careline-dev` Supabase project.
- Local and remote migration histories now both include `20261007120000`.
- `npx supabase db lint --linked --level error` reports `No schema errors found`.
- Steps 1 and 2 of the Python integration are now implemented: repository/service code and four
  authenticated session/history endpoints.
- The atomic-turn migration `20261007160000` was dry-run, applied to linked `careline-dev`, and
  passed the linked schema linter on October 7, 2026.
- The follow-up `20261007170000` migration was dry-run and applied to serialize distinct concurrent
  turns within one session.
- The `20261007180000` migration was dry-run and applied to persist proposal hashes atomically and
  consume confirmations transactionally.
- The `20261007190000` migration was dry-run and applied to create new sessions together with an
  audited, bounded set of prior summary sources.
- Local and remote migration histories contain all versions through `20261007190000`, and the final
  linked database lint reports `No schema errors found`.
- Step 3 connects `POST /api/v1/chat` to messages, runs, tool events and session timestamps.
- Step 4 connects confirmation proposals to durable `chat_pending_actions` rows.
- Step 5 generates summaries on close and supplies audited prior-session memory to new sessions.
- Backend Ruff checks pass and all 150 Python tests pass after that integration.

`POST /api/v1/chat` now requires an active owned session and writes its input, run, sanitized tool
events and assistant response. Update proposals and one-time confirmations survive process restarts
and work across API workers. Closed conversations now provide bounded, auditable summary context to
newly created sessions.

#### Implemented Step 1: Python persistence layer

These files turn the SQL tables into a safe backend interface:

| File | Responsibility |
|---|---|
| `backend/app/schemas/chat_history.py` | Strict internal database records and smaller public response models |
| `backend/app/repositories/chat_history_repository.py` | Low-level, bounded Supabase queries |
| `backend/app/services/chat_history_service.py` | Ownership checks, state transitions, cursor handling, database-result validation and safe logs |
| `backend/app/deps.py` | Builds one repository from a patient JWT client plus the server-only service-role client |
| `backend/app/core/errors.py` | Converts expected history failures to stable HTTP error JSON |

The two-client split is intentional:

```text
Authenticated request
  |
  +-- verified JWT sub ---------------------------- patient_id
  |
  +-- patient-scoped Supabase client -- SELECT --> RLS checks auth.uid()
  |
  +-- service-role Supabase client ---- WRITE  --> backend sets patient_id from JWT sub
```

Reads use the patient access token, so the database applies the existing RLS policy. Writes use the
service-role client because the migration deliberately gives authenticated browser clients no
insert/update permission. Every privileged update includes both `session_id` and the verified
`patient_id`. The request supplies a conversation `session_id`, but the backend verifies that it
belongs to the JWT patient; `patient_id` can never be supplied in request JSON.

Database rows include `patient_id` for internal validation. Public response models deliberately
remove that redundant owner ID. Every returned database row is parsed through a strict Pydantic
model before it reaches the browser. A malformed or ownership-mismatched row becomes a safe server
error rather than being silently returned.

Session list and message list queries request `limit + 1` rows. The extra row tells the service
whether another page exists without loading the full history. `next_cursor` is a URL-safe opaque
encoding of the next offset; callers must treat it as an uninterpreted string. The current cursor is
suitable for this milestone. A future high-volume release can change the internal cursor to
timestamp/UUID keyset pagination while keeping the same response field.

#### Implemented Step 2: session and history APIs

All four routes require `Authorization: Bearer <Supabase access token>`:

| Method and path | Purpose | Normal result |
|---|---|---|
| `POST /api/v1/chat/sessions` | Start one conversation | `201` and the new session |
| `GET /api/v1/chat/sessions?limit=20&cursor=...` | List owned conversations, newest first | `200` page |
| `GET /api/v1/chat/sessions/{session_id}/messages?limit=30&cursor=...` | Read owned messages in sequence order | `200` page |
| `POST /api/v1/chat/sessions/{session_id}/close` | Mark an active conversation closed | `200` closed session |

Limits are bounded from 1 through 100. Closing an already closed session is idempotent: it returns
the same closed session rather than failing or writing again. An archived session cannot be closed.
An absent session and a session owned by somebody else both return the same `404`; this avoids
revealing whether another patient's conversation UUID exists.

##### Start a session

The title is optional. It is trimmed and limited to 200 characters. Do not send `patient_id`, model
name, prompt version, status or timestamps; the backend/database owns those values.

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/chat/sessions" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"Medication question"}'
```

Example `201` response (UUIDs and timestamps will differ):

```json
{
  "id": "624cce1f-7e28-4604-aa8a-10403e5b90de",
  "title": "Medication question",
  "status": "active",
  "summary": null,
  "model_name": "gpt-5.4-mini",
  "prompt_version": "patient-router-v1",
  "started_at": "2026-10-07T09:00:00Z",
  "last_message_at": "2026-10-07T09:00:00Z",
  "ended_at": null,
  "created_at": "2026-10-07T09:00:00Z",
  "updated_at": "2026-10-07T09:00:00Z"
}
```

An empty JSON object is also valid and creates the display title `New conversation`:

```json
{}
```

##### List sessions

```bash
curl "http://127.0.0.1:8000/api/v1/chat/sessions?limit=20" \
  -H "Authorization: Bearer $TOKEN"
```

Example response:

```json
{
  "items": [
    {
      "id": "624cce1f-7e28-4604-aa8a-10403e5b90de",
      "title": "Medication question",
      "status": "active",
      "summary": null,
      "model_name": "gpt-5.4-mini",
      "prompt_version": "patient-router-v1",
      "started_at": "2026-10-07T09:00:00Z",
      "last_message_at": "2026-10-07T09:00:00Z",
      "ended_at": null,
      "created_at": "2026-10-07T09:00:00Z",
      "updated_at": "2026-10-07T09:00:00Z"
    }
  ],
  "next_cursor": null
}
```

When `next_cursor` is not null, pass it unchanged on the next request:

```text
GET /api/v1/chat/sessions?limit=20&cursor=<next_cursor>
```

##### Read a session's messages

```bash
curl "http://127.0.0.1:8000/api/v1/chat/sessions/624cce1f-7e28-4604-aa8a-10403e5b90de/messages?limit=30" \
  -H "Authorization: Bearer $TOKEN"
```

A newly created session returns an empty list until its first successful `POST /api/v1/chat` turn.
After a profile-read turn, the response resembles:

```json
{
  "session_id": "624cce1f-7e28-4604-aa8a-10403e5b90de",
  "items": [
    {
      "id": "461d62d3-8df1-40f6-978d-37ddc1c810e2",
      "session_id": "624cce1f-7e28-4604-aa8a-10403e5b90de",
      "sequence_number": 1,
      "role": "user",
      "message_type": "text",
      "content": "What are my email address and phone number?",
      "structured_data": null,
      "client_message_id": "a95c41a8-2962-4a4e-bf20-b61581dcdb86",
      "created_at": "2026-10-07T09:01:00Z"
    },
    {
      "id": "bd89aa7d-7c21-49ab-b909-086380ddbc91",
      "session_id": "624cce1f-7e28-4604-aa8a-10403e5b90de",
      "sequence_number": 2,
      "role": "assistant",
      "message_type": "profile_result",
      "content": "Here are the requested details from your patient profile.",
      "structured_data": {
        "action": "answer",
        "message": "Here are the requested details from your patient profile.",
        "requires_confirmation": false,
        "data": {
          "email": "patient@example.com",
          "phone": "+16045550123"
        }
      },
      "client_message_id": null,
      "created_at": "2026-10-07T09:01:01Z"
    }
  ],
  "next_cursor": null
}
```

##### Close a session

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/chat/sessions/624cce1f-7e28-4604-aa8a-10403e5b90de/close" \
  -H "Authorization: Bearer $TOKEN"
```

The response uses the same session shape as creation, with `status` set to `closed` and `ended_at`
set by the backend.

##### Error response examples

| HTTP | Code | Meaning |
|---|---|---|
| `400` | `invalid_history_cursor` | The supplied cursor is malformed or outside the safe range |
| `401` | `authentication_required` / `invalid_token` | Bearer token is absent or cannot be verified |
| `404` | `chat_session_not_found` | No session is visible to this patient |
| `409` | `chat_session_state_conflict` | The requested status transition is not allowed |
| `409` | `chat_turn_already_processed` | This `client_message_id` was already accepted |
| `422` | `request_validation_failed` | UUID, limit, cursor length or JSON body is invalid |
| `500` | `invalid_chat_history_data` | Stored data violated the strict application contract |
| `503` | `chat_history_unavailable` | Supabase could not complete the operation |

Errors keep the existing safe envelope and include the request ID for log correlation:

```json
{
  "error": {
    "code": "chat_session_not_found",
    "message": "The chat session was not found.",
    "request_id": "a-request-correlation-uuid"
  }
}
```

Logging records event names such as `chat_session_created`, `chat_sessions_retrieved`,
`chat_messages_retrieved` and `chat_session_closed`, together with safe counts, outcome and error
type. It deliberately does not log patient UUIDs, titles, summaries, message content, structured
data, access tokens or raw database/provider errors.

#### Implemented Step 3: persistent LangGraph turns

`POST /api/v1/chat` now performs this sequence:

```text
1. JWT identifies patient
2. PostgreSQL locks and verifies active session
3. PostgreSQL stores user message + started run atomically
4. Backend loads at most N earlier messages through patient RLS
5. LangGraph classifies and calls an allowed deterministic tool
6. PostgreSQL stores assistant message + decision + safe events + completion atomically
7. API returns response plus session/run/input/output UUIDs
```

The migration functions `careline_start_chat_turn` and `careline_complete_chat_turn` lock the parent
session row while allocating sequence numbers. Concurrent requests therefore cannot receive the
same `(session_id, sequence_number)`. `careline_fail_chat_turn` records a safe failure category and
latency when graph execution fails. A distinct request arriving while another turn is still
`started` receives `409 chat_session_state_conflict`, so it cannot run against incomplete context.

`client_message_id` is optional for compatibility but strongly recommended. The frontend generates
one UUID for a user action and keeps it while retrying that exact request. Reusing it returns a
neutral `409` and prevents the classifier or profile tool from running twice. A genuinely new
message or confirmation must use a new UUID.

The classifier receives bounded earlier messages from that same session plus the audited summaries
selected when this session was created. The newly stored input is excluded from the context query
and supplied once as the current message. Same-session messages are selected newest-first for an
efficient bounded query, then reversed before model invocation so the model sees chronological
order. Prior summaries remain separately labelled as untrusted historical data.

Stored tool events include safe metadata such as node/tool name, duration, requested field names,
intent, field count and outcome. They do not store access tokens, pending-action IDs, exception
messages, profile values or returned profile objects. The full classifier decision is stored only in
the backend-only `chat_runs` table. User-visible profile results may contain PHI in
`chat_messages.structured_data` and remain protected by RLS.

The raw pending-action token is returned only in the immediate HTTPS proposal response. It is not
copied into the stored user message, assistant `structured_data`, tool events or logs. Confirmations
are stored as neutral text such as `Confirmed the proposed profile change.` rather than storing the
token submitted by the browser.

#### Implemented Step 4: durable, one-time confirmations

In simple language, a proposed update is now saved in Supabase instead of the API process's memory.
That means an API restart does not make the Confirm button stop working, and any API worker can
handle the follow-up confirmation.

The proposal flow is:

```text
1. Patient asks to change a profile field.
2. Backend validates the field and value.
3. Backend creates a long random pending_action_id.
4. Backend calculates SHA-256(pending_action_id).
5. One PostgreSQL transaction saves:
   - the assistant confirmation-request message;
   - the completed graph run and safe events; and
   - the pending action's hash, payload, owner, session and expiry.
6. Only the raw pending_action_id is returned to the browser.
```

The approval/rejection flow is:

```text
1. Browser sends the same session_id, a new client_message_id and the pending_action_id.
2. Backend hashes the received pending_action_id.
3. careline_consume_pending_action locks the matching database row.
4. PostgreSQL verifies patient, session, expiry and unused state.
5. PostgreSQL marks the row approved/rejected and returns the saved payload exactly once.
6. On approval, the deterministic patient service performs the update.
   On rejection, no profile update is performed.
```

The raw token is treated like a short-lived secret. Supabase contains only a 64-character lowercase
hash, so a database reader cannot copy the stored value and use it as the browser's confirmation
token. The token is also deliberately excluded from application logs, user/assistant history,
structured message data, run decisions and tool events.

Unknown, expired, already-used, wrong-patient and wrong-session tokens all return the same
`pending_action_not_found` behavior. This avoids revealing which part of a token lookup failed.
Both approval and rejection consume the action, so neither decision can be replayed.

| File | Durable-confirmation responsibility |
|---|---|
| `backend/app/agents/pending_actions.py` | Generates random tokens, hashes them and validates the database consume result |
| `backend/app/agents/patient_graph.py` | Builds proposals and routes approved/rejected confirmations |
| `backend/app/services/chat_turn_service.py` | Passes a validated pending-action draft into the atomic completion transaction |
| `backend/app/repositories/chat_history_repository.py` | Calls the server-only consume database function |
| `supabase/migrations/20261007180000_add_durable_pending_actions.sql` | Atomically inserts and transactionally consumes pending rows |

To prove durability manually, create a proposal, copy its `pending_action_id`, restart the FastAPI
server, and then approve it using the same `session_id` and a new `client_message_id`. The update
should succeed. Repeat the exact confirmation with another new `client_message_id`; it should fail
because the action was already consumed. You can also create a second proposal, reject it, and read
the profile to confirm that its value was not changed.

For a safe database check in the Supabase SQL editor, inspect metadata rather than displaying the
patient-supplied payload:

```sql
select
  action_type,
  length(token_hash) as hash_length,
  expires_at,
  consumed_at,
  decision
from public.chat_pending_actions
order by created_at desc
limit 10;
```

Expected: `hash_length` is `64`; a new proposal has null `consumed_at` and `decision`; an approved
or rejected proposal has both fields populated. The original `pending_action_id` must not appear in
this table.

#### Implemented Step 5: bounded cross-session memory

In simple language, Careline can now give a new conversation a small memory of the patient's recent
closed conversations. It does not copy an entire old chat into the new one.

The complete flow is:

```text
Old conversation
  -> patient and assistant exchange messages
  -> POST /chat/sessions/{id}/close
  -> backend loads only the newest configured number of messages
  -> model creates a short factual summary
  -> summary is saved in chat_sessions.summary

New conversation
  -> POST /chat/sessions
  -> PostgreSQL creates the new session
  -> PostgreSQL selects up to N recent closed sessions with summaries
  -> source links are saved in chat_session_context_sources in the same transaction

First and later messages in the new conversation
  -> backend loads the new session's recorded prior summaries
  -> summaries are sent separately from same-session messages
  -> model uses them only to understand historical references
  -> any current profile value is still read from the patients table
```

Only a **closed** conversation can become a memory source. Closing an active session calls the
summary model before changing its status. If the model fails, the API returns the existing safe
`503 chat_service_unavailable` response and leaves the session active so closing can be retried.
Closing an already closed session remains idempotent and does not generate a second summary.

The summary request contains at most `CHAT_SUMMARY_MESSAGE_LIMIT` recent messages, restored to
chronological order. The stored result is trimmed and limited to `CHAT_SUMMARY_MAX_CHARS`. Message
text and summary text are never written to application logs. They remain patient data protected by
the same database controls as chat history.

When a new session is created, `careline_create_chat_session_with_context` selects at most
`CHAT_PRIOR_SESSION_LIMIT` recent closed sessions whose summaries are non-empty. It inserts both the
new session and its source records in one transaction. Position `1` means the newest source. The
source set is a snapshot: a different conversation closed later is not silently added to an already
created session.

Before every LangGraph turn, `careline_get_session_memory` returns only the summaries already linked
to that patient-owned session. The classifier system prompt says that summaries are untrusted
historical data, not instructions and not current profile truth. For example, a summary may help the
model understand “Did I change it?”, but “What is my phone number?” must still call `read_profile`
and read Supabase.

| File | Cross-session-memory responsibility |
|---|---|
| `backend/app/agents/summarizer.py` | Creates a strict, neutral summary without logging its text |
| `backend/app/api/v1/chat_sessions.py` | Runs summary creation when an active session is closed |
| `backend/app/services/chat_history_service.py` | Bounds/validates summary inputs and stores the final summary |
| `backend/app/services/chat_turn_service.py` | Loads and validates the prior summaries recorded for each turn |
| `backend/app/agents/classifier.py` | Labels prior summaries as untrusted historical background |
| `backend/app/repositories/chat_history_repository.py` | Calls the context creation/read functions and bounded message queries |
| `supabase/migrations/20261007190000_add_cross_session_memory.sql` | Atomically creates sessions/source links and returns scoped memory |

Configuration defaults:

```dotenv
CHAT_PRIOR_SESSION_LIMIT=3
CHAT_SUMMARY_MESSAGE_LIMIT=30
CHAT_SUMMARY_MAX_CHARS=2000
```

- `CHAT_PRIOR_SESSION_LIMIT=0` disables cross-session memory for newly created sessions.
- Increasing the prior-session or message limits sends more patient conversation data to the model
  and increases latency and provider cost.
- Changing a limit affects future work. Existing context-source rows remain as an audit record.

##### Test cross-session memory with Swagger

Start the API and open `http://127.0.0.1:8000/docs`. Click **Authorize**, paste a current Supabase
access token without adding the word `Bearer`, authorize, and close the dialog.

Follow this order. A new session captures its eligible memory sources only when it is created, so
session A must be closed and summarized before session B is created.

1. Open `POST /api/v1/chat/sessions`, click **Try it out**, and create session A:

   ```json
   {
     "title": "First contact information conversation"
   }
   ```

   A successful response is `201`. Copy its `id` as `session-A-id`.

2. Open `POST /api/v1/chat` and add a message to session A:

   ```json
   {
     "session_id": "<session-A-id>",
     "client_message_id": "<new-UUID>",
     "message": "Show me my phone number."
   }
   ```

   Generate a different `client_message_id` for every genuinely new action. In PowerShell, run
   `[guid]::NewGuid().ToString()`. The expected action is `answer` and the current phone value comes
   from the patient table.

3. Open `POST /api/v1/chat/sessions/{session_id}/close`, paste `session-A-id`, and execute it. The
   response should have `status: "closed"` and a non-empty `summary`. Exact summary wording can
   differ. If this returns `503 chat_service_unavailable`, verify `OPENAI_API_KEY` and
   `OPENAI_MODEL` in `backend/.env`, restart the API, and retry the close.

4. Open `POST /api/v1/chat/sessions` again and create session B:

   ```json
   {
     "title": "Cross-session memory test"
   }
   ```

   Copy the new `id` as `session-B-id`. The database automatically links eligible prior summaries;
   the request does not accept a source-session ID.

5. Open `POST /api/v1/chat` and test a historical reference in session B:

   ```json
   {
     "session_id": "<session-B-id>",
     "client_message_id": "<another-new-UUID>",
     "message": "Based on the profile field I reviewed in my previous conversation, read its current value from my patient profile."
   }
   ```

   When session A's summary identifies the phone field, the expected action is `answer` with the
   current `phone` value. The summary helps resolve the reference, but the value is still fetched
   from the live patient profile.

6. Open `GET /api/v1/chat/sessions`. Session A should be closed with a summary. Session B normally
   remains active with `summary: null` until it is also closed.

7. Check the backend's `chat_turn_started` log for session B. `prior_summary_count: 1` means one
   earlier summary was loaded. `context_message_count` counts only earlier messages inside session B.
   Logs must contain counts only—not message text, summary text, profile values, tokens or patient IDs.

##### If session B returns `unsupported`

An `unsupported` response means the classifier could not confidently map the new message to an
allowed profile read or single-field update. It does not, by itself, prove that database memory
failed. A vague message such as `Show me the same profile field` may still be rejected because the
classifier is deliberately instructed not to guess.

Check the `chat_turn_started` log:

- `prior_summary_count: 1` means memory loaded, but the model considered the wording or stored
  summary too ambiguous. Inspect session A's summary through the authenticated session-list API and
  retry with the more explicit historical-reference example above.
- `prior_summary_count: 0` means session B has no recorded summary source. Common causes are creating
  B before closing A, A having no summary, setting `CHAT_PRIOR_SESSION_LIMIT=0`, or testing against a
  backend process that was not restarted after configuration changes. Close A first, then create a
  completely new session B or C.

The classifier is intentionally conservative. Cross-session memory provides context; it does not
permit the model to invent an omitted field or treat old values as current database values.

Read-only Supabase verification:

```sql
select id, status, summary is not null as has_summary
from public.chat_sessions
order by created_at desc
limit 10;
```

Session A should be `closed` with `has_summary = true`. To verify session B's audit sources and
diagnose `unsupported`, run:

```sql
select
  source.position,
  source.session_id as new_session_id,
  source.source_session_id as previous_session_id,
  source.included_summary,
  old_session.status as source_status,
  old_session.summary is not null as source_has_summary,
  old_session.summary
from public.chat_session_context_sources as source
join public.chat_sessions as old_session
  on old_session.id = source.source_session_id
where source.session_id = '<session-B-id>'::uuid
order by source.position;
```

Expected: zero to `CHAT_PRIOR_SESSION_LIMIT` rows, and every included source was closed with a
summary. If the query returns no rows, no prior memory was attached to that new session.

`position` is the order of previous conversations supplied to the new session. It is not a message
number, database row number or LangGraph step:

| Position | Meaning |
|---:|---|
| `1` | Most recent eligible previous conversation |
| `2` | Second most recent eligible previous conversation |
| `3` | Third most recent eligible previous conversation |

Therefore, `position = 1`, `new_session_id = session B`, `previous_session_id = session A`, and
`included_summary = true` means: when B was created, Careline selected A as its newest eligible
previous conversation and supplied A's summary as historical context. With the default
`CHAT_PRIOR_SESSION_LIMIT=3`, positions can be `1`, `2`, and `3`; if only A has a usable summary,
only position `1` exists. Selection is ordered by newest `last_message_at`, with session UUID as a
stable tie-breaker. The context-source table records the relationship but does not duplicate the
summary or full messages.

#### Conversation session ID versus authentication session ID

A Careline chat session is a conversation, not a login session:

| ID | Created by | Meaning |
|---|---|---|
| Supabase JWT `session_id` | Supabase Auth | One login/refresh-token session |
| JWT `sub` | Supabase Auth | Permanent authenticated patient UUID |
| `chat_sessions.id` | Careline backend/PostgreSQL | One conversation UUID |
| `chat_runs.id` | Careline backend/PostgreSQL | One LangGraph execution UUID |
| `chat_messages.id` | Careline backend/PostgreSQL | One stored message UUID |

One user may have many authentication sessions and many chat sessions. Chat ownership always comes
from the verified JWT `sub`; the browser cannot select a different `chat_initiated_by` or acting
role. Migration `20261008120000` expanded this original patient-only design into the role-aware
model documented later in this README.

#### Tables and relationships

```text
auth.users + user_roles
  |
  +-- chat_sessions
        |
        +-- chat_messages
        |
        +-- chat_runs
        |     |
        |     +-- chat_tool_events
        |
        +-- chat_pending_actions
        |
        +-- chat_session_context_sources
```

Every table uses UUID primary keys generated by PostgreSQL. Child records repeat
`chat_initiated_by` and `chat_initiator_role` so role-aware history indexes do not require a join.
Composite foreign keys guarantee these values match the parent session.

##### `chat_sessions`

One row is one conversation:

| Column | Meaning |
|---|---|
| `id` | Server-generated conversation UUID |
| `chat_initiated_by`, `chat_initiator_role` | Authenticated owner UUID and acting role |
| `subject_patient_id` | Optional patient the conversation concerns |
| `title` | Optional display title, limited to 200 characters |
| `status` | `active`, `closed` or `archived` |
| `summary` | Bounded older-context summary; never live profile truth |
| `model_name` | Model selected for the session, such as `gpt-5.4-mini` |
| `prompt_version` | Application prompt version/hash, not the full system prompt |
| `started_at` | Conversation start |
| `last_message_at` | Recent-session ordering cursor |
| `ended_at` | Close/archive time |
| `created_at`, `updated_at` | Database audit timestamps |

The existing `public.set_updated_at()` trigger maintains `updated_at`. An active session cannot have
`ended_at`, and timestamps cannot move before `started_at`.

##### `chat_messages`

One row is one user-visible message:

| Column | Meaning |
|---|---|
| `id` | Message UUID |
| `session_id`, `chat_initiated_by`, `chat_initiator_role` | Composite ownership link |
| `sent_by_user_id`, `sent_by_role` | Individual sender (`assistant` has no user UUID) |
| `sequence_number` | Positive, unique ordering number within the session |
| `role` | `user`, `assistant` or `system_notice` |
| `message_type` | Text, profile result, confirmation request/result, error or notice |
| `content` | User-visible message text |
| `structured_data` | Optional JSON object used by the UI; may contain PHI |
| `client_message_id` | Frontend UUID used to make retries idempotent |
| `created_at` | Message creation time |

`(session_id, sequence_number)` is unique, so message order is deterministic. A repeated
`client_message_id` in the same session is rejected rather than creating and processing a duplicate
message after a network retry.

##### `chat_runs`

One row records one LangGraph invocation:

| Column group | Stored details |
|---|---|
| Ownership | Session, initiator UUID, acting role and Careline HTTP `request_id` |
| Message links | Input message and optional output message |
| Routing | Intent and validated `decision_data` JSON |
| Lifecycle | `started`, `completed` or `failed` with timestamps |
| Model | Model name, prompt version and optional provider response ID |
| Usage | Input tokens, output tokens and total latency |
| Failure | Safe `error_code` only |

`decision_data` can contain patient-supplied data, so this table has no patient-client grant. Raw
provider errors, response bodies and stack traces are prohibited.

##### `chat_tool_events`

This backend-only table records ordered graph-node and tool activity for a run. It stores the node
name, tool name, sequence, lifecycle state, duration, safe error code, and sanitized JSON arguments
and result. It must never store database clients, JWTs, authorization headers or provider keys.

##### `chat_pending_actions`

This table is the active durable proposal store. It links a proposed action to its initiator role,
session, run and proposal message. The functions accept patient `update_profile` and admin
`create_patient`; the table reserves `delete_account` for a future deletion flow.

The browser will receive a random confirmation token. The backend will store only:

```text
SHA-256(raw confirmation token) -> pending action row
```

The database enforces a 64-character lowercase SHA-256 hex value, JSON-object payload, future
expiration, and consistent consumption state. A decision must be `approved` or `rejected`, and
`decision` and `consumed_at` must be set together. The raw confirmation token is never stored.

##### `chat_session_context_sources`

This backend-only table records which prior sessions contributed history to a new session. It stores
the target session, source session, context order, whether the summary was included, and an optional
message sequence range. Target and source must belong to the same initiator UUID and acting role,
and cannot be the same session.

This allows an audit to answer: “Which earlier conversations were supplied as context for this new
conversation?” It does not duplicate the actual messages.

#### RLS and grants

All six tables are in the exposed `public` schema, so the migration enables RLS on every table and
revokes all privileges from both `anon` and `authenticated` before adding the minimum required
grants.

| Role | Sessions | Messages | Runs/events/actions/context |
|---|---|---|---|
| `anon` | No access | No access | No access |
| `authenticated` | Select own rows | Select own rows | No direct access |
| `service_role` | Backend read/write | Backend read/write | Backend read/write |

Patient policies are equivalent to:

```sql
using ((select auth.uid()) = chat_initiated_by)
```

There are deliberately no authenticated insert/update/delete grants. All chat writes will pass
through FastAPI, which derives `chat_initiated_by` from the verified JWT and chooses the role from
the protected endpoint. A browser with the public
Supabase key therefore cannot fabricate an assistant response, model decision, tool result or
confirmation record. The service-role key bypasses RLS and must remain server-side; persistence
services must never accept a patient ID from request JSON.

Internal tables also have RLS with no patient policy. That default-deny boundary protects them even
if an authenticated grant is added accidentally later.

#### Indexes and expected query paths

| Index | Query supported |
|---|---|
| `(chat_initiated_by, chat_initiator_role, last_message_at desc, id desc)` | Recent conversations for one user and role |
| `(session_id, sequence_number)` unique | Ordered messages in one session |
| Existing message ownership/time indexes | Cursor-based message history |
| `(session_id, created_at desc, id desc)` | Recent graph runs/tool events |
| Existing initiator/expiry pending-action index | Expired/pending action cleanup |
| `source_session_id` | Context-source trace lookup |

History APIs will use stable ordering and bounded cursor pagination rather than loading every stored
message in one response.

#### Data stored and explicitly prohibited

Stored data may include PHI and therefore requires the same protection as the patient profile:

- User and assistant messages
- Structured UI results
- Summaries
- Model decisions
- Sanitized tool arguments/results
- Pending action payloads
- Token usage, latency and safe status/error categories

Never store:

- Supabase access/refresh tokens or JWT claims copied from the request
- Authorization headers, cookies or passwords
- OpenAI/Supabase API keys
- Database URLs or client objects
- Plaintext confirmation tokens
- Raw provider request/response headers or error bodies
- Full exception text or stack traces

#### Applying the migration

From the repository root, review pending changes first:

```powershell
npx supabase migration list
npx supabase db diff --linked
```

Apply to the linked development project only after reviewing the project reference:

```powershell
npx supabase db push
```

For a disposable local database, Docker Desktop must be running. `db reset` deletes and recreates
the local Supabase database, then applies every migration from the beginning:

```powershell
npx supabase start
npx supabase db reset
```

Do not run `db reset --linked` against a shared or production project.

#### Verification queries

Run these read-only queries in the Supabase SQL editor after applying the migration.

Confirm all tables exist:

```sql
select table_name
from information_schema.tables
where table_schema = 'public'
  and table_name in (
    'chat_sessions',
    'chat_messages',
    'chat_runs',
    'chat_tool_events',
    'chat_pending_actions',
    'chat_session_context_sources'
  )
order by table_name;
```

Expected: six rows.

Confirm RLS is enabled:

```sql
select relname as table_name, relrowsecurity as rls_enabled
from pg_class
where relnamespace = 'public'::regnamespace
  and relname like 'chat_%'
order by relname;
```

Every returned row must have `rls_enabled = true`.

Confirm the two patient-visible policies:

```sql
select tablename, policyname, roles, cmd, qual
from pg_policies
where schemaname = 'public'
  and tablename like 'chat_%'
order by tablename, policyname;
```

Expected policies:

```text
chat_messages  chat_messages_select_own  SELECT
chat_sessions  chat_sessions_select_own  SELECT
```

Confirm authenticated privileges:

```sql
select
  has_table_privilege('authenticated', 'public.chat_sessions', 'select')
    as can_select_sessions,
  has_table_privilege('authenticated', 'public.chat_sessions', 'insert')
    as can_insert_sessions,
  has_table_privilege('authenticated', 'public.chat_messages', 'select')
    as can_select_messages,
  has_table_privilege('authenticated', 'public.chat_runs', 'select')
    as can_select_runs,
  has_table_privilege('anon', 'public.chat_sessions', 'select')
    as anon_can_select_sessions;
```

Expected:

```text
can_select_sessions      true
can_insert_sessions      false
can_select_messages      true
can_select_runs          false
anon_can_select_sessions false
```

#### Next implementation step

Steps 1 through 5 are complete. Same-session history, durable confirmations and bounded
cross-session summaries are implemented. The next work should harden lifecycle management:

1. Define retention periods for messages, runs, summaries, consumed pending actions and tool events.
2. Add a scheduled cleanup operation that is auditable and deletes only expired eligible records.
3. Decide whether archived sessions may remain memory sources or must be removed from future context.
4. Add a user-facing endpoint to archive a session and, later, delete one conversation safely.
5. Add live multi-worker confirmation and cross-session integration tests against a dedicated test
   Supabase project.

### Step 2.9: Endpoint 3, delete patient

`DELETE /api/v1/patients/me`

- [ ] Delete the patient's Storage files, if any.
- [ ] Delete the auth user through the Supabase Admin API (this cascades to `patients`).
- [ ] Return 204.
- [ ] Structure the code (for example, `patient_service.delete_patient`) so soft delete can replace it later.
- [ ] Add a stricter rate limit to this route.

### Step 2.10: Errors, logging and rate limiting

- [ ] Global exception handlers that return a consistent JSON error format (`{"error": {"code": ..., "message": ...}}`).
- [ ] Structured logging with a request ID on every log line.
- [ ] **Never log tokens, passwords or patient data (PHI).**
- [ ] Rate limiting with `slowapi` (for example, a moderate global limit and a low limit on `DELETE`).
- [ ] Confirm `/docs` accurately describes all endpoints, request bodies and responses.

### Step 2.11: Manual testing

- [ ] Get a real access token (log in through a small script or the Supabase dashboard).
- [ ] Call each endpoint from `/docs` using the **Authorize** button.
- [ ] Try failure cases: no token, expired token, bad key, bad value.

### Milestone 2 checklist

- [ ] All three endpoints work with a valid token and fail correctly without one
- [ ] Only whitelisted keys can be updated
- [ ] CORS limited to the frontend origin
- [ ] No secrets or PHI in logs

---

# Milestone 3: Frontend (Vite + React)

**Goal:** a modern, accessible, role-aware chat application for patients and administrators.
**Estimated time:** 3 to 4 days.

### Step 3.1: Base setup

- [x] Establish a custom responsive Careline design system with accessible native controls.
- [x] Set up React Router routes: `/login`, `/set-password`, `/patient/chat`, `/admin/chat`, and role-aware `/`.
- [x] Create one browser Supabase client from `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY`.
- [x] Validate browser configuration and show a safe setup screen when variables are missing.

### Step 3.2: Auth state

- [x] `AuthProvider` stores session/user, restores it, listens to `onAuthStateChange`, and exposes sign-in, sign-out, and password update.
- [x] `ProtectedRoute` redirects unauthenticated users and shows a loading state during restoration.
- [x] Supabase persists the session and refreshes access tokens automatically.
- [x] The protected backend determines whether `/` opens the admin or patient workspace.

### Step 3.3: Sign-up and login pages

- [x] Responsive email/password login form with browser validation and safe friendly errors.
- [x] Administrator accounts land on `/admin/chat`; other authenticated users land on `/patient/chat`.
- [x] Invited patients can finish account setup at `/set-password`.
- [ ] Public self-sign-up is intentionally deferred because the current patient flow is invitation-based.

### Step 3.4: API client

In `lib/apiClient.js`:

- [x] Wrap `fetch` and attach the freshest `Authorization: Bearer <access_token>`.
- [x] Normalize Careline error envelopes, network failures, status codes, codes, and request IDs.
- [x] Avoid logging tokens, email addresses, messages, or patient attributes.
- [ ] Add a single automatic UI retry/redirect policy for an API request that receives `401`.

#### Implemented frontend Step 1 file sequence

```text
main.jsx
  -> App.jsx router
  -> AuthProvider restores Supabase session
  -> ProtectedRoute requires authentication
  -> RoleLandingPage asks protected admin history endpoint for acting role
  -> /admin/chat or /patient/chat
  -> apiClient attaches the current bearer token to future FastAPI calls
```

Important files:

| File | Purpose |
|---|---|
| `frontend/src/lib/runtimeConfig.js` | Validates browser-safe Vite variables |
| `frontend/src/lib/supabase.js` | Single persistent Supabase browser client |
| `frontend/src/lib/apiClient.js` | Authenticated FastAPI calls and normalized errors |
| `frontend/src/auth/AuthContext.jsx` | Session restoration and Auth operations |
| `frontend/src/auth/ProtectedRoute.jsx` | Route protection and loading state |
| `frontend/src/pages/LoginPage.jsx` | Modern role-neutral login |
| `frontend/src/pages/SetPasswordPage.jsx` | Invited-patient password completion |
| `frontend/src/pages/RoleLandingPage.jsx` | Backend-verified admin/patient landing |

To run the frontend, configure `frontend/.env`, start FastAPI on port 8000, then run `npm run dev`
from `frontend/` and open `http://localhost:5173`.

### Step 3.4A: Persistent chat workspace

- [x] Shared responsive session sidebar for patient and administrator roles.
- [x] Create, list, select, paginate, and close role-owned chat sessions.
- [x] Restore the selected session's ordered messages with cursor pagination.
- [x] Send patient turns to `POST /chat` and administrator turns to `POST /admin/chat`.
- [x] Generate a new browser UUID for every `client_message_id`.
- [x] Render structured profile results, update proposals, and patient-invitation proposals.
- [x] Require an explicit Confirm or Cancel click before any update or invitation side effect.
- [x] Show safe API errors and backend request IDs without logging tokens or patient data.
- [x] Disable sending for closed sessions, pending requests, and unresolved action proposals.
- [x] Add Vitest and Testing Library coverage for role routing and chat API request contracts.

#### Simple end-to-end explanation

The frontend never starts by sending a loose chat message. It first creates a database-backed
session. Every later message includes that session's server-generated ID and a new browser-generated
message ID. FastAPI obtains the acting user from the signed Supabase access token, so the frontend
does not send or choose `chat_initiated_by`.

```text
Login
  -> backend confirms patient or admin role
  -> frontend lists only that user's sessions
  -> user opens or creates a session
  -> frontend loads that session's stored messages
  -> user sends a message with a fresh client_message_id
  -> LangGraph returns and stores the assistant response
  -> frontend reloads the durable timeline
  -> if an action was proposed, frontend asks for explicit confirmation
```

Patients use `/chat/sessions` and `/chat`. Administrators use `/admin/chat/sessions` and
`/admin/chat`. Both use the same six chat tables; the role-aware ownership columns and backend
authorization prevent their conversations from mixing.

The confirmation token is intentionally **not stored** inside `chat_messages`. It exists only in
the immediate HTTPS response and current React memory. A refreshed historical proposal is visible
for audit context, but it cannot be replayed; the user submits the request again for a new token.

#### Newest-message pagination fix

The live patient and administrator workspaces request message history with
`newest_first=true`. This fixes a subtle pagination problem: the default history order begins with
the oldest records, so a session longer than one page could save a new question and answer without
showing them on the first frontend page.

The repositories now read the newest database page in descending sequence order, while the service
reverses that page before returning it. The browser therefore receives the latest turn but still
renders it in natural top-to-bottom conversation order. Earlier cursor pages are prepended. A
request-sequence guard also prevents a slower response from a previously selected session from
overwriting the current session's messages, and the user's outgoing question is shown immediately
while LangGraph is processing it.

### Step 3.4B: Conversation organization

- [x] Generate a privacy-conscious title after the first successful turn.
- [x] Allow manual rename through patient-owned and administrator-owned APIs.
- [x] Search loaded conversation titles and summaries.
- [x] Filter sessions by active, closed, or archived status.
- [x] Archive only closed sessions so summary generation cannot be bypassed.
- [x] Restore archived sessions to closed, read-only state.
- [x] Enforce title validation, ownership, and status transitions on the backend.
- [x] Add frontend request-contract tests and backend lifecycle tests.

Session-management endpoints are:

```text
PATCH /api/v1/chat/sessions/{session_id}
POST  /api/v1/chat/sessions/{session_id}/archive
POST  /api/v1/chat/sessions/{session_id}/restore
PATCH /api/v1/admin/chat/sessions/{session_id}
POST  /api/v1/admin/chat/sessions/{session_id}/archive
POST  /api/v1/admin/chat/sessions/{session_id}/restore
```

Rename accepts only `{ "title": "..." }` with 1 to 200 non-whitespace characters. Identity is
derived from the bearer token. Archive follows `active -> closed -> archived`; restore follows
`archived -> closed`. Automatic titles use broad intent categories rather than copying raw messages
or invoking the model again. Search covers the cursor pages currently loaded in the browser.

### Step 3.4C: Chat reliability and recovery

- [x] Refresh an expired Supabase session and retry once after `401`.
- [x] Share one refresh operation across concurrent failures and sign out locally if it fails.
- [x] Show the outgoing question immediately with a Careline processing indicator.
- [x] Keep failed messages in the timeline with a safe Retry action.
- [x] Reuse the original `client_message_id` only when retrying that same turn.
- [x] Reload durable history when a retry was already processed by the backend.
- [x] Preserve per-user, per-role, per-session drafts in browser session storage.
- [x] Detect offline state, preserve drafts, and disable offline sending.
- [x] Recover expired or refreshed-away confirmations through Request again.
- [x] Add automated token-refresh and idempotency-ID tests.

```text
send with UUID A
  -> success: reload stored history and clear draft
  -> network failure: retain failed bubble, draft, and UUID A
  -> Retry with UUID A
       -> normal response: reload stored history
       -> already processed: reload history without duplicating the turn
```

On `401`, `apiClient` refreshes through Supabase and repeats the original request once. A failed
refresh clears the local authentication session so protected routing shows login. Confirmation
recovery never stores the old plaintext token: Request again repeats the original request as a new
turn and receives a new one-time token.

### Step 3.4D: Administrator audit dashboard

- [x] Add active-admin-only cross-role session discovery.
- [x] Aggregate session, messages, runs, tool events, pending actions, and context sources.
- [x] Compute pending, expired, approved, or rejected action status.
- [x] Remove `token_hash` before API serialization and JSON export.
- [x] Enrich context sources with their stored title and summary.
- [x] Return the linked patient's latest three closed/archived chat summaries.
- [x] Add a responsive audit drawer with search, record expansion, and sanitized JSON download.
- [x] Log audit access using identifiers and counts without logging chat or summary content.
- [x] Add authentication, sanitization, patient-summary, and frontend rendering tests.

Endpoints:

```text
GET /api/v1/admin/audit/sessions?limit=100
GET /api/v1/admin/audit/sessions/{session_id}
```

The active administrator can inspect patient-, administrator-, and doctor-initiated sessions. For
the selected session, `subject_patient_id` identifies the patient whose three latest summarized
patient conversations should be shown. These summaries come directly from `chat_sessions.summary`;
no new table or model invocation is required. Patient sessions with status `closed` or `archived`
are eligible, while active sessions without a finalized summary are excluded.

The audit service deliberately strips pending-action `token_hash`. It never returns authentication
tokens, passwords, service keys, or raw exception messages. The sanitized response may include PHI
such as messages, classifier decisions, action payloads, and summaries because the endpoint is
explicitly restricted to an active administrator and every access is logged.

The administrator graph persists two administrator-only message types:
`information_request` and `invitation_result`. The shared backend `ChatMessageType` now recognizes
those values so administrator history can be reopened through the common message-page contract.

#### Frontend Step 2 files

| File | Purpose |
|---|---|
| `frontend/src/components/ChatWorkspace.jsx` | Session list, message timeline, composer, error states, closing, and action approval UI |
| `frontend/src/lib/chatApi.js` | Role-specific URL selection and exact request bodies for sessions, messages, and confirmations |
| `frontend/src/App.jsx` | Mounts the working chat UI behind the existing patient/admin route guards |
| `frontend/src/App.css` | Responsive desktop/mobile workspace design |
| `backend/app/schemas/chat_history.py` | Accepts patient and administrator message types during history restoration |
| `frontend/src/lib/chatApi.test.js` | Checks role-aware endpoints, cursors, confirmation bodies, and generated message IDs |
| `frontend/src/auth/AdminRoute.test.jsx` | Checks admin access and patient redirection |

Detailed manual patient and administrator test scripts, sample input, expected behavior, and the
confirmation-token lifecycle are documented in `frontend/README.md`.

### Step 3.5: Profile page

- [ ] `hooks/usePatient.js` that fetches `GET /patients/me`.
- [ ] Show all attributes in a readable layout.
- [ ] Loading skeleton, error state and retry.

### Step 3.6: Inline editing

- [ ] An edit control per editable field, calling `PATCH /patients/me/attributes/{key}`.
- [ ] Client-side validation mirroring the backend rules (the backend remains the authority).
- [ ] Loading, success and error feedback per field.
- [ ] Email shown as read-only, with an optional "change email" flow through `supabase.auth.updateUser`.

### Step 3.7: Delete account flow

- [ ] A "Delete account" button in a clearly separated danger zone.
- [ ] A confirmation modal that requires typing `DELETE` (or re-entering the password).
- [ ] Call `DELETE /patients/me`, then sign out locally, clear state and redirect to a goodbye or login page.
- [ ] Handle failure gracefully (the account must not appear deleted if the call failed).

### Step 3.8: Polish

- [ ] Accessibility: labels on inputs, keyboard navigation, focus management in modals, sufficient color contrast.
- [ ] Responsive layout for mobile.
- [ ] Friendly empty states for fields that are not filled in yet.

### Milestone 3 checklist

- [ ] Sign up, log in and log out work
- [ ] Profile loads and each editable field can be updated
- [ ] Account deletion works end to end and signs the user out
- [ ] `npm run lint` and `npm run build` pass

---

# Milestone 4: Testing

**Goal:** automated confidence that the three endpoints and the main flows behave correctly and safely.
**Estimated time:** 2 days.

### Step 4.1: Backend unit tests (pytest)

- [ ] Validators: valid and invalid values for each key.
- [ ] `get_current_user`: valid token, expired token, malformed token, wrong audience, missing header.

### Step 4.2: Backend integration tests

- [ ] `GET /patients/me`: success, `fields` filter, unknown field, no token.
- [ ] `PATCH`: success, non-editable key (400), invalid value (422), no token.
- [ ] `DELETE`: success (204), user gone afterwards.
- [ ] **Cross-user tests:** user A's token never returns or changes user B's data.
- [ ] Use a dedicated test Supabase project (or local Supabase via `npx supabase start`), never production.

### Step 4.3: Frontend tests

- [x] Install Vitest and React Testing Library.
- [x] Test role-aware chat request bodies, generated idempotency IDs, and history cursors.
- [x] Test backend-confirmed admin access and patient redirection.
- [ ] Test the login and signup forms (validation and error display).
- [ ] Test inline editing and the delete confirmation modal (the delete button stays disabled until the confirmation is correct).

### Step 4.4: End-to-end test

- [ ] One Playwright test: sign up, edit a field, delete the account.
- [ ] Verify that after deletion the old token no longer works and the user cannot log in again.

### Milestone 4 checklist

- [ ] `pytest` passes
- [ ] Frontend tests pass
- [ ] E2E test passes against a test environment

---

# Milestone 5: Security and compliance review

**Goal:** find and fix gaps before any real patient data is stored.
**Estimated time:** 1 day.

- [ ] Confirm the **service-role key exists only on the backend** (search the frontend code and build output).
- [ ] Confirm **HTTPS only**, correct CORS, and secure response headers.
- [ ] Review what is logged and stored. Apply least privilege everywhere.
- [ ] Identify which health-privacy regulations apply to your target market (for example HIPAA, PIPEDA, GDPR, or provincial health privacy laws in Canada) and write down any gaps.
- [ ] Decide on data retention and audit logging. An `audit_log` table (who changed what, and when) is easy to add now and painful to add later.
- [ ] Re-confirm RLS is enabled on every table in the `public` schema.
- [ ] Run dependency scans: `pip-audit` for Python and `npm audit` for the frontend.
- [ ] Review the delete flow: should it require a recent re-login?
- [ ] Rotate any key that was ever shared or committed by mistake.

> This README is a technical plan, not legal advice. Consult a privacy professional before handling real patient data.

---

# Milestone 6: Deployment and CI/CD

**Goal:** a repeatable, automated path from code to production.
**Estimated time:** 1 to 2 days.

### Step 6.1: Environments

- [ ] Create a separate `careline-prod` Supabase project.
- [ ] Apply migrations to prod with `npx supabase link` to the prod project followed by `npx supabase db push`.
- [ ] Keep separate environment variables for dev and prod. Never reuse keys.

### Step 6.2: Backend deployment

- [ ] Write a `Dockerfile` for the backend (slim Python image, install from `requirements.txt`, run `uvicorn` on `$PORT`).
- [ ] Deploy to Render, Railway, Fly.io or a similar service.
- [ ] Set environment secrets in the host's dashboard (not in the repository).
- [ ] Point the host's health check at `/health`.
- [ ] Set `FRONTEND_ORIGIN` to the real frontend URL.

### Step 6.3: Frontend deployment

- [ ] Deploy to Vercel, Netlify or Cloudflare Pages.
- [ ] Set `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` and `VITE_API_BASE_URL` for production.
- [ ] Add the production URL to Supabase **Site URL** and redirect URLs.
- [ ] Configure SPA fallback so deep links such as `/profile` work.

### Step 6.4: CI with GitHub Actions

- [ ] On every pull request: backend `ruff check`, `pytest`; frontend `npm run lint`, tests and `npm run build`.
- [ ] Store test secrets in GitHub Actions secrets.
- [ ] Optionally require CI to pass before merging.

### Step 6.5: Release and rollback

- [ ] Smoke-test production: sign up, view profile, edit a field, delete the account.
- [ ] Document the rollback process (redeploy the previous build, and how to handle a bad migration).

---

## Timeline

Single developer, approximate.

| Milestone | Effort |
|---|---|
| 0. Setup | 0.5 day |
| 1. Database and auth | 1 day |
| 2. Backend | 2 to 3 days |
| 3. Frontend | 3 to 4 days |
| 4. Testing | 2 days |
| 5. Security review | 1 day |
| 6. Deployment | 1 to 2 days |
| **Total** | **about 2 to 2.5 weeks** |

---

## Risks and open questions

1. **Hard versus soft delete:** are there legal retention requirements for patient data?
2. **Email as an editable attribute:** best routed through Supabase Auth for re-verification. Include it in Phase 1?
3. **Email confirmation:** required at signup, or skipped during development?
4. **Profile fields:** is the list in the data model right, or are others needed (blood group, allergies, insurance ID)? A jsonb "extra attributes" column is an option if the set keeps changing.
5. **File storage in Phase 1?** For example, a profile photo. If not, Storage cleanup in the delete flow can wait.

---

## Phase 2 readiness

Because every record hangs off `auth.users.id` and each table gets RLS, Phase 2 should be additive:

- `doctors` table
- `doctor_availability` (slots with timezone-aware timestamps)
- `appointments` (`patient_id`, `doctor_id`, slot, status) with a unique constraint to prevent double-booking

---

## Troubleshooting

| Problem | Likely cause and fix |
|---|---|
| `source .venv/bin/activate` fails on Windows | Use `.venv\Scripts\activate` (cmd) or `.venv\Scripts\Activate.ps1` (PowerShell). |
| PowerShell refuses to run the activate script | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once. |
| Browser shows a CORS error calling the API | `FRONTEND_ORIGIN` must match the frontend URL exactly, including the port. |
| `package.json` JSON parse error | Check for a missing or extra comma in the `scripts` block. |
| A second `supabase/` folder appeared in `backend/` or `frontend/` | Delete it and rerun Supabase CLI commands from the repo root. |
| `supabase link` asks for a password | Use the database password saved at project creation, or reset it under **Project Settings, Database**. |
| Weird `npm install` or `pip install` file errors | Pause OneDrive sync or move the project out of OneDrive. |
| A secret was committed to Git | Rotate it immediately in the Supabase dashboard. Deleting the file does not remove it from history. |
| Backend returns 401 for valid-looking tokens | Confirm the token came from this Supabase project, its audience is `authenticated`, and its signing key is present in the project's JWKS response. |
| Chat returns `503` and logs `BadRequestError` | Restart Uvicorn to load the current strict classifier schema. The old unconstrained `JsonValue` schema was rejected by OpenAI with HTTP 400. Current logs also show safe provider status/code/parameter metadata. |
| Chat returns `409 chat_turn_already_processed` | The request reused a `client_message_id` already stored in that conversation. Keep the `session_id`, generate a new UUID for a genuinely new message/confirmation, and submit again. After an uncertain network timeout, first reload session messages and reconcile the original ID rather than blindly performing the action twice. Never reuse the example UUIDs from this README. |
| `relation "chat_sessions" already exists` | Migration `20261007120000` has already created the chat tables. Check `npx supabase migration list`; when the version appears in both local and remote columns, do not paste or execute the migration SQL again. Use normal future migrations for changes instead of adding `IF NOT EXISTS`, which can hide schema drift. |
| Admin endpoint returns `403 admin_access_required` | A valid login is not enough. The JWT user needs both an active `admin_profiles` row and an active `user_roles` row whose role is `admin`. Follow the provisioning steps below. |
| Admin session creation returns `422 request_validation_failed` | Send a JSON object such as `{"title":"Create a new patient"}` with `Content-Type: application/json`. The title must be `null` or 1–200 non-blank characters and no extra keys are accepted. Restart the backend and inspect the safe `request_validation_failed` log's `validation_issues` field for the failing location/type; rejected values are never logged. |
| Confirmation returns `422 request_validation_failed` | Check that `session_id` and `client_message_id` are valid UUIDs. UUID characters may only be `0-9`, `a-f`, and hyphens in the normal `8-4-4-4-12` layout. Generate IDs with PowerShell `[guid]::NewGuid()` rather than editing an example UUID manually. Validation happens before the pending action is consumed, so after correcting only a malformed UUID the same action ID can be submitted again. |
| A session exists but its messages/other records are empty and `subject_patient_id` is null | Verify that every chat and confirmation request used that exact session UUID. The six tables and pending-action token are session-scoped. A newly created but unused session correctly has no child records and no subject. Query or call the messages endpoint with the session ID returned by the conversation that produced the proposal/invitation—not a later session ID. `chat_session_context_sources` may still be empty until a new session is created after an older admin session has been closed and summarized. |
| Invitation returns `409 patient_invitation_conflict` | The email is already registered in Supabase Auth. Use the existing account flow instead of sending another create-patient invitation. |
| Confirmation returns `503 patient_invitation_unavailable` | Find the `patient_invitation_failed` log for the same request ID. Its safe `provider_status_code` and `provider_error_code` identify Auth failures without logging the email. Confirm the backend service-role key, the Auth redirect allow-list, email uniqueness and Supabase Auth email delivery settings. A failed confirmation consumes its one-time action, so correct the cause and create a fresh proposal/action ID before retrying. |
| Invitation link opens the wrong page | Set `PATIENT_INVITATION_REDIRECT_URL` and add that exact URL to Supabase **Authentication > URL Configuration > Redirect URLs**. |

---

# Role-aware administrator chat and `createPatient`

This section describes the implementation added by migration
`20261008120000_make_chat_role_aware.sql`. The migration was applied to the linked development
project on October 8, 2026. Local and remote migration histories match, and linked database lint
reports no schema errors.

## Simple explanation

The same six chat tables are now shared by patients, administrators and future doctors. Every row
records **who started the chat** and **which role they were acting as**. An administrator's chat can
therefore never be accidentally loaded as that person's patient chat, even if one Supabase account
has both roles.

An administrator can say:

> Create a patient named Jamie Patient, born 1990-05-12, phone +16045550123, email
> jamie@example.com.

The orchestrator identifies the `create_patient` intent and hands the request to the LangGraph node
named exactly `createPatient`. The node validates the information and asks the administrator to
confirm. Only a separate typed confirmation request can send the Supabase invitation email. The
language model cannot approve its own proposal.

Because this feature uses an **email invitation**, there are four required values:

1. full name;
2. date of birth in `YYYY-MM-DD` format;
3. phone number with country code; and
4. email address.

Email is the fourth required value even though the original patient record requirement mentioned
three fields; Supabase needs it to address the invitation and create the Auth identity.

## Identity model

The API never accepts `chat_initiated_by` or `chat_initiator_role` in request JSON. It verifies the
Supabase bearer token, obtains the user UUID from the signed JWT `sub` claim, and selects the role
from the endpoint being used:

```text
patient JWT -> /api/v1/chat       -> role patient
admin JWT   -> /api/v1/admin/chat -> role admin (database role check required)
```

The stored column names are deliberately generic:

- `chat_initiated_by`: Supabase Auth user UUID that started the conversation;
- `chat_initiator_role`: `patient`, `admin`, or `doctor`;
- `subject_patient_id`: optional patient the conversation is about;
- `sent_by_user_id` and `sent_by_role`: the sender of an individual message. Assistant messages
  have no user UUID and use the role `assistant`.

`user_roles` is the authorization source. `admin_profiles` contains administrator display data and
active/inactive status. The backend requires an active row in **both** tables before any admin chat
operation. The schema is doctor-ready, but no doctor workflow or doctor endpoint has been built yet.

## How all six chat tables are used

Assume admin UUID `1111...`, session UUID `aaaa...`, run UUID `bbbb...`, and invited patient UUID
`2222...`.

| Table | What one example record means |
|---|---|
| `chat_sessions` | `id=aaaa...`, `chat_initiated_by=1111...`, role `admin`, title `New patient invitation`. After a successful invitation, `subject_patient_id=2222...`. |
| `chat_messages` | Stores the admin request and assistant response in order. The admin input has `sent_by_user_id=1111...`, `sent_by_role=admin`; the response uses `sent_by_role=assistant`. |
| `chat_runs` | One orchestrator execution, such as `id=bbbb...`, intent `create_patient`, status `completed`, plus safe decision metadata and latency. |
| `chat_tool_events` | Ordered internal traces such as `classify_message`, `createPatient`, and `supabase_auth.invite_patient`; raw bearer tokens and invitation secrets are not stored. |
| `chat_pending_actions` | Stores a SHA-256 hash of the one-time confirmation token and the proposed patient payload. It records approved/rejected and consumption time. The plaintext action ID exists only in the immediate HTTPS response. |
| `chat_session_context_sources` | Records which closed admin-session summaries were loaded into a new admin session, including `position=1` for the most recent selected summary. Admin and patient histories are isolated by initiator UUID **and** role. |

The composite foreign keys repeat initiator UUID and role across child tables. This makes accidental
cross-user or cross-role references invalid at the database level, not merely unlikely in Python.

## Request flow and files called in sequence

```text
POST /api/v1/admin/chat
  -> deps.py verifies JWT and requires active admin profile + role
  -> admin_chat_service.py stores input in shared chat_messages/chat_runs
  -> admin_classifier.py applies the detailed system prompt
  -> admin_graph.py routes create_patient to createPatient
  -> admin_pending_actions.py creates or consumes a hash-only confirmation
  -> admin_service.py sends the Supabase Auth email invitation
  -> Supabase handle_new_user trigger creates patients + patient user_roles row
  -> admin_service.py fills name, date of birth and phone
  -> admin_graph.py links chat_sessions.subject_patient_id
  -> admin_chat_service.py stores response, decision and tool traces atomically
```

The administrator system prompt is the `ADMIN_SYSTEM_PROMPT` constant in
`backend/app/agents/admin_classifier.py`. It explicitly defines the supported intent, all extraction
rules, missing-field behavior, unsupported boundaries, and the instruction never to invent values
or treat ordinary text such as "yes" as confirmation.

Important logs include `admin_authorization_succeeded`, `admin_chat_intent_classified`,
`admin_patient_invitation_proposal_created`, `admin_pending_action_consumed`,
`patient_invitation_succeeded`, and `admin_chat_turn_completed`. Logs contain counts, status and
timing—not names, email addresses, phone numbers, UUIDs, tokens, or message bodies.

## One-time admin provisioning

There is intentionally no public "make me admin" API. Create the administrator in Supabase
Dashboard under **Authentication > Users**, then copy that user's UUID and run the following in the
Supabase SQL editor. Replace all example values:

```sql
insert into public.admin_profiles (id, email, display_name, is_active)
values (
  'ADMIN-AUTH-USER-UUID',
  'admin@example.com',
  'Careline Administrator',
  true
)
on conflict (id) do update
set email = excluded.email,
    display_name = excluded.display_name,
    is_active = excluded.is_active;

insert into public.user_roles (user_id, role, is_active)
values ('ADMIN-AUTH-USER-UUID', 'admin', true)
on conflict (user_id, role) do update
set is_active = excluded.is_active;
```

The existing Auth trigger also gives newly created users a patient profile and patient role. That is
safe: the endpoint chooses the acting role and every database query filters by both UUID and role.

Add these backend settings to `.env`:

```dotenv
ADMIN_CHAT_PROMPT_VERSION=admin-patient-invitation-v1
PATIENT_INVITATION_REDIRECT_URL=http://localhost:5173/set-password
```

Also allow the redirect URL in the Supabase Auth URL settings. Keep the service-role key only in the
backend; it is required to send admin invitations and must never appear in Vite/frontend variables.

## Exact Swagger test

1. Start the backend from `backend/`:

   ```powershell
   .\.venv\Scripts\Activate.ps1
   uvicorn app.main:app --reload
   ```

2. Log in with the administrator email/password using the existing Supabase token script or client
   flow. Copy the returned `access_token`.
3. Open `http://127.0.0.1:8000/docs`, select **Authorize**, and paste only the token.
4. Call `GET /api/v1/auth/verify`. Expect `{"authenticated": true}`.
5. Call `POST /api/v1/admin/chat/sessions`:

   ```json
   {"title": "Invite Jamie Patient"}
   ```

   Copy the returned session `id`.
6. Generate a fresh UUID for `client_message_id`, then call `POST /api/v1/admin/chat`:

   ```json
   {
     "session_id": "SESSION-UUID-FROM-STEP-5",
     "client_message_id": "NEW-CLIENT-MESSAGE-UUID",
     "message": "Create a patient named Jamie Patient, date of birth 1990-05-12, phone +16045550123, email jamie@example.com"
   }
   ```

7. Expect a proposal similar to:

   ```json
   {
     "action": "confirmation_required",
     "message": "Please confirm the patient information before the invitation is sent.",
     "requires_confirmation": true,
     "pending_action_id": "ONE-TIME-ACTION-ID",
     "data": {
       "proposed_patient": {
         "full_name": "Jamie Patient",
         "date_of_birth": "1990-05-12",
         "phone": "+16045550123",
         "email": "jamie@example.com"
       }
     }
   }
   ```

8. Generate another fresh client-message UUID and call the same endpoint:

   ```json
   {
     "session_id": "SAME-SESSION-UUID",
     "client_message_id": "ANOTHER-NEW-CLIENT-MESSAGE-UUID",
     "confirmation": {
       "action_id": "ONE-TIME-ACTION-ID",
       "approved": true
     }
   }
   ```

9. Expect `action: patient_invited`. Check the patient's inbox, Supabase Auth users, `patients`, and
   `user_roles`. The session now has that patient's UUID in `subject_patient_id`.
10. Test history with:

    - `GET /api/v1/admin/chat/sessions`;
    - `GET /api/v1/admin/chat/sessions/{session_id}/messages`;
    - `POST /api/v1/admin/chat/sessions/{session_id}/close`.

Closing generates a privacy-safe summary. Starting the next admin session can load the most recent
bounded summaries, and every inclusion is audited in `chat_session_context_sources`.

To test field collection, send only `"Create a patient named Jamie"`. Expect
`action: information_required` with the remaining fields. Continue in the **same session** so the
classifier can combine earlier turns. To test cancellation, submit the confirmation with
`approved: false`; no Auth user is created. Never reuse a `client_message_id` for a new action.

## Internal data example

The public response is intentionally small. Internally, one completed proposal turn is conceptually
stored as:

```json
{
  "session": {
    "chat_initiated_by": "ADMIN-UUID",
    "chat_initiator_role": "admin",
    "subject_patient_id": null
  },
  "run": {
    "intent": "create_patient",
    "status": "completed",
    "decision_data": {
      "missing_fields": []
    }
  },
  "tool_events": [
    {"node_name": "classify_message", "tool_name": "admin_chat_model.classify"},
    {"node_name": "createPatient", "tool_name": "patient_invitation.propose"}
  ],
  "pending_action": {
    "action_type": "create_patient",
    "token_hash": "SHA-256-ONLY",
    "decision": null
  }
}
```

Patient values may exist in the encrypted/controlled database records needed to perform the action,
but they are deliberately excluded from application logs and safe tool-event arguments.

## Implementation map and verification

| File | Responsibility |
|---|---|
| `backend/app/agents/admin_classifier.py` | Detailed intent/extraction system prompt and strict model output |
| `backend/app/agents/admin_graph.py` | LangGraph routing, exact `createPatient` node, confirmation and invitation handoff |
| `backend/app/agents/admin_pending_actions.py` | Random action token, SHA-256 persistence, one-time transactional consumption |
| `backend/app/services/admin_service.py` | Active-admin authorization, Supabase invite, profile completion and cleanup on partial failure |
| `backend/app/services/admin_chat_service.py` | Shared chat persistence, context, summaries, runs, traces and history pages |
| `backend/app/repositories/admin_chat_repository.py` | Narrow service-role queries against the shared tables/functions |
| `backend/app/api/v1/admin_chat.py` | `POST /api/v1/admin/chat` |
| `backend/app/api/v1/admin_chat_sessions.py` | Create/list/read/close administrator conversations |
| `supabase/migrations/20261008120000_make_chat_role_aware.sql` | Roles, admin profiles, shared columns, constraints, RLS and transactional functions |

Verification completed after implementation:

- Ruff: all checks passed;
- Pytest: 159 tests passed;
- OpenAPI: all four administrator paths generated successfully;
- Supabase migration: applied successfully to the linked development project;
- Supabase migration history: local and remote versions match through `20261008120000`;
- linked Supabase database lint: no schema errors.

The single test warning is a LangGraph dependency notice about a future serializer default. It does
not represent an application failure and all tests pass.

---

## Appointment Coordinator Agent

Careline now has a second patient-side specialist: the **Appointment Coordinator Agent**. The
main patient orchestrator remains the first entry point. It decides whether a message concerns a
profile or an appointment. Appointment messages are handed to a dedicated LangGraph subgraph with
its own detailed system prompt, strict output schema, database tools, and safety boundary.

In simple English, the flow is:

1. The signed-in patient sends a normal chat message.
2. The main orchestrator labels it `manage_appointment`.
3. The Appointment Coordinator decides whether to list appointments, search availability, book,
   reschedule, or cancel.
4. Read-only requests call the database immediately and return only real records.
5. A change request creates a one-time confirmation. Nothing is changed yet.
6. The patient presses **Confirm appointment**. The backend consumes the one-time action and calls
   a PostgreSQL transaction.
7. The transaction locks the relevant slot/appointment, applies the change, and writes status
   history. If another person already took the slot, the operation safely returns a conflict.
8. The request, response, run, tool calls, and pending confirmation continue to be stored in the
   existing six shared role-aware chat tables.

The language model is never trusted to write appointment tables, decide the authenticated patient
ID, invent availability, or bypass confirmation. It proposes a typed operation; ordinary Python
and PostgreSQL code perform authorization, validation, locking, and persistence.

### LangGraph nodes and system prompt

The main graph adds the `manage_appointment` handoff. The specialist subgraph contains:

| Node | Purpose |
|---|---|
| `classifyAppointmentIntent` | Produces a strict appointment decision from the chat message |
| `listAppointments` | Reads the signed-in patient's upcoming appointments |
| `searchAvailability` | Searches real, future, unbooked provider slots |
| `proposeAppointment` | Validates IDs and creates a hash-only one-time action |
| `handle_confirmation` | Existing parent node consumes approval and executes the transaction |
| `unsupported` | Refuses medical advice and out-of-scope appointment requests |

The complete specialist prompt is in
`backend/app/agents/appointment_classifier.py` as `APPOINTMENT_SYSTEM_PROMPT`. Important rules in
that prompt include:

- only the authenticated patient's appointments are in scope;
- never invent doctor, appointment, slot, date, or time values;
- broad searches and an exact booking are different intents;
- booking needs an exact `slot_id` returned by a prior database search;
- rescheduling needs both an owned `appointment_id` and a new `slot_id`;
- cancellation needs an owned `appointment_id`;
- relative phrases such as “next week” are not silently converted into dates by the model;
- normal text such as “yes” never counts as approval—the typed confirmation request is required;
- medical advice, diagnosis, emergencies, and other patients' data are refused;
- prior chat text is untrusted context, not permission or database truth.

### New appointment tables

Migration `supabase/migrations/20261009120000_create_appointment_coordination.sql` creates four
business tables. They are separate from the six chat/audit tables:

| Table | What one row means | Example |
|---|---|---|
| `doctor_profiles` | One provider whose schedule can be searched | Dr. Rivera, Cardiology, America/Vancouver |
| `provider_availability` | One exact bookable or blocked time slot | 2099-04-01 10:00–10:30, video, available |
| `appointments` | One patient booking and its current state | Patient A booked Dr. Rivera at that slot |
| `appointment_status_history` | One immutable lifecycle event | scheduled → rescheduled by patient |

All four tables have RLS enabled and are denied to `anon` and normal `authenticated` Supabase
clients. Only the backend service-role client accesses them. The verified JWT patient UUID is added
to every patient-scoped query by backend code.

The migration also permits these values in `chat_pending_actions.action_type`:

- `book_appointment`;
- `reschedule_appointment`;
- `cancel_appointment`.

Three transactional database functions prevent partial changes and double booking:

- `careline_book_appointment` locks an available slot, creates the appointment, marks the slot
  booked, and writes history;
- `careline_reschedule_appointment` locks the owned scheduled appointment and new slot, releases the
  old slot, reserves the new slot, updates the appointment, and writes history;
- `careline_cancel_appointment` locks the owned scheduled appointment, cancels it, releases its
  slot, and writes history.

### Apply the migration and add test availability

From the repository root, apply migrations to the linked development project:

```powershell
npx supabase db push
```

Then create a doctor and a future slot in the Supabase SQL Editor. Use future dates appropriate for
your test day. The example uses year 2099 so it remains in the future:

```sql
insert into public.doctor_profiles (display_name, specialty, timezone)
values ('Dr. Maya Rivera', 'Cardiology', 'America/Vancouver')
returning id;

-- Replace DOCTOR_UUID with the returned id.
insert into public.provider_availability (
  doctor_id, starts_at, ends_at, status, location_type, location
) values (
  'DOCTOR_UUID',
  '2099-04-01T10:00:00-07:00',
  '2099-04-01T10:30:00-07:00',
  'available',
  'video',
  'Careline Video'
)
returning id;
```

Keep the returned availability UUID; that value is the `slot_id` used for an exact booking.

### Swagger tests

Start the backend, open `http://127.0.0.1:8000/docs`, select **Authorize**, and paste only a valid
patient access token.

#### 1. Search availability directly

Run `GET /api/v1/appointments/availability` with:

```text
starts_at = 2099-04-01T00:00:00-07:00
ends_at   = 2099-04-02T00:00:00-07:00
specialty = Cardiology
```

Expected shape:

```json
{
  "items": [
    {
      "id": "SLOT-UUID",
      "starts_at": "2099-04-01T17:00:00Z",
      "ends_at": "2099-04-01T17:30:00Z",
      "location_type": "video",
      "location": "Careline Video",
      "doctor": {
        "id": "DOCTOR-UUID",
        "display_name": "Dr. Maya Rivera",
        "specialty": "Cardiology"
      }
    }
  ]
}
```

#### 2. List the signed-in patient's appointments

Run `GET /api/v1/appointments`. Before booking, `items` may be empty. Set `include_past=true` when
you also want cancelled/historical records.

#### 3. Test through the orchestrator chat

Create a patient session with `POST /api/v1/chat/sessions` and copy its `id`. Every chat request
below needs a fresh valid UUID in `client_message_id`.

Search through chat:

```json
{
  "session_id": "SESSION-UUID",
  "client_message_id": "NEW-UUID-1",
  "message": "Find cardiology appointments from 2099-04-01T00:00:00-07:00 to 2099-04-02T00:00:00-07:00"
}
```

Expected action: `answer`, with real records in `data.available_slots`.

Book the returned slot:

```json
{
  "session_id": "SESSION-UUID",
  "client_message_id": "NEW-UUID-2",
  "message": "Book slot SLOT-UUID"
}
```

Expected proposal:

```json
{
  "action": "confirmation_required",
  "message": "Please confirm this appointment action before it is applied.",
  "requires_confirmation": true,
  "pending_action_id": "ONE-TIME-ACTION-ID",
  "data": {
    "proposed_appointment_action": {
      "operation": "book_appointment",
      "slot_id": "SLOT-UUID"
    }
  }
}
```

The real response also includes `selected_slot` inside `proposed_appointment_action` so the patient
can review the provider, specialty, time, and location instead of confirming an opaque UUID.

Confirm using another fresh client message UUID:

```json
{
  "session_id": "SESSION-UUID",
  "client_message_id": "NEW-UUID-3",
  "confirmation": {
    "action_id": "ONE-TIME-ACTION-ID",
    "approved": true
  }
}
```

Expected action: `appointment_booked`. `GET /api/v1/appointments` now returns the booking. To test
rescheduling, create another available slot, ask `"Move appointment APPOINTMENT-UUID to slot
NEW-SLOT-UUID"`, and approve its proposal. To test cancellation, ask `"Cancel appointment
APPOINTMENT-UUID"` and approve. Reusing an action ID fails because confirmation tokens are one-time.

To test rejection, send the confirmation with `approved: false`. The action becomes consumed and
the appointment remains unchanged. To test conflict handling, create a proposal, mark the slot
booked before confirming, and then approve. The API returns `409 appointment_conflict` and asks the
patient to search again.

### Internal JSON and the six chat tables

A booking proposal is recorded conceptually as:

```json
{
  "chat_sessions": {
    "chat_initiated_by": "PATIENT-UUID",
    "chat_initiator_role": "patient",
    "subject_patient_id": "PATIENT-UUID"
  },
  "chat_messages": {
    "message_type": "confirmation_request",
    "structured_data": {
      "action": "confirmation_required",
      "data": {"proposed_appointment_action": {"operation": "book_appointment"}}
    }
  },
  "chat_runs": {
    "intent": "manage_appointment",
    "status": "completed"
  },
  "chat_tool_events": [
    {"node_name": "classify_message", "tool_name": "chat_model.classify"},
    {"node_name": "appointment.classify_intent", "tool_name": "appointment_model.classify"},
    {"node_name": "appointment.book_appointment", "tool_name": "appointment.pending_action.create"}
  ],
  "chat_pending_actions": {
    "action_type": "book_appointment",
    "token_hash": "SHA-256-HASH-ONLY",
    "decision": null
  },
  "chat_session_context_sources": {
    "included_summary": true,
    "position": 1
  }
}
```

The plaintext `pending_action_id` is returned only in the immediate HTTPS response. It is never
stored in `chat_messages`; only its SHA-256 hash is stored. Logs contain safe operation names,
counts, outcomes, durations, and error classes. They do not contain access tokens, confirmation
tokens, chat text, medical reasons, or database error bodies.

### Appointment implementation map

| File | Responsibility |
|---|---|
| `backend/app/agents/classifier.py` | Top-level `manage_appointment` routing |
| `backend/app/agents/appointment_classifier.py` | Detailed appointment system prompt and strict model output |
| `backend/app/agents/appointment_graph.py` | Specialist LangGraph nodes and deterministic tool handoffs |
| `backend/app/agents/patient_graph.py` | Specialist handoff and shared confirmation execution |
| `backend/app/agents/pending_actions.py` | Hash-only appointment proposal and one-time consumption |
| `backend/app/schemas/appointment.py` | Strict decisions, records, payloads, and response models |
| `backend/app/repositories/appointment_repository.py` | Narrow Supabase queries/RPC calls |
| `backend/app/services/appointment_service.py` | Date, ownership, data, and mutation validation |
| `backend/app/api/v1/appointments.py` | Authenticated list and availability Swagger endpoints |
| `frontend/src/components/ChatWorkspace.jsx` | Appointment cards and context-aware confirmation UI |
| `supabase/migrations/20261009120000_create_appointment_coordination.sql` | Tables, RLS, grants, constraints, indexes, and transactions |

### Administrator scheduling workspace

The appointment milestone also includes a frontend integrated with authenticated backend APIs.
After an administrator signs in, the header now contains **Conversations** and **Appointments**.
Selecting **Appointments** opens `/admin/appointments`.

The workspace provides:

- live counts for scheduled appointments, available slots, booked slots, and active providers;
- an appointment table with patient, provider, time, visit mode, and status;
- appointment status filtering;
- provider creation, activation, and deactivation;
- availability creation with timezone-aware timestamps;
- video, phone, and in-person visit types;
- safe blocking and reopening of unbooked slots;
- a disabled block control for booked slots;
- responsive desktop/tablet/mobile layouts;
- loading, empty, retry, and safe error states;
- route-level code splitting so the scheduling bundle loads only for administrators who open it.

These administrator APIs require both a valid Supabase JWT and an active record in both
`admin_profiles` and `user_roles`:

| Method and path | Purpose |
|---|---|
| `GET /api/v1/admin/appointments/doctors` | List active and inactive providers |
| `POST /api/v1/admin/appointments/doctors` | Create a provider profile |
| `PATCH /api/v1/admin/appointments/doctors/{doctor_id}` | Update or activate/deactivate a provider |
| `GET /api/v1/admin/appointments/availability` | List/filter provider availability |
| `POST /api/v1/admin/appointments/availability` | Publish a future appointment time |
| `PATCH /api/v1/admin/appointments/availability/{slot_id}/status` | Block or reopen an unbooked slot |
| `GET /api/v1/admin/appointments` | Filter and inspect appointments across patients |

The browser never talks directly to the protected appointment tables. It calls FastAPI with the
fresh Supabase access token. FastAPI verifies the identity and active administrator role before the
service-role repository can read or modify schedules.

#### Test the admin scheduling frontend

1. Start the backend and frontend.
2. Sign in with an active administrator account.
3. Open `http://localhost:5173/admin/appointments`, or select **Appointments** in the header.
4. Add a provider, for example `Dr. Maya Rivera`, `Cardiology`, `America/Vancouver`.
5. Under **Publish appointment times**, select that provider, enter future start/end times, select
   the visit type, and publish.
6. Confirm the new time appears as `available` and that the open-times count increases.
7. Select **Block**. Confirm it becomes `blocked` and is no longer returned by patient searches.
8. Select **Reopen**. Confirm it becomes `available` again.
9. Book it from patient chat. Return to the admin page and refresh. Confirm the slot is `booked`,
   the appointment appears in the table, and the Block button is disabled.

Swagger can test the same backend without the frontend. Authorize using an admin access token and
open the **Admin appointments** group. Example provider request:

```json
{
  "display_name": "Dr. Maya Rivera",
  "specialty": "Cardiology",
  "timezone": "America/Vancouver"
}
```

Example availability request:

```json
{
  "doctor_id": "DOCTOR-UUID",
  "starts_at": "2099-04-01T10:00:00-07:00",
  "ends_at": "2099-04-01T10:30:00-07:00",
  "location_type": "video",
  "location": "Careline Video"
}
```

#### Human-friendly patient choices

Patient chat context now includes the structured appointment results stored with prior assistant
messages. This lets the specialist resolve a bounded follow-up such as:

```text
Patient: Find cardiology appointments from 2099-04-01T00:00:00-07:00 to
         2099-04-03T00:00:00-07:00
Careline: [returns three real availability cards]
Patient: Book the first option
```

The prior structured data is explicitly labelled as untrusted model input. It helps the classifier
resolve “first option” to the already-returned slot UUID, but it cannot authorize a write. Careline
still resolves that exact slot from the database, shows a confirmation card, and requires the typed
one-time confirmation before booking.

#### Supabase migration reconciliation completed

The linked Supabase database already contained all four appointment tables with the expected
columns and all three transaction functions, but migration version `20261009120000` was missing
from its migration ledger. A normal `db push` safely stopped at `doctor_profiles already exists`.
The schema and functions were checked through read-only/service-role calls, then the migration
ledger was repaired:

```powershell
npx supabase migration repair 20261009120000 --status applied
npx supabase migration list
npx supabase db push --dry-run
```

Local and remote migration history now match through `20261009120000`, and the final dry run reports
`Remote database is up to date`. No provider or patient test records were inserted automatically.

#### Appointment chat intent constraint correction

The first live appointment-chat attempt revealed that the original `chat_runs_intent_valid`
constraint still allowed only profile, invitation, and unsupported intents. The graph correctly
returned the new top-level `manage_appointment` intent, but PostgreSQL rejected that value while
the completed turn was being stored. The API intentionally hid the raw database error behind
`chat_history_unavailable`.

Migration `20261010120000_allow_appointment_chat_intent.sql` updates the check constraint to allow:

```text
read_profile
update_profile
create_patient
manage_appointment
unsupported
```

A regression test reads the migration and compares it with `ChatIntent.MANAGE_APPOINTMENT`, so a
future intent/schema mismatch cannot pass the backend suite unnoticed.

The migration has been applied to the linked Supabase project. Verification confirmed that local
and remote migration history match through `20261010120000`, a new `db push --dry-run` reports
`Remote database is up to date`, and the linked database schema lint reports no errors.

If a message failed before this correction, type and send it again as a **new message**. Do not use
the Retry button on the old failed bubble: that bubble retains its original `client_message_id`,
which was already stored and can correctly trigger duplicate-message protection. A newly sent
message receives a new ID and uses the corrected database constraint.

#### Natural-language appointment dates

Patients are not required to type ISO timestamps in the portal. Each patient chat request now
includes the browser's IANA timezone, such as `America/Vancouver`. The appointment model extracts
the patient's words, while `appointment_date_service.py` performs the actual calendar calculation.
Keeping date arithmetic in deterministic application code avoids asking the model to guess the
current date, timezone, or daylight-saving offset.

Supported examples include:

| Patient wording | Search window |
|---|---|
| `How soon is one available?` | Now through the next 30 days |
| `tomorrow` | The next local calendar day |
| `tomorrow morning` | 8:00 a.m. through noon locally |
| `tomorrow afternoon` | Noon through 5:00 p.m. locally |
| `next week` | Next Monday through the following Monday |
| `this weekend` / `next weekend` | Saturday through Monday |
| `this Friday` / `next Tuesday` | The named local calendar day |
| `this month` / `next month` | The corresponding local calendar month |
| `in the next 3 days` / `next 2 weeks` | A bounded range starting now |

The frontend obtains the timezone with
`Intl.DateTimeFormat().resolvedOptions().timeZone`. Swagger and older clients may omit the field;
the backend then uses `UTC` for backward compatibility. An invalid timezone is rejected with HTTP
`422` instead of being silently changed.

Example patient request:

```json
{
  "session_id": "<active-session-uuid>",
  "client_message_id": "<new-uuid-for-this-message>",
  "message": "Find me a cardiology appointment next week",
  "timezone": "America/Vancouver"
}
```

If the phrase cannot be resolved safely, no date is invented. Careline asks the patient for a
clearer expression and gives examples. The resolved start, end, and timezone are included in the
sanitized appointment tool trace, and structured logs record either
`appointment_date_range_resolved` or `appointment_date_information_required` without recording the
patient's raw message.

Availability queries also select the internal `doctor_profiles.is_active` value so Supabase can
exclude inactive providers. Before a slot is validated or returned to a patient, the service maps
that joined record to the public doctor summary fields (`id`, `display_name`, and `specialty`). This
preserves strict validation without exposing the internal flag. The same mapping is used for search
results and exact-slot selection during a booking proposal.

Patient wording is also normalized to the stored specialty category before the query. For example,
`cardiologist` and `heart doctor` search `Cardiology`; equivalent mappings cover common titles such
as dermatologist, neurologist, pediatrician, psychiatrist, and oncologist. Unknown specialty text
is preserved rather than guessed.

Implementation files:

| File | Responsibility |
|---|---|
| `backend/app/services/appointment_date_service.py` | Deterministic phrase-to-date conversion |
| `backend/app/agents/appointment_classifier.py` | Extracts, but does not calculate, date phrases |
| `backend/app/agents/appointment_graph.py` | Resolves the range before querying availability |
| `backend/app/schemas/chat.py` | Validates the browser's IANA timezone |
| `frontend/src/lib/chatApi.js` | Adds patient browser timezone to chat requests |

Administrator scheduling implementation files:

| File | Responsibility |
|---|---|
| `backend/app/api/v1/admin_appointments.py` | Protected administrator HTTP endpoints |
| `backend/app/services/appointment_admin_service.py` | Validation, logging, safe provider/slot operations |
| `backend/app/schemas/appointment_admin.py` | Strict request and response contracts |
| `backend/app/repositories/appointment_repository.py` | Service-role schedule and oversight queries |
| `frontend/src/pages/AppointmentAdminPage.jsx` | Integrated scheduling dashboard |
| `frontend/src/lib/appointmentAdminApi.js` | Authenticated frontend API calls |
| `frontend/src/components/AppShell.jsx` | Role-aware navigation |

Verification completed for this addition:

- 186 backend tests passed;
- frontend lint passed;
- 15 frontend tests passed;
- production frontend build passed.
- a read-only live Supabase contract check validated the stored doctor, availability, and
  appointment rows against the strict backend response models;
- the linked development database currently contains one provider, two availability slots, and no
  appointments; these pre-existing records were not created or changed by the check.

---

## Common commands

```bash
# Backend (from backend/)
uvicorn app.main:app --reload
ruff check .
ruff format .
pytest

# Frontend (from frontend/)
npm run dev
npm run lint
npm run format
npm run build

# Supabase (from repo root)
npx supabase migration new <name>
npx supabase db push
```

---

## Project completion summary

Careline concludes as a working development-stage healthcare portal built around authenticated,
role-aware conversations. The project began as a patient profile application and expanded into a
larger proof of concept containing patient and administrator workspaces, durable LangGraph chat,
patient invitation, audit visibility, and appointment coordination.

### What was completed

| Area | Final implementation |
|---|---|
| Authentication | Supabase email/password authentication, JWT verification through JWKS, protected React routes, session restoration, token refresh, logout, and invitation password setup |
| Identity and authorization | Token-derived user identity, patient/admin role checks, active administrator profiles, user-role records, patient-scoped database access, and Supabase RLS policies |
| Patient profile | Authenticated profile reads, selectable fields, validated single-attribute updates, and chat-based read/update requests |
| Patient chat agent | LangGraph routing for profile reads, profile-update proposals, appointment requests, confirmations, and unsupported requests |
| Administrator chat agent | Dedicated `createPatient` handoff that collects required information, requires explicit confirmation, and sends a Supabase email invitation |
| Persistent conversations | Role-aware sessions, messages, runs, tool events, pending actions, and cross-session context sources stored in six shared chat tables |
| Conversation management | Session creation, restoration, pagination, close, rename, search, archive/restore, privacy-conscious titles, bounded summaries, and the three most recent patient summaries |
| Safe write workflow | Validated proposal → visible confirmation → one-time hashed pending action → deterministic database operation |
| Administrator audit | Authorized cross-role session discovery, combined audit bundles, action status, patient summaries, sanitized JSON export, and removal of confirmation token hashes |
| Appointment coordination | Provider and availability administration, patient appointment listing/search, booking/rescheduling/cancellation proposals, transactional Supabase functions, and confirmation before mutation |
| Natural-language scheduling | Deterministic interpretation of phrases such as `how soon`, `tomorrow afternoon`, `next week`, named weekdays, weekends, months, and bounded day/week ranges using the browser's IANA timezone |
| Frontend | Responsive Vite/React patient and administrator chat workspaces, structured result cards, confirmation UI, optimistic messages, retry/recovery behavior, offline awareness, and an administrator scheduling dashboard |
| Observability | Structured request and domain logging, request correlation IDs, persisted sanitized tool traces, safe error envelopes, and deliberate exclusion of tokens/passwords from logs |
| Database evolution | Versioned Supabase migrations for profiles, triggers, RLS, durable chat, role-aware ownership, appointment coordination, and the appointment chat intent constraint |

### Final architecture in simple terms

```text
User signs in with Supabase
        ↓
React sends the access token and chat request
        ↓
FastAPI verifies the token and derives the acting user
        ↓
LangGraph identifies the allowed intent
        ↓
Deterministic service validates inputs and calls a narrow database tool
        ↓
Supabase applies RLS or a protected server-side transaction
        ↓
The response, run metadata, tool trace, and safe conversation history are stored
```

The model never supplies the trusted patient or administrator identity. Reads may execute
immediately, while profile changes, invitations, bookings, rescheduling, and cancellations use an
explicit confirmation boundary. This separation keeps language understanding flexible while
leaving authorization, validation, and database mutation in deterministic code.

### Final verification status

- Backend lint passed.
- **186 backend tests passed.**
- Frontend lint passed.
- **15 frontend tests passed.**
- The production frontend build passed.
- Local and linked Supabase migration history match through
  `20261010120000_allow_appointment_chat_intent.sql`.
- The linked Supabase schema lint reported no errors.
- Read-only live contract checks validated provider, availability, and appointment result shapes.
- A live service check confirmed that natural wording such as `cardiologist` resolves to the stored
  `Cardiology` specialty and returns a strictly validated availability result.

### Intentionally unfinished or deferred

This repository should be treated as a tested development prototype, not as a production healthcare
system. The following roadmap items remain incomplete or require a production decision:

- permanent patient-account deletion and its retention/legal policy;
- standalone profile, inline-editing, and account-deletion frontend pages (the implemented patient
  experience is chat-first);
- public self-sign-up, which was intentionally replaced by administrator email invitation;
- full end-to-end browser automation against a dedicated test Supabase project;
- production privacy/compliance assessment, retention policy, and legal review;
- dependency/security scans and a formal audit-log retention strategy;
- production hosting, environment separation, CI/CD, monitoring, backups, and rollback procedures;
- notification, payment, medical-record, diagnosis, and medical-advice capabilities.

Before real patient data is used, complete the security and compliance checklist, use a separate
production Supabase project, rotate development credentials, configure HTTPS and production CORS,
run end-to-end isolation tests, and obtain the applicable healthcare privacy review. The current
application deliberately does not provide diagnoses, emergency guidance, or clinical advice.

### Closing assessment

The central goal was achieved: Careline demonstrates how a modern chat interface can safely route
authenticated patient and administrator requests through LangGraph while preserving durable,
role-aware history in Supabase. The strongest design outcome is the boundary between model-driven
understanding and deterministic authorization, validation, confirmation, and persistence. The
remaining work is primarily production hardening, compliance, deployment, and product expansion
rather than completion of the core conversational prototype.

### Portable Supabase installation

All required database SQL is stored in one ordered location:
[`supabase/migrations`](supabase/migrations). The folder contains 12 timestamped migrations and is
the source of truth for both new and existing environments. [`supabase/seed.sql`](supabase/seed.sql)
is intentionally data-free; it exists because `config.toml` enables seeding during local resets,
but it never creates environment-specific users or business data. A detailed migration manifest
and clean-install guide is available in [`supabase/README.md`](supabase/README.md).

For a brand-new hosted project, run from the repository root:

```powershell
npx supabase login
npx supabase link --project-ref <new-project-ref>
npx supabase migration list
npx supabase db push --dry-run
npx supabase db push
npx supabase db lint --linked
npx supabase db push --dry-run
```

The first dry run should show all 12 migrations. The final dry run should say
`Remote database is up to date`. Administrator records cannot be safely seeded because they must
reference a real Supabase Auth user in the target environment; provision the first administrator
after deployment using the earlier one-time provisioning instructions. Doctors and availability
slots are also runtime business data and should be created through the protected administrator
APIs.

For a disposable local clean-install test, start Docker Desktop and run:

```powershell
npx supabase start
npx supabase db reset
npx supabase db lint
```

This local reset is the strongest proof that a blank database can replay the complete migration
chain. It is destructive only to the local Supabase development database and must not be used as a
hosted-database deployment command.

---

## Architecture explained step by step

Careline has three main layers: **React** presents the patient and administrator screens;
**FastAPI** verifies users, coordinates workflows, and applies business rules; **Supabase** manages
authentication and stores application data. **LangGraph**, running inside the backend, connects
language understanding to the permitted services.

See the [detailed architecture diagrams](docs/architecture.md) for the system map, chat sequence,
data ownership diagram, and implementation file references. The walkthrough below follows a
patient asking: **“Find me a cardiologist next week.”**

### Step 1: Sign in through Supabase Auth

The React frontend sends login details to Supabase Auth. After successful authentication, Supabase
returns an access token and session information. The frontend maintains that session and attaches
the access token to requests sent to FastAPI. Token refresh is handled through Supabase Auth.

### Step 2: Open the appropriate workspace

The frontend opens the patient workspace for a patient. Administrator access requires additional
backend checks: both an active `admin_profiles` record and an active admin assignment in
`user_roles`. Authorized administrators can access invitations, appointment administration, and
auditing. Frontend route guards help navigation; the backend still authorizes protected operations.

### Step 3: Start or reopen a conversation

For a new conversation, the frontend asks FastAPI to create a session. The backend stores a record
in `chat_sessions` and returns its `session_id`. Reopening a session loads its saved messages.

`chat_initiated_by` identifies the user who started the conversation, and `chat_initiator_role`
records the acting role. `subject_patient_id` identifies the patient the conversation concerns,
when applicable. An administrator chatting about a patient remains the administrator initiating
the chat; these two identities are separate.

### Step 4: Prepare previous context

A new patient session can link to a configurable number of eligible earlier conversation summaries
(three by default). For each turn, the backend loads bounded recent messages from the current
session and the previous summaries linked to it. This supports follow-up questions without sending
the entire historical conversation collection to the model.

### Step 5: Submit the patient's message

The browser sends the access token in the authorization header and a body such as:

```json
{
  "session_id": "<current-conversation-uuid>",
  "client_message_id": "<new-message-uuid>",
  "message": "Find me a cardiologist next week",
  "timezone": "America/Vancouver"
}
```

Replace the UUID placeholders with actual UUIDs when testing. The patient endpoint is
`POST /api/v1/chat`; administrator messages use `POST /api/v1/admin/chat` and its separate schema.

| Value | Purpose |
|---|---|
| Access token | Proves the authenticated identity |
| `session_id` | Identifies the conversation |
| `client_message_id` | Identifies one submitted action and supports duplicate detection |
| `timezone` | Supplies the patient's calendar timezone for supported relative date phrases |
| Request ID | Correlates the HTTP request with backend logs and run metadata |

### Step 6: Validate identity, input, and ownership

FastAPI verifies the token's signature using Supabase public JWKS keys and checks required claims.
The authenticated user ID comes from that verified token, not from the message text. Request
validation and persistence checks enforce the expected format, session ownership, active session
status, duplicate-message protection, and restrictions on overlapping turns. Administrator routes
also enforce their administrator authorization requirements.

#### Step 6 explained: JWT verification and administrator authorization

This step has two separate purposes:

1. **Authentication** answers: “Who sent this request, and can Careline trust that identity?”
2. **Authorization** answers: “Is that verified identity allowed to use this particular feature?”

For example, assume an administrator named Anita signs in. Supabase Auth returns a JWT access
token. React later sends it with an administrator chat request:

```http
POST /api/v1/admin/chat
Authorization: Bearer <Anita's access token>
Content-Type: application/json
```

The request body contains the conversation data, but it does not supply a trusted administrator
identity:

```json
{
  "session_id": "<conversation-uuid>",
  "client_message_id": "<new-message-uuid>",
  "message": "Create a new patient"
}
```

##### What is inside a JWT?

A JSON Web Token has three signed parts:

```text
header.payload.signature
```

The header and payload can be read by a browser or attacker, so reading them is not enough to
trust them. The signature is what makes the values verifiable. A simplified, illustrative token
payload looks like this:

```json
{
  "sub": "11111111-1111-4111-8111-111111111111",
  "iss": "https://<project-ref>.supabase.co/auth/v1",
  "aud": "authenticated",
  "exp": 1791766800,
  "iat": 1791763200,
  "role": "authenticated"
}
```

| Claim | Meaning in Careline |
|---|---|
| `sub` | Authenticated Supabase user ID; Careline treats this as the acting user only after verification |
| `iss` | Expected Supabase Auth issuer for this project |
| `aud` | Expected audience: `authenticated` |
| `exp` | Token expiration time |
| `iat` | Time the token was issued |
| `role` | Supabase database role; it does not, by itself, grant Careline administrator access |

##### How JWKS signature verification works

Supabase signs JWTs with a private key. The private key remains with Supabase. FastAPI verifies
the signature with the matching public key published at the project's JWKS endpoint:

```text
https://<project-ref>.supabase.co/auth/v1/.well-known/jwks.json
```

The token header contains a `kid`, or key ID. Careline uses it to select the matching public key
from the JWKS. The backend then verifies the signature and requires only `ES256` or `RS256` signing
algorithms. It also checks:

| Validation | Reason |
|---|---|
| Signature | Detects a forged or changed token |
| Allowed algorithm | Rejects unsupported signing methods, including unsigned tokens |
| `exp` | Rejects expired access tokens |
| `iss` | Rejects tokens issued by another authentication system or Supabase project |
| `aud` | Requires the expected `authenticated` audience |
| Required claims | Requires `exp`, `sub`, `aud`, and `iss` |
| `sub` format | Requires the verified user ID to be a UUID |

For example, changing a payload from Anita's `sub` value to somebody else's ID invalidates the
signature. FastAPI rejects it because the attacker cannot create a matching signature without
Supabase's private signing key.

Only after all checks pass does Careline convert `sub` to its trusted internal identity:

```text
verified JWT → sub → CurrentUser.id
```

The access token is kept as a protected value for user-scoped Supabase calls and is not written to
routine logs. Authentication failures return a safe `401` response without exposing token details.

##### How administrator authorization works

A valid JWT proves that Anita is signed in. It does not prove that Anita is a Careline
administrator. For administrator routes, the backend uses the verified `CurrentUser.id` to require
both of these active database records:

```text
admin_profiles: id = verified user ID, is_active = true
user_roles:     user_id = verified user ID, role = admin, is_active = true
```

| Situation | Result |
|---|---|
| Missing, expired, malformed, or forged JWT | Authentication fails with `401` |
| Valid patient JWT but no active admin records | Administrator access is denied |
| Active admin profile but inactive or missing admin role | Administrator access is denied |
| Active admin role but inactive or missing admin profile | Administrator access is denied |
| Valid JWT plus both active admin records | Administrator workflow can continue |

Checking both `admin_profiles` and `user_roles` gives Careline an immediate database-controlled
way to revoke administrator access. If either record becomes inactive, future admin requests fail
even when a previously issued JWT has not yet expired.

##### The first four steps in very simple language

Use Anita, an administrator, as the example:

| Step | What happens | Simple comparison |
|---|---|---|
| 1. Anita logs in | Anita enters her email and password. Supabase checks them and gives the browser a temporary JWT access token. | Supabase gives Anita a temporary digital ID card. |
| 2. React sends the request | Anita types “Create a new patient.” React sends her message and the token to FastAPI. | React sends an envelope: the message is inside, and the ID card is attached outside. |
| 3. FastAPI takes the token | FastAPI reads `Authorization: Bearer <token>`. A missing token stops the request with `401`. | The security guard takes the ID card before allowing anyone into the building. |
| 4. FastAPI checks the token | FastAPI uses Supabase public JWKS keys to verify the signature, expiration, issuer, audience, and user ID. | The guard checks whether the ID card is genuine, current, and issued by the correct organization. |

The important point is that FastAPI does not trust a statement such as “I am Anita” in a browser
request. It trusts the user ID only after it verifies the signed token. It then checks the two admin
tables to decide whether that verified user can use administrator features.

In interview language: Supabase Auth handles authentication. FastAPI validates the JWT using the
project's JWKS keys and trusts `sub` only after signature and claim validation. Careline then uses
`admin_profiles` and `user_roles` for application-level administrator authorization. This prevents
the browser, request body, or model from assigning administrator privileges.

### Step 7: Record the start of the turn

A database function atomically stores the incoming `chat_messages` record and a running
`chat_runs` record. It returns the input message ID, run ID, and message sequence. The backend
loads the bounded context before invoking the graph. These records connect the later outcome to
the original request and allow execution failures to be recorded.

### Step 8: Route the request through LangGraph

The patient orchestrator uses structured model output to identify the intent. For this example,
the intent is `manage_appointment`, which hands control to the appointment specialist.

| Example request | Workflow |
|---|---|
| “What is my phone number?” | Read the authenticated patient's profile |
| “Change my phone number” | Validate and propose a profile update |
| “Find a cardiologist” | Hand off to the appointment specialist |
| Administrator: “Create a patient” | Administrator `createPatient` workflow |

There are three orchestration graphs: patient, administrator, and appointment specialist. Their
nodes are workflow steps. The model interprets language; application code controls the available
tools, authorization, and validation.

### Step 9: Convert the request into validated search inputs

The appointment specialist extracts the search operation, specialty, and date expression.
Deterministic backend services normalize `cardiologist` to `Cardiology` and resolve “next week”
using the backend clock and supplied IANA timezone. They validate the search range before querying
availability. Supported phrases have explicit rules; an unsupported expression triggers a
clarifying question. Requests without a timezone use UTC.

### Step 10: Retrieve real appointment availability

The appointment repository queries `provider_availability` joined to `doctor_profiles`, filtering
for matching available slots and active doctors. The service validates the returned data and maps
the provider join to the public doctor summary fields. The model does not invent available slots.
If the query returns no matches, Careline reports that no matching times were found.

The database access modes differ by operation: profile access uses the patient's JWT and RLS;
appointment operations and chat writes use protected backend service-role access. Service-role
access bypasses RLS, so those paths depend on explicit authorization, ownership checks, and scoped
repository or transaction-function inputs.

### Step 11: Save the response and execution details

After graph execution, the backend completes the turn through the persistence function. It stores
the assistant response, run outcome, tool events, and any proposed pending action.

| Table | Responsibility |
|---|---|
| `chat_sessions` | Conversation ownership, title, status, and summary |
| `chat_messages` | Ordered user messages and assistant responses |
| `chat_runs` | Execution status, intent, request ID, and input/output message links |
| `chat_tool_events` | Tool activity, duration, and sanitized execution details |
| `chat_pending_actions` | Proposed changes, token hashes, expiry, and decision status |
| `chat_session_context_sources` | Ordered links to prior sessions supplying summary context |

A read-only availability search normally creates no pending action. On a processing failure, the
backend attempts to mark the run failed and returns a safe error with a request ID.

### Step 12: Display the result in React

FastAPI returns the response and turn identifiers. React displays the answer or structured
appointment cards. A follow-up such as “Book the first option” can refer to bounded structured
results from earlier assistant messages. The backend resolves and checks the actual selected slot
again before creating a booking proposal.

### Step 13: Confirm before executing a chat mutation

The backend prepares an expiring pending action. Supabase stores its token hash and action payload;
the immediate frontend response receives the token required for confirmation. Preparing a proposal
does not book the appointment or change the profile.

When the user clicks Confirm, the browser sends a separate request with a new message UUID,
`action_id`, and `approved: true`. The backend verifies and consumes the owned action once, then
executes the stored operation. A booking transaction checks availability and applies the booking.
If the slot is no longer available, the backend returns a conflict rather than success.

This proposal/confirmation flow applies to chat-based profile updates, invitations, bookings,
rescheduling, and cancellations. Direct protected REST operations have their own contracts.
Business execution and chat completion are separate phases; they are not one global transaction.

### Step 14: Close the conversation and prepare future memory

Closing a patient session summarizes a bounded set of recent messages and stores the summary in
`chat_sessions.summary`. A future session can link that summary as context through
`chat_session_context_sources`. These summaries help with conversational continuity; current
database records remain authoritative for profile and appointment information.

### Administrator invitation example

The administrator workflow follows authenticate → collect details → propose → confirm → execute
→ save the result. The `createPatient` node collects full name, date of birth, phone, and email.
After explicit confirmation, the backend calls Supabase Auth's invitation API. The signup trigger
creates the associated profile/role records, and the service completes patient details. The invited
patient follows the email link to `/set-password` to finish account setup. The administrator's chat
history and the patient concerned remain linked without mixing their ownership identities.

---

## Interview preparation

This section uses Careline's implemented architecture. In an interview, describe the parts you personally built or validated and use the examples below to structure your explanation.

### A 60-second project introduction

“Careline is a role-aware, chat-based healthcare application. The frontend is React with Vite, the backend is FastAPI, and Supabase provides authentication and PostgreSQL storage. LangGraph coordinates patient, administrator, and appointment workflows.

The key design decision is separating language understanding from sensitive actions. The model can identify a request, but deterministic backend services enforce authentication, authorization, validation, ownership, confirmation, and database mutations. Any chat action that changes data, including profile updates, invitations, and appointment changes, is first proposed and then requires explicit user confirmation.”

### A 30-second architecture explanation

“React sends a Supabase access token with every protected request. FastAPI verifies the token using Supabase JWKS public keys and derives the authenticated user from the verified `sub` claim. LangGraph routes the request to an allowed workflow. Backend services validate the result and call scoped repositories or PostgreSQL functions. Supabase stores the result and the chat history, and React displays the response.”

### Roles and responsibilities

| Role or component | Responsibility | It must not decide or expose |
|---|---|---|
| Patient | Reads their profile, asks appointment questions, confirms their own proposed changes | Other patients' data or administrator functions |
| Administrator | Invites patients, manages schedules, and reviews authorized audit data | Identity based on browser-supplied IDs or unrestricted browser database access |
| React frontend | Login, session restoration, protected routes, chat UI, confirmation controls | JWT verification, role assignment, service-role key, or mutation policy |
| Supabase Auth | Login, token refresh, JWT issuance, public JWKS, and email invitations | Careline's administrator authorization decision |
| FastAPI | JWT verification, input validation, role checks, graph construction, and safe errors | Trusting user IDs or permissions supplied by a browser or model |
| LangGraph | Routes requests across patient, admin, and appointment workflows | Direct database access or self-approval of actions |
| Deterministic services | Validate, normalize, check ownership, create actions, and execute approved work | Treating model output as authorization |
| Repository layer | Performs narrow Supabase queries and RPC calls | Returning unvalidated database rows directly to users |
| Supabase PostgreSQL | Stores records, RLS policies, constraints, triggers, and transactions | Interpreting conversational intent |

### Main components and their relationship

```text
Patient or administrator
        ↓
React frontend
        ↓  JWT + HTTP request
FastAPI request boundary
        ↓  verified identity + validated input
LangGraph workflow
        ↓  structured intent
Deterministic service
        ↓  scoped repository or PostgreSQL RPC
Supabase Auth / PostgreSQL
        ↓
Validated result, persisted chat turn, and response to React
```

| Component | Representative implementation |
|---|---|
| Frontend routes and authentication | `frontend/src/App.jsx`, `frontend/src/auth/` |
| Chat UI and API client | `frontend/src/components/ChatWorkspace.jsx`, `frontend/src/lib/` |
| API routes and dependencies | `backend/app/api/v1/`, `backend/app/deps.py` |
| JWT verification | `backend/app/core/security.py` |
| LangGraph workflows | `backend/app/agents/patient_graph.py`, `admin_graph.py`, `appointment_graph.py` |
| Business services and repositories | `backend/app/services/`, `backend/app/repositories/` |
| Database schema and functions | `supabase/migrations/` |

### Interview questions and answers: architecture

#### 1. Why did you choose React, FastAPI, Supabase, and LangGraph?

React provides a responsive browser experience and works well with Supabase sessions. FastAPI gives typed validation, dependency injection, and a clean API boundary. Supabase combines authentication, PostgreSQL, RLS, and database functions. LangGraph makes the conversational workflows explicit and testable.

#### 2. What is the end-to-end path for a patient chat request?

React sends the bearer token, session ID, client message ID, message, and timezone. FastAPI verifies identity, starts a durable chat turn, loads bounded context, invokes the patient graph, validates the service result, persists the response and traces, and returns the response for React to render.

#### 3. Why is LangGraph useful in this project?

It models the allowed transitions between clear workflow nodes. The patient graph can route only to profile reading, profile-update proposal, appointment management, confirmation handling, or a safe unsupported response. This is easier to test and audit than allowing a model to freely decide how to call application services.

#### 4. How many agents are in the application?

There are three orchestration graphs: patient, administrator, and appointment specialist. They are separate workflows with limited responsibilities. The summarizer is an additional model task that runs when a patient session closes.

#### 5. What does the model do, and what does it not do?

The model interprets language and returns structured intent and fields. It does not authenticate users, assign roles, select a trusted patient identity, query Supabase directly, construct arbitrary SQL, approve a mutation, or hold service credentials.

#### 6. Why do you use a repository layer?

Repositories centralize Supabase queries and RPC calls. Services focus on business rules such as ownership, validation, and response shaping. This improves testability because repository calls can be replaced by fakes in automated tests.

#### 7. How does “Book the first option” work safely?

The conversation context includes bounded structured results from earlier assistant messages. The graph can resolve “first option” to a returned slot reference, but treats that context as untrusted. The backend retrieves the slot again from the database before creating a booking proposal.

#### 8. How does natural-language appointment date handling work?

The model extracts a phrase such as “next week.” Deterministic backend code resolves only supported phrases using the browser's IANA timezone and the backend clock, then validates the exact date range before querying real availability. Unsupported phrases receive a clarification request.

### Interview questions and answers: authentication and security

#### 9. What is the difference between authentication and authorization?

Authentication confirms who made the request. Careline verifies a Supabase JWT and uses its verified `sub` claim as the acting user ID. Authorization decides what that verified user may do. Admin routes require active records in both `admin_profiles` and `user_roles`.

#### 10. How does FastAPI verify a Supabase JWT?

It selects the signing key from Supabase's JWKS endpoint using the token's `kid`. It verifies the signature while allowing only `ES256` and `RS256`, and validates issuer, audience, expiry, required claims, and the UUID format of `sub`.

#### 11. Why is JWKS needed instead of simply decoding a JWT?

Decoding only reads a token's text; anyone could create a token-shaped string claiming another user ID. JWKS provides public keys that verify the cryptographic signature. A valid signature proves the expected Supabase project issued the token and that its contents were not changed.

#### 12. Why do you check both `admin_profiles` and `user_roles`?

One check confirms an active administrator profile and the other confirms an active admin role assignment for the same verified identity. This lets the current database state revoke admin access even before a previously issued access token expires.

#### 13. Why not trust the JWT `role` claim for administrator access?

The Supabase `role` claim represents a database/API role such as `authenticated`. It is separate from Careline's business-level administrator role. Careline uses its own active profile and role records for that decision.

#### 14. How is patient data isolated?

The backend derives the patient identity from the verified JWT rather than accepting a patient ID in the request body or URL. User-scoped profile queries use the JWT-backed Supabase client and RLS. Services also apply explicit ownership filters as defense in depth.

#### 15. Why do some operations use the service-role client?

Chat persistence, administrator operations, and appointment transactions need backend-controlled access and PostgreSQL functions. The service-role key remains only on the backend. Because it bypasses RLS, each service-role path validates identity, role, and ownership before querying or mutating data.

#### 16. How do you protect secrets and sensitive values?

The browser receives only public Supabase configuration. Backend environment values contain the service-role and model keys. Tokens are wrapped as protected values where possible, and structured logs avoid raw tokens, passwords, message content, and sensitive profile values.

### Interview questions and answers: chat and persistence

#### 17. Why do you persist a chat turn before calling the model?

It creates a durable record that the request began and gives every execution a run ID. If processing fails later, the system can mark that run failed instead of losing the request history. It also supports duplicate protection and traceability.

#### 18. What does `client_message_id` solve?

It is an idempotency ID generated by the frontend for one user action. If the browser retries the same request after a network problem, the backend recognizes that it was already processed and avoids running the action twice.

#### 19. Why are there six chat tables instead of one large messages table?

Each table has one focused purpose. Sessions organize conversations, messages store dialogue, runs store execution state, tool events store sanitized operational traces, pending actions store expiring proposals, and context sources record cross-session memory links. This makes auditing, pagination, status handling, and retention decisions clearer.

#### 20. What is a pending action?

It is a server-stored, short-lived proposed operation. It contains validated action data, an expiry, a decision status, and a hash of its confirmation token. It represents “this change is ready for approval,” not “this change has happened.”

#### 21. Why store a token hash instead of the raw confirmation token?

If the database were exposed, a raw token could be replayed. Storing only a hash allows the backend to compare a submitted token without retaining the usable original. The raw token exists only in the immediate HTTPS response and frontend memory.

#### 22. How do you prevent double booking or repeated confirmation?

The pending action is consumed once, and appointment changes use PostgreSQL transaction functions. The transaction verifies that the target slot is still available. If another request already changed the slot, the backend returns a conflict instead of reporting success.

#### 23. How does cross-session memory work?

When a patient closes a session, the backend summarizes a bounded set of messages. A future session links a limited number of eligible prior summaries through `chat_session_context_sources`. The model uses them as context, while current database records remain authoritative.

#### 24. How are failed chat turns handled?

If a model, database, or validation step fails, the backend attempts to mark the existing run as failed and sends a safe error response with a request ID. The frontend keeps the failed message so the user can understand the result and retry when appropriate.

### Interview questions and answers: appointments and administration

#### 25. How do you ensure appointment availability is real?

The model never creates slots. The appointment service queries `provider_availability` and active `doctor_profiles`, validates returned records against strict response models, and returns only records that exist in Supabase.

#### 26. How do you handle “cardiologist” when the database stores “Cardiology”?

The appointment service uses a deterministic mapping for understood practitioner titles, such as `cardiologist` to `Cardiology`. Unknown specialty text is preserved rather than guessed, so the model cannot silently invent database categories.

#### 27. How does an administrator create a patient?

The admin graph collects full name, date of birth, phone, and email through chat. It proposes the invitation and waits for explicit confirmation. After approval, the backend uses Supabase Auth's administrator invitation API, and the signup trigger creates the related profile and role records.

#### 28. What happens if the invitation succeeds but profile completion fails?

The service attempts a compensating cleanup by deleting the newly invited auth user. This avoids leaving a partial patient identity that would prevent the administrator from safely retrying the invitation.

#### 29. What can an administrator audit?

An active administrator can inspect an authorized, sanitized audit bundle that combines session, message, run, tool-event, pending-action, and context-source information. Pending-action token hashes are removed before the API response or JSON export is returned.

#### 30. Why do `chat_initiated_by` and `subject_patient_id` both exist?

They describe different things. `chat_initiated_by` identifies who started the conversation, such as an administrator. `subject_patient_id` identifies the patient the conversation concerns. Keeping them separate prevents the system from confusing the acting user with the patient subject.

### Interview questions and answers: engineering and tradeoffs

#### 31. How did you test the project?

The backend has unit and workflow tests using fake repositories and classifiers so they do not call Supabase or a model. Frontend tests check role-aware requests, session behavior, and access routing. The project also uses linting and a production frontend build. Read-only live checks validated the linked Supabase schema and appointment response shape.

#### 32. What would you improve before production?

I would complete formal privacy and compliance review, production hosting and CI/CD, dependency security scans, monitoring and backups, dedicated end-to-end testing against a separate test project, and a defined patient-account retention and deletion policy.

#### 33. What was the most important tradeoff?

The central tradeoff was between flexible AI conversation and predictable protected operations. The model understands language, but identity, authorization, validation, confirmation, and database writes remain in deterministic code. This adds workflow steps but makes the system easier to test and reason about.

#### 34. Why use structured model output?

Free-form model text is hard to validate safely. Structured output supplies expected fields such as intent, specialty, IDs, and missing information. The backend still treats the result as a suggestion and validates every sensitive value before performing an operation.

#### 35. How would you scale this architecture?

I would run FastAPI with multiple workers behind a load balancer, keep PostgreSQL as the source of truth for turn serialization and pending actions, add connection pooling and observability, queue suitable long-running background work, and tune indexes from real query metrics. Persisted state avoids depending on one backend process's memory.

#### 36. What is the strongest security design decision in Careline?

The strongest decision is separation of responsibility. The browser never receives privileged keys, the model cannot authorize itself, FastAPI verifies identity before every protected request, and sensitive mutations require one-time confirmation backed by server-side state.

### Questions you can ask the interviewer

1. “How does your team separate model orchestration from authorization and database mutation?”
2. “What approach do you use for role management and permission revocation?”
3. “How do you test idempotency and concurrency for workflows that change customer data?”
4. “What observability do engineers have for failed AI workflow steps without exposing sensitive data?”
5. “How does the team evaluate prompt, model, and workflow changes before production release?”
