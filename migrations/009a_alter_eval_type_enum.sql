-- Migration: Add new values to eval_type_enum
-- Must run outside a transaction because ALTER TYPE ... ADD VALUE
-- cannot be used in a transaction that also uses the new value.
-- Date: 2026-06-11

COMMIT;

ALTER TYPE eval_type_enum ADD VALUE IF NOT EXISTS 'free';
ALTER TYPE eval_type_enum ADD VALUE IF NOT EXISTS 'pro_monthly';
ALTER TYPE eval_type_enum ADD VALUE IF NOT EXISTS 'admin';
