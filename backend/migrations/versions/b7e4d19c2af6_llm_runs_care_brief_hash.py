"""llm_runs.care_brief_hash

The audit trail could already say which prompt produced a row, and could not say which care
brief did. `llm_runs.prompt_version` is a hash of the prompt FILE, and the brief is not in that
file: it is assembled per household and sent as call input, wrapped in `<care_brief>`. So a run
made under "log breathlessness, weight and any fall" and a run made under a brief naming a
different person entirely record the same `prompt_version` and are the same row to anyone
reading this table. The brief is a relevance specification — it decides what counts as a care
event at all — which makes it the single biggest determinant of what a run extracted, and the
one input the audit trail could not see.

Nullable, and NULL means something: a merge call is shown two already-extracted events and no
brief, and a budget denial is a row for a call that was never made. Both genuinely have no
brief, and the column says so rather than being padded with a value that implies a filter was
in force.

No backfill, for the same reason. Every existing row predates the column, and the household's
current brief is not the brief those runs ran under — writing today's hash onto last month's
runs would make the audit trail confidently wrong, which is worse than empty. Existing rows
read as "unknown", which is exactly what they are.

Text, not a fixed-width char: 12 hex characters today, matching `prompt_version`, and a column
type that does not have to be migrated if that convention ever changes.

Revision ID: b7e4d19c2af6
Revises: a3f2c81b9d04
Create Date: 2026-07-26 04:35:12.881204

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e4d19c2af6"
down_revision: str | Sequence[str] | None = "a3f2c81b9d04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("llm_runs", sa.Column("care_brief_hash", sa.Text(), nullable=True))


def downgrade() -> None:
    # Drops provenance, not data anyone depends on: nothing reads this column to decide
    # behaviour, so rolling back loses the ability to answer "which brief produced this run?"
    # for rows written while it existed, and breaks nothing else.
    op.drop_column("llm_runs", "care_brief_hash")
