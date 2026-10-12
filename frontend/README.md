# Careline frontend

The Careline frontend is a Vite + React application for role-aware patient and administrator
conversations. Frontend Steps 1 and 2 implement authentication, role-aware routing, persistent
session history, messaging, and explicit confirmation cards for agent-proposed actions.

## Configure

Copy `.env.example` to `.env` and provide:

```dotenv
VITE_SUPABASE_URL=https://YOUR-PROJECT.supabase.co
VITE_SUPABASE_ANON_KEY=YOUR-SUPABASE-ANON-KEY
VITE_API_BASE_URL=http://127.0.0.1:8000/api/v1
```

Only the Supabase anon/publishable key belongs in the browser. Never add the service-role key or
OpenAI key to a `VITE_` variable.

The backend CORS `FRONTEND_ORIGIN` must match the URL used by the browser. The recommended local
combination is:

```text
Frontend: http://localhost:5173
Backend:  http://127.0.0.1:8000
```

## Run

Start the backend first, then from this directory:

```powershell
npm install
npm run dev
```

Open `http://localhost:5173`.

## Implemented routes

| Route | Behavior |
|---|---|
| `/login` | Supabase email/password login for any Careline role |
| `/` | Protected role resolver; administrators go to admin chat, others to patient chat |
| `/admin/chat` | Administrator conversation workspace and patient-invitation flow |
| `/patient/chat` | Patient conversation workspace and profile read/update flow |
| `/set-password` | Completes password setup from a Supabase invitation session |

## How authentication works

1. `AuthProvider` asks Supabase for the stored browser session.
2. `onAuthStateChange` keeps React synchronized with login, refresh, recovery, and logout.
3. `ProtectedRoute` prevents unauthenticated workspace access; `AdminRoute` separately asks the
   backend to confirm administrator access before rendering the administrator workspace.
4. The `/` route calls the protected admin-session endpoint. Success proves an active admin role;
   `403` selects the patient workspace. Browser data never grants an admin role.
5. `apiRequest()` obtains the current Supabase session immediately before each FastAPI call and
   attaches its access token as a bearer credential.

The application logs safe event names and technical categories in development. It does not log
tokens, passwords, email addresses, chat messages, or patient attributes.

## Invitation password flow

Supabase invitation emails must redirect to:

```text
http://localhost:5173/set-password
```

Add that exact URL to Supabase Authentication URL Configuration. Supabase reads the invitation
link, establishes the recovery session, and the page lets the invited patient choose a password.
After success, the user is sent to the patient workspace.

## Verify

```powershell
npm run lint
npm run build
npm run test
```

All three commands pass for the current implementation. The automated tests verify exact
role-specific chat URLs and payloads, fresh client-message UUIDs, opaque cursor forwarding, admin
access, patient redirection, rename requests, and archive routes. Manual checks:

1. An unauthenticated visit to `/` redirects to `/login`.
2. Wrong credentials show a friendly error without exposing provider details.
3. Admin login reaches `/admin/chat`.
4. Patient login reaches `/patient/chat`.
5. Refresh preserves the signed-in session.
6. Sign out clears the session and returns to login.
7. A valid invitation link opens `/set-password` and matching passwords complete setup.

## Chat workspace behavior

The patient and administrator routes share one visual component, but they do not share authority.
`chatApi.js` selects the correct role-specific FastAPI endpoints and the backend validates the
Supabase token and role on every request.

When the workspace opens:

1. It loads the signed-in user's newest 30 sessions.
2. It selects the newest session and asks for the newest 50 messages. FastAPI reads that page
   newest-first, then returns the page in normal chronological display order.
3. Selecting another session clears the current timeline before loading that session's messages.
4. **Load more** uses the opaque `next_cursor` returned by FastAPI and prepends the earlier page.
   The browser never creates or interprets cursors.
5. A new conversation is created before any message can be submitted, guaranteeing that every
   LangGraph turn is attached to a stored session.

For each submitted message, the browser generates a fresh `client_message_id` with
`crypto.randomUUID()`. This gives the backend an idempotency key: a repeated network request cannot
silently process the same action twice. The ID is never reused for a later message or confirmation.

### Patient request sequence

```text
POST /chat/sessions                         create a conversation
GET  /chat/sessions                         list owned conversations
GET  /chat/sessions/{id}/messages           restore the selected timeline
POST /chat                                  send a profile question/update request
POST /chat                                  approve or reject a proposed update
POST /chat/sessions/{id}/close              summarize and close the conversation
```

### Administrator request sequence

```text
POST /admin/chat/sessions                   create an invitation conversation
GET  /admin/chat/sessions                   list this admin's conversations
GET  /admin/chat/sessions/{id}/messages     restore the selected timeline
POST /admin/chat                            send patient information
POST /admin/chat                            approve or reject the invitation
POST /admin/chat/sessions/{id}/close        summarize and close the conversation
```

The administrator screen shows `subject_patient_id` after an invitation succeeds. Before a
patient has been created, that value correctly remains empty.

### Confirmation-token security

An action such as updating a phone number or inviting a patient returns a one-time
`pending_action_id`. The interface keeps that token only in React memory and displays a Confirm and
Cancel card. The durable assistant message deliberately omits the plaintext token. Therefore:

- the current proposal can be confirmed or cancelled immediately;
- refresh or session switching removes the live confirmation buttons;
- restored history still shows what was proposed, but labels it as a historical proposal;
- the user must submit the request again to receive a new confirmation token.

This is intentional and prevents a sensitive action link from being replayed from stored message
history.

### Why the newest conversation is requested separately

The durable history API supports `newest_first=true` for the live workspace. Without it, page one
contains the oldest messages; once a session exceeds one page, a newly saved question and answer
would be stored correctly but would not appear until every earlier page was loaded. The frontend
now requests:

```text
GET /chat/sessions/{session_id}/messages?limit=50&newest_first=true
```

The administrator route uses the equivalent `/admin/chat/sessions/...` URL. The interface also:

- shows the outgoing question immediately while the agent is working;
- reloads the authoritative stored records when the turn finishes;
- ignores a late response belonging to a session the user has already left;
- keeps the newest question and answer visible after refresh;
- prepends older pages instead of placing them after newer messages.

## Test the chat workspace in the browser

Start FastAPI at `http://127.0.0.1:8000`, start Vite with `npm run dev`, then open
`http://localhost:5173`.

### Patient flow

1. Sign in using a patient account. The browser should open `/patient/chat`.
2. Click **+** or **Start conversation**. A new active session should appear on the left.
3. Enter `Show my full name and phone number`. The message and profile response should appear and
   remain after a refresh.
4. Enter `Update my phone number to +2131234567`. A review card should appear.
5. Click **Cancel** and verify the timeline says the update was cancelled.
6. Repeat the update, click **Confirm change**, then ask for the phone number again.
7. Click **Close conversation**. The composer should become disabled and the session should show as
   closed.
8. Create another session and use the left panel to switch between both histories.

### Administrator flow

1. Sign in using an active administrator account. The browser should open `/admin/chat`.
2. Create a new conversation.
3. Enter, for example:

   ```text
   Invite Jane Doe, born 1992-04-15, phone +14165550123, email jane@example.com.
   ```

4. Verify the review card shows all four fields, then click **Cancel** once to test the safe path.
5. Submit the request again and click **Confirm & invite**. Use only a test email address that is
   allowed to receive an invitation.
6. Verify the success message persists after refresh and the session header now shows its linked
   patient UUID.

### Expected safety/error behavior

- Sending is disabled while a request is running, for a closed session, or while a confirmation is
  waiting.
- Pressing Enter sends; Shift+Enter inserts a new line.
- API failures show the safe backend message and request ID, when supplied.
- A patient who manually visits `/admin/chat` is redirected to `/patient/chat`.
- Signing out returns to `/login`; visiting a protected route then redirects to login.
- The browser console never logs access tokens, passwords, messages, or patient data.

### Regression check for a missing newest turn

1. Open one patient session and send several questions.
2. Refresh the page and select the same session.
3. Verify the final question and Careline answer are at the bottom of the timeline.
4. Send another question. It should appear immediately, followed by the answer when processing
   completes.
5. Switch rapidly between two sessions. Messages from the first session must never replace the
   selected session's timeline.
6. If **Load more messages** appears, click it and verify older messages are added above the current
   timeline.

## Conversation organization

The workspace supports automatic titles, manual rename, local search, status filters, archive, and
restore for patient and administrator conversations.

After the first successful message, a default title is replaced with a broad, privacy-conscious
category such as `Update phone number`, `Profile address`, `Profile information`, or
`Patient invitation`. It does not copy a full message or patient contact value into the title, and
it does not require another model call.

Select a session and click **Rename** to save a custom title between 1 and 200 characters. Search
matches titles and summaries in the session pages currently loaded in the sidebar. Filters show
All, Active, Closed, or Archived conversations.

Archive uses this controlled lifecycle:

```text
active --Close conversation--> closed --Archive--> archived
archived --Restore--> closed
```

An active session cannot be archived because closing generates its summary. Archived sessions are
read-only and restorable. A restored session remains closed; start a new conversation to continue.

Protected endpoints:

```text
PATCH /chat/sessions/{session_id}
POST  /chat/sessions/{session_id}/archive
POST  /chat/sessions/{session_id}/restore

PATCH /admin/chat/sessions/{session_id}
POST  /admin/chat/sessions/{session_id}/archive
POST  /admin/chat/sessions/{session_id}/restore
```

### Test conversation organization

1. Create a patient session and send `Update my phone number to +2131234567`.
2. Verify the automatic title becomes **Update phone number**.
3. Click **Rename**, change it to `Contact details`, and save.
4. Search for `Contact` and verify the session remains visible.
5. Close it, choose the **Closed** filter, and verify it appears.
6. Click **Archive**, choose **Archived**, and verify it appears there.
7. Open it and click **Restore**. It should return to Closed and stay read-only.
8. Repeat with an administrator session; its automatic title becomes **Patient invitation**.

## Reliability and recovery

The chat workspace recovers without duplicating a patient action.

If FastAPI returns `401`, the shared API client asks Supabase to refresh the session and retries the
original request exactly once. Concurrent failures share one refresh operation. If refresh fails,
Careline signs out only the local browser session and the protected route returns to login. Tokens
and refresh tokens are never logged.

A `client_message_id` is created before sending and retained when the request fails. The failed
message remains in the timeline with **Retry**. Retry uses the same message and same ID; a new user
turn always gets a new ID. If the first attempt completed but its response was lost, the backend
returns `chat_turn_already_processed` and the frontend reloads durable history instead of creating a
duplicate.

Unsent text is stored in `sessionStorage` under the signed-in user ID, role, and session ID. Each
conversation has an independent draft. It survives session switching and refresh in the same tab,
is removed after successful sending, and is never logged.

Offline mode shows a banner, disables sending, and preserves the draft. While an online request is
running, the outgoing question appears immediately and **Careline is thinking…** remains visible.

If confirmation returns `chat_action_invalid` or `admin_chat_action_invalid`, the action changes to
**Confirmation expired** with **Request again**. After refresh, the latest unresolved historical
proposal also offers Request again using its preceding stored user request. This creates a new turn
and new one-time token; the old plaintext token is never persisted.

### Test reliability manually

1. **Drafts:** type without sending, switch sessions, return, and verify each session restores its
   own text. Refresh and verify the selected draft remains.
2. **Offline:** use browser developer tools → Network → Offline. Verify the banner appears, Send is
   disabled, and the draft remains. Return to Online and send it.
3. **Thinking:** send a profile question. Verify the outgoing message appears before the answer and
   the processing indicator is visible.
4. **Retry:** stop FastAPI, try to send, and verify the failed bubble has Retry. Restart FastAPI and
   click Retry. Only one durable user turn should appear.
5. **Expired confirmation:** request an update, refresh before confirming, then click **Request
   again** on the historical proposal and confirm the newly generated proposal.
6. **Token refresh:** `npm run test` verifies one refresh and one retry after `401`; refresh failure
   signs out the local session.

## Administrator audit dashboard

Administrators can click **Audit details** in any administrator conversation. The drawer can then
search and select from the newest 100 conversations across patient, administrator, and future
doctor roles—not only conversations initiated by the signed-in administrator.

The audit view joins the six chat tables by `session_id` and displays:

- session owner, initiator role, linked patient, status, model, and prompt version;
- stored user, assistant, and system messages;
- LangGraph runs, request IDs, detected intent, decision data, latency, and safe error codes;
- ordered node/tool events with sanitized arguments and results;
- pending-action payload, computed status, expiry, and decision;
- prior-session context sources, including source title and included summary;
- the linked patient's newest three summarized patient conversations.

Patient summaries are read from `chat_sessions.summary`. They include closed or archived
patient-initiated sessions, are ordered newest-first by close time, and never trigger a new model
request. If the selected session has no `subject_patient_id`, the drawer shows that no patient is
linked.

The **Download sanitized JSON** button exports exactly the server response. It never includes a
pending-action `token_hash`, access/refresh token, password, service key, or raw provider exception.
Message and summary content is visible because this endpoint explicitly requires an active
administrator account.

### Audit API

```text
GET /api/v1/admin/audit/sessions?limit=100
GET /api/v1/admin/audit/sessions/{session_id}
```

Both endpoints require a valid Supabase bearer token plus active records in both `user_roles` and
`admin_profiles`. A patient token receives `403`; no token receives `401`. Audit access logs include
only administrator/session/patient identifiers and record counts, never message or summary text.

### Test the audit dashboard

1. Sign in as an active administrator and open `/admin/chat`.
2. Select any session and click **Audit details**.
3. Search by session ID, patient ID, role, or title and select a result.
4. Verify Messages, LangGraph runs, Tool events, Pending actions, and Context sources are visible.
5. For a session with a linked patient, verify up to three summaries appear newest-first.
6. Download the JSON and search it for `token_hash`; there must be no match.
7. Sign in as a patient and call either audit URL through Swagger; verify access is denied.

## Main files

```text
src/
  auth/
    AuthContext.jsx       session lifecycle and auth operations
    authState.js          context hook shared without Fast Refresh warnings
    AdminRoute.jsx        backend-confirmed administrator navigation guard
    ProtectedRoute.jsx    authenticated route boundary
  components/
    AppShell.jsx          shared authenticated header and logout
    Brand.jsx             Careline identity
    ChatWorkspace.jsx     sessions, timeline, composer and confirmation cards
    FullPageLoader.jsx    accessible full-page progress state
  lib/
    apiClient.js          bearer-authenticated FastAPI client
    chatApi.js            role-aware chat/session endpoint wrapper
    chatApi.test.js       patient/admin request contract tests
    logger.js             privacy-safe development events
    runtimeConfig.js      Vite environment validation
    supabase.js           single browser Supabase client
  pages/
    LoginPage.jsx
    RoleLandingPage.jsx
    SetPasswordPage.jsx
  test/
    setup.js              DOM matcher and per-test render cleanup
```
