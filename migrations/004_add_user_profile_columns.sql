-- Migration: Add missing user_profiles columns (referral system)
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS referral_code VARCHAR UNIQUE;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS referred_by VARCHAR;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS referral_discounts INTEGER NOT NULL DEFAULT 0;
