-- Migration: Add free, pro_monthly, admin to eval_type_enum (v3 — forced)
-- Date: 2026-06-18

ALTER TYPE eval_type_enum ADD VALUE IF NOT EXISTS 'free';
ALTER TYPE eval_type_enum ADD VALUE IF NOT EXISTS 'pro_monthly';
ALTER TYPE eval_type_enum ADD VALUE IF NOT EXISTS 'admin';
