"""059 payout channels — additive 3-layer payee/bank/recipient split + reference audit.

ADDITIVE ONLY (Phase 1a): creates payment_channel, counterparty_bank_account,
payout_channel_registration, finance_payout_reference_audit; seeds the 3 Wise channels; migrates
existing finance_payout_bank_accounts rows into (bank_account + registration). Touches NOTHING existing
— the legacy finance_payout_bank_accounts and finance_vendor_payouts tables stay intact.

Phase 2 (separate revision, at cutover): rename finance_vendor_payouts -> finance_payouts, add
channel_id/registration_id, repoint payout_service off ENTITY_WISE_PROFILE, drop the legacy table.

Design: documentation/wip/PAYOUTS_DATA_MODEL.md.

Revision ID: 059_payout_channels
Revises: 058_vendor_gst_registrations
"""
from alembic import op

revision = "059_payout_channels"
down_revision = "058_vendor_gst_registrations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE payment_channel (
      id            serial PRIMARY KEY,
      provider      varchar(32) NOT NULL,
      label         varchar(64) NOT NULL,
      our_entity_id integer REFERENCES finance_entities(id),
      config        jsonb NOT NULL DEFAULT '{}',
      status        varchar(20) NOT NULL DEFAULT 'active',
      created_at    timestamp NOT NULL DEFAULT now()
    );
    CREATE UNIQUE INDEX ux_payment_channel_provider_profile
      ON payment_channel (provider, (config->>'profile_id'));

    INSERT INTO payment_channel (provider, label, our_entity_id, config) VALUES
      ('wise','Wise Ventures',1,'{"profile_id":"74921502"}'),
      ('wise','Wise SG',2,'{"profile_id":"13811029"}'),
      ('wise','Wise AU',3,'{"profile_id":"41524706"}');

    CREATE TABLE counterparty_bank_account (
      id                  serial PRIMARY KEY,
      counterparty_id     integer REFERENCES finance_counterparties(id) ON DELETE CASCADE,
      payee_type          varchar(16) NOT NULL DEFAULT 'counterparty',
      payee_id            integer,
      account_holder_name varchar(255),
      legal_type          varchar(16),
      currency            varchar(3),
      country             varchar(2),
      account_type        varchar(32),
      account_number      varchar(64),
      iban                varchar(64),
      bsb_code            varchar(16),
      sort_code           varchar(16),
      swift_bic           varchar(16),
      bank_code           varchar(32),
      bank_name           varchar(255),
      masked_account      varchar(64),
      is_default          boolean NOT NULL DEFAULT false,
      status              varchar(20) NOT NULL DEFAULT 'active',
      source              varchar(20),
      verified_by         varchar(120),
      verified_at         timestamp,
      created_by          varchar(120),
      created_at          timestamp NOT NULL DEFAULT now()
    );
    CREATE INDEX ix_cba_counterparty ON counterparty_bank_account (counterparty_id);
    CREATE UNIQUE INDEX ux_cba_default ON counterparty_bank_account (counterparty_id) WHERE is_default;

    CREATE TABLE payout_channel_registration (
      id                    serial PRIMARY KEY,
      bank_account_id       integer NOT NULL REFERENCES counterparty_bank_account(id) ON DELETE CASCADE,
      channel_id            integer NOT NULL REFERENCES payment_channel(id),
      external_recipient_id varchar(64) NOT NULL,
      status                varchar(20) NOT NULL DEFAULT 'active',
      raw                   jsonb,
      verified              boolean NOT NULL DEFAULT false,
      registered_at         timestamp NOT NULL DEFAULT now()
    );
    -- partial: only ONE active registration per (account, channel); superseded ones coexist (edits
    -- create a new recipient + supersede the old — Wise recipients are immutable, POL-127).
    CREATE UNIQUE INDEX ux_pcr_account_channel ON payout_channel_registration (bank_account_id, channel_id) WHERE status='active';
    CREATE INDEX ix_pcr_recipient ON payout_channel_registration (external_recipient_id);

    CREATE TABLE finance_payout_reference_audit (
      id          serial PRIMARY KEY,
      target_type varchar(32) NOT NULL,
      target_id   integer,
      action      varchar(16) NOT NULL,
      before      jsonb,
      after       jsonb,
      actor       varchar(120),
      actor_role  varchar(60),
      actor_ip    varchar(64),
      reason      text,
      created_at  timestamp NOT NULL DEFAULT now()
    );
    CREATE INDEX ix_fpra_target ON finance_payout_reference_audit (target_type, target_id);

    -- migrate existing payout bank accounts into the real-account layer (+ registration where a
    -- Wise recipient already exists), matching the channel by our funding entity.
    INSERT INTO counterparty_bank_account
      (id, counterparty_id, payee_type, payee_id, account_holder_name, currency, country,
       account_number, bank_code, bank_name, masked_account, is_default, status, source,
       verified_by, verified_at, created_by, created_at)
    SELECT id, counterparty_id, coalesce(payee_type,'counterparty'), payee_id, account_holder_name,
           currency, country, account_number, bank_code, bank_name, masked_account,
           is_default, status, source, verified_by, verified_at, created_by, created_at
    FROM finance_payout_bank_accounts;
    SELECT setval(pg_get_serial_sequence('counterparty_bank_account','id'),
                  coalesce((SELECT max(id) FROM counterparty_bank_account), 1));

    INSERT INTO payout_channel_registration
      (bank_account_id, channel_id, external_recipient_id, status, verified, registered_at)
    SELECT fpba.id, pc.id, fpba.wise_recipient_id, 'active', true, now()
    FROM finance_payout_bank_accounts fpba
    JOIN payment_channel pc ON pc.provider='wise' AND pc.our_entity_id = fpba.entity_id
    WHERE fpba.wise_recipient_id IS NOT NULL;
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS finance_payout_reference_audit;
    DROP TABLE IF EXISTS payout_channel_registration;
    DROP TABLE IF EXISTS counterparty_bank_account;
    DROP TABLE IF EXISTS payment_channel;
    """)
