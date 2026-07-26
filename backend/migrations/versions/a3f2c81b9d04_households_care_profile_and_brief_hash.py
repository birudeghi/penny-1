"""households.care_profile and households.care_brief_hash

The relevance specification the onboarding interview writes, and the hash that decides when a
change to it is worth re-reading the history for.

WHY JSONB AND NOT COLUMNS. The interview keeps learning what to ask. Aliases, conditions,
medications, clinicians and the four vitals are the fields it captures today; the next family
that answers in an unanticipated shape adds another. A column per field means a migration per
question, and the questions are the part of this product still moving.

WHY THE HASH IS A SEPARATE COLUMN. `messages.extracted_at IS NULL` is the extraction cursor,
and a stamped message is never reconsidered. Every message stamped before the interview ran
was filtered by a brief that did not know the care recipient's name. Storing the hash of the
rendered brief is what lets a profile write ask "did the filter actually change?" and clear
the cursor only when the answer is yes — so an idle settings save costs nothing, and a real
correction reaches the six months of history it was meant to fix.

NULLABLE, not NOT NULL DEFAULT ''. NULL means "no brief has ever been rendered for this
household", which is genuinely different from "the brief rendered to the empty string", and
the first extraction run after this deploy needs to tell those apart to decide whether it is
looking at a fresh household or a changed one.

Cheap on any table Penny will ever have: both are non-volatile defaults, so Postgres 11+
stores them in the catalogue instead of rewriting rows.

Revision ID: a3f2c81b9d04
Revises: 12797385345a
Create Date: 2026-07-26 04:12:41.882190

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a3f2c81b9d04"
down_revision: str | Sequence[str] | None = "12797385345a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "households",
        sa.Column(
            "care_profile",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "households",
        sa.Column("care_brief_hash", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("households", "care_brief_hash")
    op.drop_column("households", "care_profile")
