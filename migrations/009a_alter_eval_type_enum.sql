-- Migration: Add new values to eval_type_enum (v2 — forced re-run)
-- Date: 2026-06-14

ALTER TYPE eval_type_enum ADD VALUE IF NOT EXISTS 'free';
ALTER TYPE eval_type_enum ADD VALUE IF NOT EXISTS 'pro_monthly';
ALTER TYPE eval_type_enum ADD VALUE IF NOT EXISTS 'admin';
