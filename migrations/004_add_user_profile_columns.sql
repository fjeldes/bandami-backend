-- Migration: Add missing columns
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS referral_code VARCHAR UNIQUE;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS referred_by VARCHAR;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS referral_discounts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE user_subscriptions ADD COLUMN IF NOT EXISTS stripe_session_id VARCHAR;
