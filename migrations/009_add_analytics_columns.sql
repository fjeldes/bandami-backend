-- Migration: Add analytics columns to user_profiles
-- Date: 2026-06-11

ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS upgraded_at TIMESTAMPTZ;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS last_active_at TIMESTAMPTZ;
