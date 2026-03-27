"""Legacy revision placeholder.

Some environments were initialized with an earlier Alembic revision hash
(`9b7c008c68a7`) that is no longer present in this repo. Without a script
for that revision, Alembic cannot resolve the current DB state and fails
with:

    Can't locate revision identified by '9b7c008c68a7'

This file re-introduces that revision as a no-op placeholder so that:
- Existing databases stamped at 9b7c008c68a7 can upgrade forward.
- New databases can still upgrade normally (the next migration is `001`).
"""

from alembic import op

revision = "9b7c008c68a7"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No-op. The schema changes that existed in the legacy revision are now
    # represented (idempotently) in `001_initial_public_schema.py`.
    return


def downgrade() -> None:
    # No-op.
    return

