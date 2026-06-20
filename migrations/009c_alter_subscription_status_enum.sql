-- Migration: Add missing values to subscription_status_enum
-- Date: 2026-06-14

ALTER TYPE subscription_status_enum ADD VALUE IF NOT EXISTS 'cancel_at_period_end';
