# Penny

**A care assistant that lives in the family group chat.** Add Penny's WhatsApp number to the
group where a family already coordinates care, and she quietly turns their day-to-day messages
into a shared, structured care record — a timeline of symptoms, medications and appointments
that nobody had to sit down and write.

**Live:** [app.pennyai.chat](https://app.pennyai.chat) — the front door explains itself.

## What it does

Families caring for an elderly parent lose the thread: nobody remembers when the dizziness
started, what the GP changed at the last appointment, or which sibling collected the
prescription. That history exists — it's scattered across months of WhatsApp messages.

Penny reads the group's conversation and extracts it into structure:

- **A care timeline** — "Amlodipine stopped", "Up twice overnight, better than last week" —
  each entry linked to the actual messages (and photos, and voice notes) it came from.
- **Standing facts** — medications, conditions, allergies, baseline mobility — kept current
  as the chat mentions them, confirmed or corrected by the family on the website.
- **Upcoming appointments**, pulled from messages like "cardiology follow-up on the 6th".
- **Periodic care reports** written by an LLM over the accumulated record.

Onboarding is one action with zero setup: add Penny to the group. That provisions the
household, and she replies in the chat with the website link and the family's shared
credentials. She only ever speaks when @-mentioned; the rest of the time she reads silently.

## Who it's for

The 3–8 relatives sharing care of one person — typically adult siblings coordinating around
an ageing parent, plus the aunt who visits on Tuesdays. The buyer is the sibling who has
become the family's unpaid care coordinator; the users are everyone in the group, including
the ones who will never install another app.

## The value

- **Zero behaviour change.** The family keeps talking where they already talk. The record is
  a by-product of messages they were sending anyway — no app to install, no forms to fill.
- **Answers that are otherwise lost.** "When did the swelling start?" is a scroll through the
  timeline, not an argument about memory. Every entry cites its source messages as evidence.
- **A record worth handing over.** GP visits, respite carers, memory-clinic assessments and
  Attendance Allowance forms all ask for exactly the history Penny has been keeping.
- **Trust by construction.** One shared family login, evidence links on every entry, spend
  caps per household, and a bot that is contractually silent unless spoken to.

## How it works

```
family WhatsApp group
        │  messages, photos, voice notes
        ▼
   gowa (WhatsApp bridge, go-whatsapp-web-multidevice)
        │  signed webhooks, private networking
        ▼
   penny (FastAPI + Postgres) ──► OpenAI: extraction, transcription, reports
        │  serves frontend/dist same-origin
        ▼
   React SPA — landing page, timeline, reports, settings
```

- **One origin, two services.** FastAPI serves the built React app alongside `/api/*`, which
  deletes CORS, third-party-cookie and CSRF problems outright. The WhatsApp bridge (`gowa`)
  is a sidecar reached only over private networking; a third `tick` container calls one
  authenticated endpoint hourly for scheduled work.
- **Extraction with guardrails.** Messages are batched to a pinned, dated OpenAI model
  snapshot under strict JSON schemas; every event stores which messages it came from. Voice
  notes are transcribed; photos are filed with the entry they belong to. Per-import and
  per-household monthly spend caps are enforced in code.
- **Onboarding as a webhook.** A group-join event provisions the household, generates
  credentials, and posts the welcome message — with the password warning, the context
  questions that seed extraction quality, and the one rule about when Penny speaks.
- **Reproducible deployment.** `infra/provision.py` builds the entire Railway deployment —
  project, Postgres, services, volume, secrets, domain — idempotently from this repo. An
  OpenAI key and a domain are the only inputs a human brings.

Pre-product, deliberately: a demoable vertical slice for 1–5 test families, optimised for
correctness and auditability over scale.

## Repository layout

```
backend/           FastAPI app (Python 3.12+, uv); pytest with `live` and `db` opt-in markers
frontend/          React + TypeScript SPA (Vite)
infra/             provision.py — the whole deployment as one idempotent script
docs/              architecture, API contract, deployment runbook, GOWA runbook
```

## Run it locally

```sh
cp .env.example .env        # every variable documented inline; OPENAI_API_KEY is the only real key

cd backend && uv sync && uv run uvicorn app.main:app --reload    # http://localhost:8000
cd frontend && npm install && npm run dev                        # http://localhost:5173, proxies /api
```

No backend handy? `VITE_USE_FIXTURES=true npm run dev` serves the whole UI from hand-authored
demo data, clearly bannered as such.

Tests: `cd backend && uv run pytest` (684 tests; `live` and `db` markers skip unless opted in)
and `cd frontend && npx vitest run`. CI runs both plus the production build on every PR.

## Deploying

```sh
export OPENAI_API_KEY=sk-...
infra/provision.py --workspace <workspace-id> --domain penny.example.com --dry-run   # look first
infra/provision.py --workspace <workspace-id> --domain penny.example.com
```

Re-running converges: nothing duplicates, no secret rotates. The full runbook — including the
fourteen platform traps that cost a night — is in [docs/deployment.md](docs/deployment.md).

## Docs

- [docs/architecture.md](docs/architecture.md) — end-to-end system and deployment architecture
- [docs/api-contract.md](docs/api-contract.md) — the HTTP contract the frontend types are generated from
- [docs/deployment.md](docs/deployment.md) — provisioning, verification, rollback, the kill switch
- [docs/railway-deployment.md](docs/railway-deployment.md) — service topology, variables, platform gotchas
- [docs/gowa-runbook.md](docs/gowa-runbook.md) — pairing a WhatsApp account; needs a physical phone
- [infra/variables.md](infra/variables.md) — every variable, classified, and why it is allowed to exist
