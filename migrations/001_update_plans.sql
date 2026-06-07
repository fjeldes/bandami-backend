-- ============================================================
-- Migration 001: Update plan pricing & limits
-- ============================================================
-- Free: 3 eval/day, instant feedback (delay 0h)
-- Exam Week Pass: $2.99, 10 eval/day
-- ============================================================

UPDATE subscription_plans
SET
    daily_eval_limit = 3,
    feedback_delay_hours = 0,
    description = '3 daily evaluations with basic AI. Speaking Part 1 only. Instant score.'
WHERE slug = 'free';

UPDATE subscription_plans
SET
    price_cents = 299,
    daily_eval_limit = 10,
    description = '7-day access with advanced AI. 10 evaluations/day. Includes all modules.'
WHERE slug = 'exam_week_pass';
