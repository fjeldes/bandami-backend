-- Migration: Add upgraded_text to evaluations
-- Date: 2026-06-14

ALTER TABLE evaluations ADD COLUMN IF NOT EXISTS upgraded_text TEXT;
