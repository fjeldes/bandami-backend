-- Migration: Add general_feedback column to evaluations
-- Date: 2026-05-30

ALTER TABLE evaluations ADD COLUMN IF NOT EXISTS general_feedback TEXT NOT NULL DEFAULT '';
