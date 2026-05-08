# Messages Feature — Build Plan

> Status: **planning only — no code yet.** Author: tech-lead review pending.
> The Messages tab in the Communication Hub is partially built: FE is
> production-shape; BE is scaffolded but not finished and not registered.
> This plan scopes the work to bring it live.

---

## 1. Context

The "Messages" sidebar entry in the FE renders an empty UI (Inbox/Unread/
Sent/Draft all show 0) and clicking "New Message + Send" appears to do
nothing. Investigation confirmed:

- The FE component tree under `components/app/messages/` is fully
  implemented (`MessagesInbox`, `NewMessageModal`, `MessageThreadView`,
  `MessageThreadList`, `MessageThreadCard`, `AttachmentRenderer`,
  `SendResponseModal`, `SendTaskModal`).
- The FE API client (`services/client/threads.ts`) calls **six
  endpoints** that don't exist on the BE.
- The BE has a partially-written `app/api/routes/api/threads.py` with
  every line **commented out**, plus models (`ChatThread`,
  `ThreadParticipant`) that are **incomplete** (missing columns, FKs
  commented out, no relationships wired up).
- There is no `thread_messages` table or message-attachment plumbing.
- Email notification has **no implementation path** today —
  `email_service.py` only knows `send_invitation_email()`.

The FE swallows the resulting 404s in its `useCreateThread` mutation
without surfacing a toast, so the user sees nothing happen. That alone
is a bug worth fixing as a one-line change once the BE is back online.

## 2. Target end-state

A working multi-participant messaging feature scoped to a clinical
trial:

- A user (creator) selects recipients (to / cc), an optional trial
  context, types a subject + content, and clicks Send.
- A new thread is persisted; each recipient sees it in their Inbox.
- Recipients can open the thread, read messages, reply, and mark as
  read. Unread count drops accordingly.
- Threads can carry optional attachments (a task reference or a
  RAG-response snapshot — the FE already supports both).
- **Optional add-on (separate phase)**: when a new message lands,
  recipients receive an email notification with a deep-link back into
  the app.

Out of scope for this plan: real-time push (WebSockets / SSE), search
across threads, message editing, file uploads as attachments, group
mentions.

## 3. FE→BE contract drift (the canonical reference for this work)

The FE types in `services/threads/types.ts` are the **authoritative
contract**. The BE will be reshaped to match them, not the other way
around. Key shapes:

### `MessageThread` (FE) — what the BE must return on GET/POST

```ts
{
  id: string,
  trial_id: string,
  subject: string,           // ← BE's ChatThread.title must rename to `subject`
  created_by: string,
  created_at: string,
  deleted_at: string | null,
  participants?: ThreadParticipant[],   // populated via join
  messages?: ThreadMessage[],           // populated via join (last N or all)
  creator?: { id, email, full_name },   // populated via join on profiles
  trial?: { id, name },                 // populated via join on trials
}
```

### `ThreadParticipant` (FE)

```ts
{
  thread_id: string,
  user_id: string,
  participant_type: 'to' | 'cc',         // ← BE missing this column
  last_read_message_id: string | null,
  joined_at: string,                     // ← BE missing this column
  user?: { id, email, full_name },
}
```

### `ThreadMessage` (FE) — needs a brand-new BE table

```ts
{
  id: string,
  thread_id: string,
  sent_by: string,
  content: string,
  sent_at: string,
  parent_message_id: string | null,
  deleted_at: string | null,
  sender?: { id, email, full_name },
  attachments?: MessageAttachment[],
}
```

### `CreateThreadInput` (FE → BE on POST)

```ts
{
  trial_id?: string,
  subject: string,
  content: string,                          // first message
  to_users: string[],                       // user_ids
  cc_users?: string[],
  attachment?: { type: 'task', task_id }
             | { type: 'response', response_snapshot },
}
```

### Routes the FE expects

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/client/{orgId}/threads` | List user's threads (filters: `trial_id`, `unread_only`) |
| POST | `/api/client/{orgId}/threads` | Create thread + first message + participants in one call |
| GET | `/api/client/{orgId}/threads/{threadId}/messages` | List messages in a thread |
| POST | `/api/client/{orgId}/threads/{threadId}/messages` | Reply to a thread |
| POST | `/api/client/{orgId}/threads/{threadId}/read` | Mark thread as read for current user |
| POST | `/api/client/{orgId}/trials/{trialId}/validate-access` | Verify recipient `user_ids` have access to a trial (used to block invalid recipients before send) |

---

## 4. Database changes

### 4.1 `chat_threads` — minor patch

| Action | Detail |
|---|---|
| Rename column | `title` → `subject` (matches FE; safer to do now than re-train UI later) |
| Existing columns kept | `id`, `trial_id`, `created_by`, `created_at`, `updated_at`, `deleted_at` |
| New relationships | `participants` (1:N to thread_participants), `messages` (1:N to thread_messages) — see model fix below |

### 4.2 `thread_participants` — finish what was started

| Action | Detail |
|---|---|
| Restore commented column | `thread_id UUID NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE` |
| Add column | `participant_type TEXT NOT NULL CHECK (participant_type IN ('to','cc'))` |
| Add column | `joined_at TIMESTAMPTZ NOT NULL DEFAULT now()` |
| Add column | `notified_at TIMESTAMPTZ NULL` (for email idempotency in Phase B) |
| Existing column | `last_read_message_id UUID REFERENCES thread_messages(id) ON DELETE SET NULL` (FK target changes — was `chat_messages` which is unrelated) |
| Add unique constraint | `UNIQUE (thread_id, user_id)` so a user can't be added twice |

### 4.3 New table — `thread_messages`

Don't overload `chat_messages` (which is owned by RAG chat sessions —
different data model). Create a new table:

```sql
CREATE TABLE thread_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id UUID NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
    sent_by UUID NOT NULL REFERENCES profiles(id),
    content TEXT NOT NULL,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    parent_message_id UUID NULL REFERENCES thread_messages(id) ON DELETE SET NULL,
    deleted_at TIMESTAMPTZ NULL
);

CREATE INDEX idx_thread_messages_thread ON thread_messages (thread_id, sent_at);
CREATE INDEX idx_thread_messages_sent_by ON thread_messages (sent_by);
```

### 4.4 New table — `message_attachments`

Keep attachments out of the messages table so they don't bloat reads:

```sql
CREATE TABLE message_attachments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id UUID NOT NULL REFERENCES thread_messages(id) ON DELETE CASCADE,
    attachment_type TEXT NOT NULL CHECK (attachment_type IN ('task','response')),
    task_id UUID NULL REFERENCES tasks(id) ON DELETE SET NULL,
    response_snapshot JSONB NULL,   -- when type='response', a denormalised snapshot
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT one_payload CHECK (
        (attachment_type = 'task' AND task_id IS NOT NULL AND response_snapshot IS NULL)
     OR (attachment_type = 'response' AND task_id IS NULL AND response_snapshot IS NOT NULL)
    )
);

CREATE INDEX idx_message_attachments_message ON message_attachments (message_id);
```

`response_snapshot` is a JSONB blob because the FE attaches a
`ResponseSnapshot` (a frozen RAG answer object — already defined in
`services/messages/types.ts`). Storing the snapshot at attachment time
keeps the message readable even if the source response is later
deleted.

### 4.5 Migration delivery

Two artifacts, matching existing patterns in the repo:

1. **`migrations/add_messages_feature.sql`** — full DDL above plus a
   verification SELECT. Idempotent (`IF NOT EXISTS` everywhere).
2. **Self-heal block in `app/main.py`** — same column-add pattern used
   for prior schema migrations (e.g., `chat_sessions.document_id`).
   Ensures a deploy with the new code automatically applies the schema
   on first boot of the new revision.

The rename (`title → subject`) is the only non-additive change. Two
safe approaches:
- **Add `subject TEXT` column, copy from `title`, then drop `title`** —
  zero downtime, two deploys.
- **Just rename in a single migration** — simpler, requires a brief
  window where the new code matches the new column. Works because the
  table currently has zero rows in production (feature was never live).

Given the table is empty, recommend the simple rename.

---

## 5. Backend code changes

### 5.1 Models (`app/models/`)

| File | Change |
|---|---|
| `chat_threads.py` | Rename `title` → `subject`. Uncomment relationships; wire `participants` + `messages` back-populates. |
| `thread_participants.py` | Uncomment `thread_id` FK. Add `participant_type`, `joined_at`, `notified_at` columns. Add `user` relationship to `Profile`. Add `__table_args__` with the unique constraint. |
| `thread_messages.py` (NEW) | New SQLAlchemy model for the new table. Includes relationships to `ChatThread`, sender (`Profile`), and `attachments`. |
| `message_attachment.py` (NEW) | New model for the new table. Polymorphic via `attachment_type`. |

Estimated effort: **~0.5 day** (mechanical, follows existing model conventions).

### 5.2 Contracts (`app/contracts/`)

| File | Change |
|---|---|
| `thread_chat.py` (REWRITE) | Currently referenced by the commented BE but content unknown. Rewrite to match the FE types: `MessageThreadResponse`, `ThreadMessageResponse`, `ThreadParticipantResponse`, `CreateThreadInput`, `ReplyToThreadInput`, `MessageAttachmentResponse`. |
| `validate_access.py` (NEW or extend existing) | `ValidateAccessRequest { user_ids: list[UUID] }` and `ValidateAccessResponse { valid_users, invalid_users }`. |

Estimated effort: **~0.5 day**.

### 5.3 Routes — rebuild `app/api/routes/api/threads.py`

Discard the commented file; write fresh against the FE contract. Six
handlers:

| Handler | Auth/Authorisation | Notes |
|---|---|---|
| `GET /` (list_threads) | JWT + member-scoped | Joins to `thread_participants` to filter to threads the caller participates in. Optional `trial_id` filter. `unread_only=true` filter joins on `participants.last_read_message_id` vs. latest message. |
| `POST /` (create_thread) | JWT + `trial_id` access via `get_trial_with_access` if provided | Validates recipient `user_ids` against the trial (reuses Phase 5.4 validate-access logic). Creates thread + `to`/`cc` participants + first message + optional attachment, all in one transaction. |
| `GET /{thread_id}/messages` | JWT + caller-must-be-participant | Returns full message list with sender + attachments populated. |
| `POST /{thread_id}/messages` (reply) | JWT + caller-must-be-participant | Inserts a `thread_messages` row, optionally with attachment. Bumps `chat_threads.updated_at`. |
| `POST /{thread_id}/read` | JWT + caller-must-be-participant | Sets `participants.last_read_message_id` to the latest message id. |
| Validate-access (separate file) | JWT + trial-access | See 5.4. |

Estimated effort: **~1.5 days**.

### 5.4 New endpoint — validate-access

Goes in `app/api/routes/api/trial_validation.py` (or extends an existing
file). Path: `POST /api/client/{orgId}/trials/{trial_id}/validate-access`.

Body: `{ user_ids: [uuid] }`. Logic:
1. `get_trial_with_access(trial_id, member, db)` — caller must have
   access themselves
2. For each `user_id` in input, check whether they're a `TrialMember`
   for that trial (or organization admin)
3. Split into `valid_users` and `invalid_users` (with email/full_name
   for the FE to show "X cannot be added because not on this trial")

Effort: **~0.5 day**.

### 5.5 URL path scoping decision

The FE calls `/api/client/{orgId}/threads`. Existing BE routes don't
include orgId in the path — they derive it from the JWT-resolved
`Member`. Two options:

| Option | Trade-off |
|---|---|
| **(a) Match the FE path: include `{orgId}`** | Validate `orgId` matches `member.organization_id`; mismatch → 403. Zero FE change. Slightly redundant with JWT resolution. **Recommended.** |
| **(b) Drop `{orgId}` from the FE path** | One-line FE refactor; matches existing BE convention. Slightly more work, deferred risk if other FE code uses the same convention. |

Recommend **(a)** — easier to ship and keeps the FE untouched.

### 5.6 Wire the router

`app/main.py`:

```python
from app.api.routes.api.threads import router as threads_router
# … existing imports …

app.include_router(
    threads_router,
    prefix="/api/client/{org_id}/threads",
    tags=["threads"],
)

app.include_router(
    trial_validation_router,
    prefix="/api/client/{org_id}/trials/{trial_id}",
    tags=["trial-validation"],
)
```

The `org_id` path param needs to be threaded through the handlers (or
ignored after a guard check). Effort: **~30 min**.

---

## 6. Frontend changes (limited)

The FE is already built; only two small changes needed:

| File | Change | Effort |
|---|---|---|
| `hooks/client/useThreads.ts` | Add `onError` toast/error UI to `useCreateThread`, `useReplyToThread`, `useMarkThreadRead` mutations. Currently silent — that's the reason the user saw nothing happen. | ~20 min |
| `services/client/threads.ts` | None expected if BE adopts option 5.5(a). Verify after BE is up. | 0 |

If we discover other FE/BE shape drift during integration, fix as
discovered (small fixups expected).

---

## 7. Email notification (optional add-on phase)

This is a separate phase — can ship the in-app messaging first and add
email later without rework.

### 7.1 Method on `EmailService`

`app/services/email_service.py`:

```python
async def send_thread_notification(
    self,
    recipient_email: str,
    recipient_name: str,
    sender_name: str,
    subject: str,
    snippet: str,                 # first ~150 chars of the message
    deep_link_url: str,           # FRONTEND_URL/messages/{thread_id}
) -> bool:
    """Send a 'you have a new message' email."""
```

Uses SendGrid (already wired). Same templating approach as
`send_invitation_email`.

### 7.2 Trigger points

In the `create_thread` and `reply_to_thread` route handlers, after the
DB transaction commits successfully:

```python
for participant in to_users + cc_users:
    if participant.user_id == member.profile_id:
        continue  # don't email the sender
    background_tasks.add_task(
        email_service.send_thread_notification,
        recipient_email=participant.email,
        ...
    )
```

Use FastAPI's `BackgroundTasks` so the API response returns
immediately; email is best-effort (one failure shouldn't 500 the
whole send).

### 7.3 Idempotency / spam control

- Mark `participants.notified_at` so we don't email someone twice for
  the same message (e.g., on retry/replay).
- Per-user throttle: if a user receives N replies in a thread within
  Y minutes, batch into a digest email instead. Out of scope for this
  build but worth a follow-up ticket.

### 7.4 HTML template

A simple template at `app/services/email_templates/thread_notification.html`
matching the existing invitation template's style. Renders:
- Sender name + subject line
- First 150 chars of the message
- Deep-link button to `FRONTEND_URL/messages/{thread_id}`
- Footer with "you can manage notifications in settings" (future)

Estimated effort for full email phase: **~1 day**.

---

## 8. Critical files (reference)

| Domain | Files |
|---|---|
| BE models to edit | `app/models/chat_threads.py`, `app/models/thread_participants.py` |
| BE models to create | `app/models/thread_messages.py`, `app/models/message_attachment.py` |
| BE contracts | `app/contracts/thread_chat.py` (rewrite), `app/contracts/validate_access.py` (new) |
| BE routes | `app/api/routes/api/threads.py` (rewrite), `app/api/routes/api/trial_validation.py` (new) |
| BE wiring | `app/main.py` (register routers, self-heal block) |
| BE migration | `migrations/add_messages_feature.sql` (new) |
| BE email (Phase 7) | `app/services/email_service.py`, `app/services/email_templates/thread_notification.html` |
| FE — surface errors | `hooks/client/useThreads.ts` |

---

## 9. Reused, not reinvented

- `get_current_member` for JWT auth — `app/dependencies/auth.py`
- `get_trial_with_access` for trial-level authorisation —
  `app/dependencies/trial_access.py`
- Self-heal column-add pattern — `app/main.py:79+` (model after
  the `chat_sessions.document_id` block)
- `email_service.send_invitation_email` as the structural model for
  `send_thread_notification` — same SendGrid client, same template
  approach
- `BackgroundTasks` for fire-and-forget email — same pattern as the
  ingestion job queue in `app/api/routes/upload.py`
- React Query mutation invalidation pattern — copy from
  `useTrialDocuments.uploadMutation.onSuccess`

---

## 10. Verification

After implementation:

1. **Schema applied** — `\d+ chat_threads thread_participants
   thread_messages message_attachments` in DBeaver shows the expected
   shape.
2. **Smoke test in browser:**
   - User A composes a message to User B in trial X
   - Network tab shows `POST /api/client/{org}/threads` returns 201
   - User A sees the new thread in Sent (and Inbox if they're also a
     participant)
   - User B logs in → sees the thread in Inbox; Inbox count reflects
     unread
   - User B opens the thread → Inbox unread count drops
   - User B replies → User A sees the reply in their Inbox
3. **Authorisation** — User C (different org) tries to call
   `GET /api/client/{org}/threads/{thread_id}/messages` for a thread
   they're not a participant in → 404 (don't leak existence)
4. **Validate-access** — try creating a thread with a recipient who's
   not a TrialMember → response includes them in `invalid_users`; FE
   blocks send.
5. **Email (Phase 7 only):** verify SendGrid Activity dashboard shows
   the notification email; check sender deep-link works after click.
6. **Schema-drift safety net** — restart Cloud Run after deploy with
   the self-heal block; new tables/columns appear without manual SQL.

---

## 11. Effort summary

| Item | Effort |
|---|---|
| Models (existing fix + 2 new) | 0.5 d |
| Contracts | 0.5 d |
| Routes (5 handlers in `threads.py` + validate-access) | 2 d |
| Migration SQL + self-heal | 0.5 d |
| FE error-handling polish | <0.5 d |
| Integration testing + bugfix | 1 d |
| **In-app phase total** | **~4–5 days** |
| Email notification add-on (Phase 7) | +1 d |
| **With email** | **~5–6 days** |

Single engineer. Could compress with a second engineer pairing on the
route handlers.

---

## 12. Open decisions for review

Before the plan is approved for implementation, confirm:

1. **URL path scoping** — match the FE's `{orgId}` in the path
   (recommended) or refactor the FE to drop it?
2. **Title vs Subject rename** — single-step rename (recommended,
   table is empty) or zero-downtime add-then-drop?
3. **Attachment storage** — separate `message_attachments` table
   (recommended) or JSONB column on `thread_messages`?
4. **Email add-on** — ship in this phase, or defer to a follow-up?
5. **CC visibility** — should `to` recipients see who's on `cc`, or
   should `cc` be hidden from recipients (BCC-style)? Affects the
   `participants` payload returned to non-creators.
6. **Cross-trial messaging** — `CreateThreadInput.trial_id` is
   optional, suggesting threads can exist without a trial context. Is
   this intended? It changes the auth model
   (`get_trial_with_access` won't apply when `trial_id` is null).

---

## 13. Out of scope (explicit)

- Real-time push (no WebSockets / SSE — relies on FE polling /
  invalidation)
- Search across threads
- Editing or deleting individual messages (soft-delete column exists
  but no edit endpoints)
- File-upload attachments (only task and response-snapshot
  attachments per the FE contract)
- Threading at the message level (`parent_message_id` is in the
  schema for future use; the FE doesn't render reply-trees yet)
- Notifications panel / bell icon
- Rate limiting on send (could be abused for internal spam — defer)
- Mobile-app push notifications

---

## 14. Recommended next step

1. Tech lead reviews this plan, answers the six open decisions in §12.
2. Greenlit plan moves to implementation. Branch off
   `feat/messages-feature`.
3. Stand up a draft PR after Phase 5.1 (models) so the schema can be
   reviewed before the route work piles up.
4. Plan to ship behind a feature flag (`MESSAGES_ENABLED=true` env
   var) so the FE sidebar entry can be toggled live without
   redeploying — useful if a customer demo lands during the build.
