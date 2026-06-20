-- ============================================================
-- Migration 001: Update plan pricing & limits
-- ============================================================
-- Free: 3 eval/day, instant feedback (delay 0h)
-- Exam Week Pass: deactivated (Premium now uses subscription with $2.99 first week)
-- ============================================================

UPDATE subscription_plans
SET
    daily_eval_limit = 3,
    feedback_delay_hours = 0,
    description = '3 daily evaluations. Speaking Part 1 + Writing. Instant band score.'
WHERE slug = 'free';

UPDATE subscription_plans
SET is_active = FALSE
WHERE slug = 'exam_week_pass';
