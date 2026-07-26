"""Which photographs belong to which entry. ONE module, because there are two callers.

`GET /api/feed` needs a capped set per entry for a whole page. `GET /api/events/{id}/photos`
needs the uncapped set for one entry so the lightbox can page past the cap. Those are the same
question asked at two sizes, and the rule that answers it — what counts as a photograph, how far
from the entry it may have been sent, which entry gets it when several could — is exactly the
kind of thing that rots when it exists twice. It exists here.

THE RULE, IN ORDER.

1. **A photograph is an image message whose bytes we kept.** `message_type` in
   `media.IMAGE_MESSAGE_TYPES` and `media_content_type IS NOT NULL`. That second column is the
   whole of "there is a file on the volume for this message"; a `.txt` import's `<image omitted>`
   placeholder has neither bytes nor a `provider_message_id` to fetch them by, so it can never
   satisfy it. Those placeholders are counted separately by `unavailable_counts` rather than
   silently dropped, because an unexplained gap in a medical record reads as "nothing happened".

2. **A CITED photograph always belongs to its entry, whatever the clock says.** `cited` means
   the message id is in `events.source_message_ids` — the model read it and attached it. No time
   heuristic may overrule that, and this is not a hypothetical: `extraction.persist` UNIONS
   `source_message_ids` on every re-extraction (`_union`, persist.py:218), so an entry discussed
   on Monday and again on Friday has a source span of a week. A window trimmed to the recent end
   would throw away the photograph cited on Monday — the one the model was most sure about, on
   exactly the long-running entries a care record cares about most.

3. **An UNCITED photograph must be near the conversation AND judged relevant.** Near is
   `PHOTO_WINDOW` either side of the entry's MOST RECENT source message.

   Anchoring on the most recent one, rather than spanning the whole source range, is what makes
   the rule survive both shapes an entry comes in. A long-running entry has a source span of a
   week (rule 2 explains why) and spanning it would drag in a week of unrelated pictures; a
   family sends the photograph about an appointment near the confirmation, not near the first
   mention. And an entry drawn from ONE message has a span of zero — the first version of this
   query used `[MIN(sent_at), MAX(sent_at)]` and that collapses to a single instant, so no
   uncited photograph could ever fall inside it. That was the common case, and it was silently
   empty. It took a real database to see; the shape is invisible in review.

   The window reaches FORWARD as well as back because a photograph often follows the sentence
   that prompted it — "her ankle looks worse" and then, a minute later, the picture.

   Relevant is `payload ? 'derived_from_media'`, which is not a heuristic — it is Penny's own
   answer. `media._derive` is told to reply with an empty string for "a meme, a screenshot, a
   photo of a dog" (media.py:204) and writes NO text and NO marker when it does (media.py:761).
   So the marker is a paid-for judgement that this picture is about this person's care, and
   without it a holiday snap would land on a symptom card looking like proof.

   The honest cost: a photograph Penny could not read — over budget, an unreadable type, too
   large — has no marker either, and so never appears as an extra. It still appears if cited.

4. **One photograph belongs to exactly one entry.** A busy day produces several entries from one
   conversation whose windows overlap almost completely, and without this the same picture renders
   five times down the feed. The winner is the entry that cites it; failing that the entry nearest
   in time; failing that the lowest id. That last tiebreak is not decoration — the feed refetches
   every sixty seconds, and a photograph that hops between two entries on alternate polls is worse
   than a duplicate. It has to be stable, not merely deterministic.

WHY THE CAP IS A WINDOW FUNCTION AND NOT `LIMIT`. The feed needs two numbers per entry: the
photographs to show, and how many more there are, so the card can say "+3 more" instead of
silently dropping evidence. A `LATERAL … LIMIT 4` gives the first and destroys the second.
`row_number()` and `count()` over the same partition give both, and the outer `WHERE rn <= :cap`
still bounds what crosses the wire at `cap * n_events` — which was the point of the limit.

WHERE THE TWO CALLERS DELIBERATELY DISAGREE. Rule 4's tiebreak is computed over the events the
caller asked about. For the feed that is the page; for one entry's lightbox it is that entry
alone. So the lightbox can show an uncited photograph that the feed handed to a neighbouring
entry. That asymmetry is deliberate and it errs in the safe direction: somebody who opened THIS
entry's photographs sees every picture that was genuinely in this entry's window, and the card's
"+N more" can only ever under-promise. A cited photograph cannot drift this way — rule 2 makes
its owner a property of the photograph, not of the query.

TENANCY IS AN EXPLICIT PREDICATE IN EVERY HALF OF EVERY QUERY BELOW, never inherited from the
join to `events`. `household_id` filtering is the entire tenancy mechanism in this app and there
is no RLS behind it; this module hands back message ids that become `GET /api/media/{id}` URLs,
so a query that forgets is a query that shows one family another family's photographs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import sqlalchemy as sa

from app.media import IMAGE_MESSAGE_TYPES
from app.schemas import FEED_PHOTOS_PER_EVENT, PHOTO_WINDOW, PhotoSet

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

# `message_type` is stored lowercase by the adapters; sorted so the bound parameter is stable
# between processes and a query plan can be cached.
_IMAGE_TYPES = sorted(IMAGE_MESSAGE_TYPES)

# The candidate set: every photograph any of these entries could claim, with `cited` computed
# per entry rather than from a page-wide list of ids. Computing it page-wide was a real bug in
# review — it marks a photograph `cited` on entry A because entry B cited it, and then orders
# A's cap on that false premise.
_CANDIDATES = """
WITH ev AS (
    SELECT e.id AS event_id, e.occurred_at, e.source_message_ids
    FROM events e
    WHERE e.household_id = :household_id
      AND e.id IN :event_ids
      AND e.deleted_at IS NULL
),
win AS (
    SELECT ev.event_id,
           MAX(m.sent_at) + :window AS hi,
           MAX(m.sent_at) - :window AS lo
    FROM ev
    JOIN messages m
      ON m.id = ANY(ev.source_message_ids)
     AND m.household_id = :household_id
    GROUP BY ev.event_id
),
cand AS (
    SELECT ev.event_id,
           ev.occurred_at,
           p.id AS message_id,
           p.sent_at,
           p.sender_display_name,
           p.text,
           (p.id = ANY(ev.source_message_ids)) AS cited
    FROM ev
    JOIN win ON win.event_id = ev.event_id
    JOIN messages p
      ON p.household_id = :household_id
     AND p.message_type IN :image_types
     AND p.media_content_type IS NOT NULL
     AND (
             p.id = ANY(ev.source_message_ids)
          OR (
                 p.sent_at >= win.lo
             AND p.sent_at <= win.hi
             AND jsonb_exists(p.payload, 'derived_from_media')
             )
         )
),
claimed AS (
    SELECT DISTINCT ON (message_id)
           event_id, message_id, sent_at, sender_display_name, text, cited
    FROM cand
    ORDER BY message_id,
             cited DESC,
             abs(EXTRACT(EPOCH FROM (occurred_at - sent_at))),
             event_id
)
"""

# Cited first, then most recent. `message_id` is the final tiebreak so two photographs sharing a
# timestamp to the second cannot swap places between two polls of the same feed.
_ORDER = "cited DESC, sent_at DESC, message_id"


async def photos_for_page(
    session: AsyncSession,
    household_id: UUID,
    event_ids: Sequence[UUID],
) -> dict[UUID, PhotoSet]:
    """Capped photographs plus both counts, for every entry on one feed page. ONE statement.

    Returns a mapping keyed by event id. Entries with nothing to show are absent rather than
    present-and-empty; `to_event` renders a missing key exactly as it renders an empty one.
    """
    if not event_ids:
        return {}

    rows = (await session.execute(_candidate_query(household_id, event_ids))).all()
    unavailable = await unavailable_counts(session, household_id, event_ids)

    found: dict[UUID, PhotoSet] = {}
    for row in rows:
        existing = found.get(row.event_id)
        photos = list(existing.photos) if existing else []
        photos.append(_as_photo(row))
        found[row.event_id] = PhotoSet(
            photos=photos,
            # `total` is the same on every row of a partition, so the last one read wins and
            # says the same thing as the first. Withheld is what the cap cut, never negative.
            withheld=max(0, int(row.total) - len(photos)),
            unavailable=unavailable.get(row.event_id, 0),
        )

    # An entry with no photographs but with import placeholders still has something to say.
    for event_id, count in unavailable.items():
        if event_id not in found and count:
            found[event_id] = PhotoSet(photos=[], withheld=0, unavailable=count)
    return found


async def photos_for_event(
    session: AsyncSession,
    household_id: UUID,
    event_id: UUID,
) -> list[dict[str, Any]]:
    """Every photograph for ONE entry, uncapped. What the lightbox pages through.

    Uncapped is safe here in a way it would not be on the feed: this is one entry, fetched
    because somebody clicked a photograph, not 500 entries fetched every sixty seconds by every
    open tab. See the module docstring for why this may return a photograph the feed attributed
    to a neighbouring entry.
    """
    statement = sa.text(f"{_CANDIDATES} SELECT * FROM claimed ORDER BY {_ORDER}").bindparams(
        sa.bindparam("household_id", household_id),
        sa.bindparam("event_ids", [event_id], expanding=True),
        sa.bindparam("window", PHOTO_WINDOW, type_=sa.Interval()),
        sa.bindparam("image_types", _IMAGE_TYPES, expanding=True),
    )
    return [_as_photo(row) for row in (await session.execute(statement)).all()]


async def unavailable_counts(
    session: AsyncSession,
    household_id: UUID,
    event_ids: Sequence[UUID],
) -> dict[UUID, int]:
    """Image messages among these entries' OWN sources whose files we do not hold.

    Scoped to `source_message_ids` and not to the time window, deliberately. We can only honestly
    say "photographs were shared here and the export did not include them" about messages this
    entry was actually drawn from; extending it to everything nearby would turn one family's
    unrelated `<image omitted>` line into a claim about this entry.
    """
    if not event_ids:
        return {}
    statement = sa.text(
        """
        SELECT e.id AS event_id, count(*) AS n
        FROM events e
        JOIN messages m
          ON m.id = ANY(e.source_message_ids)
         AND m.household_id = :household_id
        WHERE e.household_id = :household_id
          AND e.id IN :event_ids
          AND e.deleted_at IS NULL
          AND m.message_type IN :image_types
          AND m.media_content_type IS NULL
        GROUP BY e.id
        """
    ).bindparams(
        sa.bindparam("household_id", household_id),
        sa.bindparam("event_ids", list(event_ids), expanding=True),
        sa.bindparam("image_types", _IMAGE_TYPES, expanding=True),
    )
    return {row.event_id: int(row.n) for row in (await session.execute(statement)).all()}


def _candidate_query(household_id: UUID, event_ids: Sequence[UUID]) -> sa.TextClause:
    """The page query: rank inside each entry, keep the cap, carry the total out with it."""
    return sa.text(
        f"""
        {_CANDIDATES},
        ranked AS (
            SELECT claimed.*,
                   row_number() OVER (PARTITION BY event_id ORDER BY {_ORDER}) AS rn,
                   count(*)     OVER (PARTITION BY event_id) AS total
            FROM claimed
        )
        SELECT event_id, message_id, sent_at, sender_display_name, text, cited, total
        FROM ranked
        WHERE rn <= :cap
        ORDER BY event_id, rn
        """
    ).bindparams(
        sa.bindparam("household_id", household_id),
        sa.bindparam("event_ids", list(event_ids), expanding=True),
        sa.bindparam("window", PHOTO_WINDOW, type_=sa.Interval()),
        sa.bindparam("image_types", _IMAGE_TYPES, expanding=True),
        sa.bindparam("cap", FEED_PHOTOS_PER_EVENT),
    )


def _as_photo(row: Any) -> dict[str, Any]:
    """One selected row -> the `Photo` shape.

    `text` becomes `description` and the rename is the whole point: on a stored photograph that
    column is always Penny's reading, never the family's caption. `schemas.Photo` carries the
    proof of why that is structurally true rather than merely usually true.
    """
    return {
        "message_id": row.message_id,
        "sent_at": row.sent_at,
        "sender": row.sender_display_name,
        "description": row.text,
        "cited": bool(row.cited),
    }
