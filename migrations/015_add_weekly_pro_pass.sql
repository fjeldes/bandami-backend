-- ============================================================
-- Migration 015: Add Weekly Pro Pass plan ($4.99 one-time, 7 days premium)
-- ============================================================

INSERT INTO subscription_plans (slug, name, description, price_cents, interval, daily_eval_limit, provider, feedback_delay_hours, sort_order)
VALUES ('weekly_pro_pass', 'Weekly Pro Pass', 'Full premium access for 7 days. All modules, detailed analysis, study plans. $4.99 one-time.', 499, 'one_time', 30, 'openai', 0, 7)
ON CONFLICT (slug) DO NOTHING;
