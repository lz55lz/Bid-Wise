"""Align enterprise_profile identity with the UUID enterprise domain model.

The legacy profile table uses ``ep_id`` as its physical primary key and
``enterprise_id`` as the UUID association.  The ORM previously mapped a
nullable compatibility column named ``id`` as its primary key, which caused
new profile inserts to explicitly write NULL to ``ep_id``.
"""

from alembic import op

revision = "202609120000"
down_revision = "202609110000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Production databases created by the legacy bid-tag migration already
    # have this sequence.  Reattach a default defensively for installations
    # where it was dropped during later table evolution.
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('app.enterprise_profile_ep_id_seq') IS NULL THEN
                CREATE SEQUENCE app.enterprise_profile_ep_id_seq;
            END IF;
            ALTER TABLE app.enterprise_profile
                ALTER COLUMN ep_id SET DEFAULT nextval('app.enterprise_profile_ep_id_seq');
            ALTER SEQUENCE app.enterprise_profile_ep_id_seq
                OWNED BY app.enterprise_profile.ep_id;
            PERFORM setval(
                'app.enterprise_profile_ep_id_seq',
                COALESCE((SELECT MAX(ep_id) FROM app.enterprise_profile), 0) + 1,
                false
            );
        END $$;
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_enterprise_profile_enterprise_id
        ON app.enterprise_profile (enterprise_id)
        WHERE enterprise_id IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS app.ux_enterprise_profile_enterprise_id")
