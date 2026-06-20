-- Migration: Add unique constraint and index on stripe_session_id for idempotency
-- Date: 2026-06-11

-- First clean up any duplicates (keep the oldest record)
DELETE FROM user_subscriptions a USING (
  SELECT MIN(created_at) as keep_date, stripe_session_id
  FROM user_subscriptions
  WHERE stripe_session_id IS NOT NULL
  GROUP BY stripe_session_id
  HAVING COUNT(*) > 1
) b
WHERE a.stripe_session_id = b.stripe_session_id
  AND a.created_at > b.keep_date;

-- Add index
CREATE INDEX IF NOT EXISTS ix_user_subscriptions_stripe_session_id
  ON user_subscriptions (stripe_session_id);

-- Add unique constraint
ALTER TABLE user_subscriptions
  ADD CONSTRAINT uq_user_subscriptions_stripe_session_id
  UNIQUE (stripe_session_id);
