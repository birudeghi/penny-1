"""messages: the index that makes "photos near this entry" a range scan

`a41c0f7b2e19` added the three media columns and declined to index them, and said why:

    No index, deliberately. The only lookup is by primary key with a household predicate,
    which `pk_messages` already answers; an index here would be a row per message for a
    query nobody makes.

That was correct when it was written and it is not correct now. `GET /api/feed` attaches
photographs to entries, which asks a question nobody was asking then: *which images with stored
bytes did this household send between these two timestamps?* Nothing on the table answers it.

WHY NONE OF THE THREE EXISTING INDEXES HELP. `uq_messages_household_provider_message_id` is keyed
on the provider id. `uq_messages_household_content_hash` is keyed on the content hash — it can get
you to a household, and then you scan every message that household has ever sent and recheck
`sent_at` and `media_content_type` on each one. `ix_messages_household_sent_at_unextracted` is the
right two columns and the wrong rows: it is partial on `extracted_at IS NULL`, and every photograph
the feed can show has already been extracted, so that index contains none of them. The foreign key
to `households` is not indexed either.

WHY PARTIAL, AND WHY ON THIS PREDICATE. `media_content_type IS NOT NULL` is the whole of "we kept
bytes for this message", and it is true of almost nothing: a family sends thousands of lines of
text and a handful of photographs. So the index holds one entry per stored file rather than one per
message, which is the difference between a rounding error and a second copy of the table. This is
the same shape — and the same argument — as `ix_messages_household_sent_at_unextracted`: index the
small interesting subset and let the rest be a recheck.

`message_type = 'image'` is deliberately NOT in the key. It is a recheck over whatever the range
returns and can never widen the scan, and leaving it out keeps this one index useful when documents
are attached to entries too (see TODOS.md), rather than needing a second one keyed the other way.

CONCURRENTLY IS NOT USED, on purpose. It cannot run inside a transaction, and alembic runs
migrations in one — so using it here means `autocommit_block()` and a migration that cannot be
rolled back cleanly. The table this locks is small enough at present scale that a plain `CREATE
INDEX` is a blip, and the honest tradeoff is written down here rather than discovered during a
deploy. Revisit if a household ever has millions of messages.

Revision ID: c5d8a13f6b20
Revises: a41c0f7b2e19
Create Date: 2026-07-26 09:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5d8a13f6b20"
down_revision: str | Sequence[str] | None = "a41c0f7b2e19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_messages_household_sent_at_with_media",
        "messages",
        ["household_id", "sent_at"],
        unique=False,
        postgresql_where=sa.text("media_content_type IS NOT NULL"),
    )


def downgrade() -> None:
    # Dropping this costs performance and nothing else: the feed query still returns the same
    # rows, it just reads more of the table to find them. No data is touched and no file on the
    # media volume is involved, which is the asymmetry `a41c0f7b2e19`'s downgrade had to argue
    # about and this one does not.
    op.drop_index("ix_messages_household_sent_at_with_media", table_name="messages")
