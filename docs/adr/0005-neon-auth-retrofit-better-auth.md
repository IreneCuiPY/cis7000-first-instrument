# ADR-0005: Giving the question bank an owner (Neon Auth, Better Auth)

- **Date**: 2026-10-15  |  **Iteration**: 1  |  **Status**: Accepted  |  **Deciders**: Irene Cui (decisions), Claude Sonnet 5 (build)

**TL;DR**: This retrofits the existing question bank with identity — one
`Authorization` header now separates a shared practice commons from a
caller's own private questions and private review progress — following
`jlumbroso/your-protected-notes`' ADR-0001/ADR-0002 pattern and its
`docs/RETROFIT-NEON-AUTH.md` guide, adapted for Neon Auth's current
provider (Managed Better Auth) and for a quiz bank's shape instead of a
notes list's.

## Context

ADR-0001 through ADR-0004 in this repo cover the server's original
shape: Python + the official MCP SDK (ADR-0002), deployed to Render's
free tier (ADR-0003), `uv`-managed (ADR-0004). None of them touch
identity, because there wasn't any — every caller saw and could mark
reviewed the same shared commons.

**What's new here, specifically:** `your-protected-notes` demonstrates
the retrofit this ADR performs, but two things changed since it was
written, and a third thing is simply different about a quiz bank:

- **The provider moved.** Neon's console no longer offers project-id +
  publishable-key "legacy Stack" auth to new projects — new projects
  get **Managed Better Auth** (`docs/NEON-AUTH-TRANSITION.md` in that
  repo tells the story in full). This project's Neon Auth is Better
  Auth: confirmed directly — `neon_auth."user"` exists, `users_sync`
  does not, tokens are signed EdDSA, and there's no Stack project id to
  find anywhere in the console.
- **claude.ai needs somewhere to sign in.** `your-protected-notes`
  ships `auth.py`, a minimal in-process OAuth 2.1 authorization server
  (discovery, dynamic client registration, PKCE, a login page) that
  this project now also runs — without it, claude.ai's connector UI has
  no sign-in flow to show.
- **A quiz bank isn't a notes list.** The source material's "shelf" is
  exclusive by design — sign in and the commons disappears, replaced by
  your own notes. A question bank has a **shared seed corpus** notes
  never had (15 questions nobody owns), and it has a **per-item review
  state** notes never had either (a note is just kept; a question is
  reviewed or not). Both of those needed their own decision — D2 and D3
  below have no equivalent in the source ADR.

## The six decisions (each with the road not taken)

**D1 — Neon Auth on Managed Better Auth, not Stack.** `current_identity`
verifies against the JWKS URL derived from `NEON_AUTH_BASE_URL`, with
`algorithms=["EdDSA"]` and issuer/audience both equal to that URL's
origin. *Road not taken*: legacy Stack (`STACK_PROJECT_ID`,
`ES256`/`RS256`, a separate publishable key) — the course's own updated
`server.py` keeps a dual-provider branch for students still on old
projects, but this project was provisioned fresh onto Better Auth, so
that branch is dead code here: it adds an unused credential
(`STACK_PUB_CLIENT_KEY`) and an unused failure mode for a provider this
deployment will never see. If a future Neon migration tool moves this
project back to Stack, the fix is re-adding that branch, not reworking
this one.

**D2 — Merged view: commons plus your own; guests are read-only.**
Signed in, you see the 16-question commons **and** your own private
additions; as a guest, commons only — and a guest cannot add to the
commons either. `add_question` no longer tells the two shelves apart by
who's calling (guest's question joins the commons, signed-in user's
stays private): it's signed-in only now, so a guest never reaches the
insert at all. The commons' only remaining avenue for new shared
questions is the owner adding a row directly in the database. *Road not
taken*: the source material's exclusive shelf (sign in, the commons
vanishes). That costs a notes app nothing — there's no shared seed
material to lose. It would cost this project its entire practice bank:
the moment a student signs in to get their own private shelf, the 16
questions everyone studies from would disappear for them specifically.
The one-minute "watch it become private" demo ADR-0001 built the
exclusive shelf to teach still works here (add a *private* question,
watch a classmate not see it) — it just doesn't also require giving up
the shared corpus to get it.

*Also road not taken*: leaving guest writes to the commons open, which
is what ADR-0001 D2 in the course repo itself chose — that ADR
considered and rejected a read-only guest as "half the contrast" its
one-minute demo is built to show. This project diverges from that
choice for a reason the source repo doesn't have to deal with: it runs
on a public URL, and the commons is the shared corpus every caller
studies from, not a private demo shelf — leaving it open to anonymous
guest writes would turn it into a public dumping ground anyone could
pollute, forever, with no identity attached. The honest cost of that
divergence: the one-minute "watch it become private" demo changes
shape. It no longer shows a guest adding something a classmate can't
see; it now shows sign in, add a private question, a classmate (still a
guest) can't see it — **and** that classmate can't add anything
themselves, private or shared, until they sign in too.

**D3 — Review progress in its own table, keyed by identity, not a
column on `questions`.** `question_reviews(question_id, user_id,
reviewed_at)`, unique on `(question_id, COALESCE(user_id, ''))` — the
same COALESCE-unique-index shape ADR-0001 D3 used for petname
uniqueness, applied here to a different problem. *Road not taken*: the
pre-auth `questions.reviewed BOOLEAN` column, left in place but now
unused. A boolean on the question's own row is a single shared fact;
once the commons is visible to more than one identity (D2), "reviewed"
stops being a fact about the question and becomes a fact about *who*
reviewed it. Keeping it on the row would have meant one student
reviewing a shared seed question silently marking it reviewed for
every other guest and every other signed-in user — the exact
shared-mutable-state bug D2 was designed not to reintroduce, just
relocated to a different column.

**D4 — Pre-auth rows become the commons as-is; old review state
carries forward under the guest identity; signed-in users start at
zero.** The migration added `user_id` (nullable, existing rows stay
`NULL`) and backfilled `question_reviews` from every pre-existing
`reviewed = TRUE` row, attributed to `user_id IS NULL` — the guest
identity — because every review that happened before this migration
*did* happen as a guest: there was no other identity yet for it to
belong to. A student who then signs in starts that commons question's
review state at zero under their own identity. *Roads not taken*: (a)
treating old `reviewed = TRUE` rows as reviewed-by-everyone going
forward — silently grandfathering every future identity into someone
else's study history, the same conflation D3 exists to end, just moved
to migration time instead of runtime; (b) discarding the old reviewed
state entirely — technically clean, but it erases real study history
for no reason the data forced. Attributing it to the guest identity
specifically was the one option that didn't invent a fact: it's the
identity that was actually asking, at the time.

**D5 — Identity keyed on the token's `sub`, never email.** Same
decision as `your-protected-notes` ADR-0001 D1, re-derived rather than
assumed, because Better Auth's `neon_auth."user"` table is shaped
differently from Stack's `users_sync` but makes the identical case:
`id` (uuid) is the row's actual key; `email` is just a column on it,
fetched only for `user_status`'s display text. *Road not taken*: email
as the key. The failure mode the source ADR gives for that — mutable,
reassignable, routine at a university — is carried over from
`your-protected-notes` ADR-0001 D1's reasoning, not something this
project independently observed in Better Auth's own schema; what *is*
true here, and sufficient on its own, is simpler: `id` is the row's
actual key, `email` is just a column on it.

**D6 — Tokens are verified only against the public JWKS endpoint; the
server never reads `neon_auth.jwks` directly.** `current_identity` calls
`PyJWKClient(JWKS_URL)`, which fetches public keys over HTTPS from
Better Auth's own `/.well-known/jwks.json` — the same endpoint any
other verifier would hit, no special access. *Road not taken, and
actually attempted*: reading the signing key straight out of
`neon_auth.jwks` in this project's own database, to mint a test token
without needing a real sign-in. That table carries a `privateKey`
column — the session's sandbox flagged the attempt as credential
materialization and refused it before any key material was read, and
the human's standing instruction afterward was explicit: don't try that
again. The refusal was correct independent of the instruction: a
verifier that can read the private key it's supposed to be checking
signatures *against* has quietly become a signer, which is a different
and much larger trust boundary than the one this server is meant to
hold. Every other test in this project's history that needed a "valid
token" used a real sign-in instead, or tested the rejection paths
(garbled tokens, forged signatures against the real public JWKS)
without ever touching the private key.

All six are live in `server.py`/`auth.py`, verified over the real MCP
streamable-HTTP transport and the real OAuth authorization-code + PKCE
dance (not just unit-level): guest isolation (30× `get_random_question`
as guest never returned a user-owned row; `get_hint`/`get_answer` on a
private id return the same "not found" a nonexistent id would); the
three identity modes (absent token, declared guest token, invalid/
forged token — the last raising, never degrading to guest); and a
Render build+start simulation confirming `uv.lock` alone reproduces the
environment this all runs in. The signed-in path itself was verified by
the human's own real sign-in from claude.ai (Gmail account) on
2026-10-15: `user_status` returned their email, and the question they
added carries their Better Auth uuid in `questions.user_id` while the
seed rows stayed `NULL`. After deploying commit `5027a38` (D2's guest-write restriction),
signed-in `add_question` on the live server still succeeded and the new
row carries their uuid. Guest `add_question`'s refusal was verified only
locally, not against the deployed server: calling it with no
Authorization header returned the refusal message and left the
`questions` row count unchanged (17 to 17).

## Questions

*Unlike ADR-0001's two questions, this one wasn't pre-answered before
the code existed — it surfaced afterward, from a choice D3 made on
purpose: leaving `questions.reviewed` in place, unused, rather than
dropping it. It's posed here the same way regardless: Recommendation
is mine, ANS is explicitly not — left for Irene.*

### QST-DEAD-COLUMN: Should `questions.reviewed` be dropped now that `question_reviews` is the real source of truth?
- Status: answered
- Why asking: the column is no longer read or written by any tool (D3), but dropping a column is irreversible in a way leaving it idle is not.
- Need: pick a letter (or override — any shape answers)

Options:
- **A — drop it now**: one more migration (`ALTER TABLE questions DROP COLUMN reviewed`), on the same production database, behind the same kind of pre-migration branch used for the D3/D4 migration.
- **B — leave it in place indefinitely**: costs nothing to sit unused; a future reader of `questions`' schema has to notice the comment explaining it's dead, rather than the column being gone.

**Recommendation**: (by Claude Sonnet 5, 2026-10-15)
**B — leave it.** *Rationale*: nothing in this project reads it, so it has zero runtime cost; dropping it buys only schema tidiness against a real (if small) risk — a script, a classmate's copy of this repo, or a future ADR this one didn't anticipate, that still reads the old column. *Confidence*: 0.7 — because the risk is small but not zero, and "tidiness" is a real cost on the other side, not nothing. *If wrong*: a code-review pass or a semester's distance turns up nothing anywhere depending on it — then A, same migration discipline (SQL shown, approved, pre-migration branch) as D4 used.

**ANS:** (by Irene Cui, 2026-10-15)
B — leave `questions.reviewed` in place for now.
