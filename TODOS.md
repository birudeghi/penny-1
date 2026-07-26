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
