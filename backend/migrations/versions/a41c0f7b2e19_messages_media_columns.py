"""messages: the three columns that say we kept the file

A photo of a rash and a discharge letter used to reach the record as an empty row — no text, no
file, nothing for the feed to show and nothing for the prompt to read. `app.media` now fetches
the bytes once, writes them to the volume at `<MEDIA_ROOT>/<household_id>/<message_id>`, and
derives text from them. These columns are what the rest of the app reads back.

WHY COLUMNS AND NOT A `payload` KEY. `payload` is the provider's blob and already carries our
two underscore-prefixed additions, so a `_media` block there would not have been out of place.
It is a column set instead because ONE of these is read by `GET /api/media/{message_id}` — the
route that hands a photograph of an identifiable person to a browser — and that query has to be
a plain column comparison scoped by `household_id`, not a JSONB path expression. The most
consequential tenant read in the product is the last place to be clever.

WHY THERE IS NO PATH COLUMN. The file's location is DERIVED from `(household_id, message_id)`
at read time and never stored. A stored path is a string, and a string is something a bug or a
hostile payload could point at another household's directory; a path recomputed from the
session's own household id cannot address anything the caller does not own. Nothing is lost:
the layout is one function in `app.media`, and a household's whole media directory is
`rm -rf`-able by tenant id, which is what makes deletion actually delete.

All three are NULLable and default NULL, so this is a catalogue-only `ADD COLUMN` on Postgres
11+: a brief ACCESS EXCLUSIVE lock and no table rewrite, whatever the message count. Every row
written before this deploy correctly says "no file kept", because none was.

Revision ID: a41c0f7b2e19
Revises: 12797385345a
Create Date: 2026-07-26 10:12:41.882714

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a41c0f7b2e19"
down_revision: str | Sequence[str] | None = "b7e4d19c2af6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The provider's content type, verbatim. NOT NULL on this column is the single fact
    # "there are bytes on the volume for this message"; the serving route reads it and
    # `app.media.served_as` decides what a browser is allowed to be told it is.
    op.add_column("messages", sa.Column("media_content_type", sa.Text(), nullable=True))
    # What we wrote, so a truncated or half-written file is detectable without opening it.
    op.add_column("messages", sa.Column("media_byte_count", sa.Integer(), nullable=True))
    op.add_column(
        "messages",
        sa.Column("media_stored_at", sa.DateTime(timezone=True), nullable=True),
    )
    # No index, deliberately. The only lookup is by primary key with a household predicate,
    # which `pk_messages` already answers; an index here would be a row per message for a
    # query nobody makes.


def downgrade() -> None:
    # The FILES ARE NOT TOUCHED by this, and that asymmetry is on purpose: dropping the columns
    # loses our record that they exist, and deleting a family's photographs because a migration
    # went backwards is not a trade anyone would choose. Re-upgrading leaves the columns NULL
    # against files still on the volume — they are then unreachable (the route reads the column)
    # and are swept the next time a household is deleted. Clean them up by hand if a downgrade
    # is ever permanent: `rm -rf $MEDIA_ROOT`.
    op.drop_column("messages", "media_stored_at")
    op.drop_column("messages", "media_byte_count")
    op.drop_column("messages", "media_content_type")
