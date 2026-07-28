# TODOS

Deferred work, with enough context that someone picking it up in three months knows why
it was deferred and where to start.

---

## Backfill photos for imported `.txt` history

**What:** Recover photo files for messages that came from a WhatsApp export, by matching
them against GOWA's `chatstorage.db`.

**Why:** A household seeded from an export shows "photos were shared here; WhatsApp
exports do not include the files" and nothing else, permanently. If a family imported six
months of history containing the photo of the wound that started everything, it is
unreachable.

**Pros:** Turns a permanent hole in the record into a one-time recovery job. The families
with imported history are the ones with the most history worth having.

**Cons:** No shared identifier exists. An export line carries (timestamp to the minute,
sender name, sometimes a filename); a GOWA row carries a provider message id. Matching is
fuzzy on all three, and a wrong match attaches the wrong photograph to a medical entry —
the worst failure this product has. GOWA retention means most old media is gone anyway;
`WHATSAPP_AUTO_DOWNLOAD_MEDIA=false` (`infra/provision.py:209`) exists specifically to
keep that volume small, so the source data is thin by design.

**Context:** `ingest/whatsapp_txt.py:266` — "Never the file itself — v1 stores no bytes."
Placeholders parse to `message_type='image'` with `provider_message_id IS NULL`, which
`media._skip_reason` refuses as `no_downloadable_media`.

**Where to start:** measure before building. Take one imported household and count what
fraction of its image placeholders have a plausible GOWA match at all. If it is low, the
mis-match risk is not worth it and this should be closed rather than attempted.

**Depends on:** the photos-on-entries PR landing — its `photos_unavailable` count is what
tells you how big the problem is.

---

## Documents and PDFs on feed entries

**What:** Attach documents — discharge letters, care plans, prescriptions — to entries the
way images now attach.

**Why:** The derivation pipeline already downloads, stores and reads documents.
`DERIVABLE_MESSAGE_TYPES` is images ∪ documents (`media.py:136`), `READABLE_DOCUMENT_TYPES`
exists, and `served_as` already hands a PDF back as an attachment. The photos PR touches
only images, so all of that stays invisible to families.

**Pros:** A discharge letter is arguably worth more in a care record than any photo. The
backend is built and tested. The remaining work is a UI affordance plus one predicate.

**Cons:** A document is not a thumbnail. It needs filename, type, size and a download
affordance, and the lightbox does not apply — `served_as` deliberately returns
`attachment` for PDFs so browsers never render them inline. That makes it a second
component, not a widened predicate. Downloading also writes the file to the user's disk,
which is what `private, no-store` protects against for photos; that tradeoff needs its own
decision.

**Context:** `media.py:134-136` and `media.py:844-847` split images and documents cleanly
already. The natural shape is a document row *under* the photo grid, not inside it.

**Depends on:** the photos-on-entries PR — shares the window rule, the marker filter and
the one-photo-one-entry dedup rule.

---

## One Penny: unify assistant.py and interview.py into a single responder

**What:** Merge the two conversational LLM modules — `assistant.py` (mention replies) and
`interview.py` (onboarding capture) — into one responder module with one prompt: one Penny
for all group speech, whatever the interview state.

**Why:** The mention-routing PR (branch `fix-onboarding-mention-bug`) made the interview
call answer questions during onboarding and extracted a shared core, but left two prompts
and two modules. Two voices drift; the next conversational feature would add a third.

**Pros:** Single prompt to eval, no drift between Penny's onboarding voice and her
steady-state voice, deletes roughly two files of parallel machinery.

**Cons:** The assistant's safety machinery — `is_emergency` short-circuit, server-authored
disclaimer lines keyed off `ReplyKind`, URL stripping — has to be rewritten inside the
merged module, and both eval suites need rebuilding before it ships. It is its own careful
PR, not a rider.

**Context:** the eng review of 2026-07-28 found six verified duplications between the two
files (`_load_prompt` assistant.py:165/interview.py:92, `_recent_messages` :421/:284,
`_truncate` :516/:306, the 700-char reply cap and finish logic, the gateway wiring with the
private `_HouseholdBudget` import, and the `cast(Purpose, ...)` workaround), plus one
safety asymmetry: `is_emergency` ran only on the assistant path. The shared-core extraction
in that PR is the first half of this work; this TODO is the second half.

**Where to start:** the shared module that PR created — everything still imported by both
`assistant.py` and `interview.py` after it lands is the remaining merge surface.

**Depends on:** `fix-onboarding-mention-bug` landing, and `scripts/eval_interview.py`
existing as the safety net for prompt behavior changes.

---

## Answer @-mentions in photo captions

**What:** Reply when a family sends a photo whose caption @-mentions Penny ("@Penny is
this rash worse?"). Today that message earns silence.

**Why:** Captions are deliberately dropped at ingest (`webhooks.py _classify` — a caption
without its picture reads as a non-sequitur and would pre-empt the photo description), so
the mention in the caption never reaches the reply path as text. Families will
caption-mention photos of symptoms, and silence there reads as broken.

**Pros:** Closes the last "Penny ignored me" gap in mention coverage; the media
description pipeline already produces the text a reply would need.

**Cons:** The reply cannot be useful until the photo's model description has landed, so
this needs an answer-after-derivation flow — new sequencing, not a routing tweak. A
payload-only mention (photo, no usable text) can also reach the reply scheduler with empty
text today; the mention-routing PR pins that to silence with a test, which this work would
deliberately unpin.

**Context:** eng review of 2026-07-28. The mention may survive in the stored payload
(`mentioned_jid`-style fields, `mentions.py:_payload_mentions`) even though the caption
text is dropped, so detection is already possible; only the reply timing is unsolved.

**Where to start:** `derive_in_background` (webhooks.py) — it already knows the moment the
description lands; a mention check there is the natural trigger.

**Depends on:** `fix-onboarding-mention-bug` landing (it defines the routing this would
extend).

---

## E2E test framework

**What:** Stand up Playwright so browser-level user-flow tests exist at all.

**Why:** `frontend/` has two test files (`entry-photo.test.tsx`, `kind-node.test.tsx`), no
`e2e/` directory, and `package.json:11` is `"test": "vitest run"`. The backend compensates
well — 44 test files, `TestClient` integration tests carry tenancy — so the gap is
specifically browser behaviour: responsive layout, focus traps, the 60-second poll,
keyboard interaction.

**Pros:** The photo lightbox is exactly what unit tests verify badly — focus management,
escape handling, scroll lock. A responsive photo grid at 375px vs 1440px is not
meaningfully testable in vitest.

**Cons:** Real infrastructure — a browser in CI, fixtures, a seeded household, flake
management. It is a project, not a task.

**Context:** the specific risk (photos vanishing at a breakpoint) already bit once: the
photo block used to be `hidden sm:block`, which deleted the best evidence in the record on
phones. That is fixed and the lesson is in the comment at `timeline-entry.tsx:253`.

**Where to start:** the four paths that want it, in priority order — lightbox focus trap,
photo grid at 375/768/1024/1440, the 60s poll not re-downloading images, and signed-out
degradation.

**Depends on:** nothing.
