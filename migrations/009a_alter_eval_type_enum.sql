-- Migration: Add new values to eval_type_enum
-- Separated from 009 to avoid UnsafeNewEnumValueUsage:
-- ALTER TYPE ADD VALUE cannot be in the same transaction
-- as statements that use the new value.
-- Date: 2026-06-11

ALTER TYPE eval_type_enum ADD VALUE IF NOT EXISTS 'free';
ALTER TYPE eval_type_enum ADD VALUE IF NOT EXISTS 'pro_monthly';
ALTER TYPE eval_type_enum ADD VALUE IF NOT EXISTS 'admin';
