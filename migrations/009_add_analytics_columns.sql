-- Migration: Add analytics columns and fix dashboard view
-- Date: 2026-06-11

-- Add columns for usage analytics
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS upgraded_at TIMESTAMPTZ;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS last_active_at TIMESTAMPTZ;

-- Fix user_dashboard_stats view to count all eval sources
CREATE OR REPLACE VIEW user_dashboard_stats AS
SELECT
    up.id AS user_id,
    up.subscription_tier,
    COALESCE(sp.daily_eval_limit, 4) AS daily_eval_limit,
    (
        SELECT COUNT(*) FROM exams
        WHERE user_id = up.id
          AND created_at::date = CURRENT_DATE
          AND eval_source IN ('daily', 'free', 'pro_monthly', 'admin')
          AND status NOT IN ('pending', 'failed')
    ) AS daily_evals_used,
    (
        SELECT COUNT(*) FROM exams WHERE user_id = up.id
    ) AS total_exams,
    AVG(ev.overall_band)::NUMERIC(3,1) AS average_band,
    MAX(ev.overall_band) AS highest_band,
    COUNT(e.id) FILTER (WHERE e.exam_type = 'writing') AS writing_exams,
    COUNT(e.id) FILTER (WHERE e.exam_type = 'speaking') AS speaking_exams,
    COUNT(e.id) FILTER (WHERE e.status = 'completed') AS completed_exams,
    (
        SELECT COALESCE(SUM(credits_total - credits_used), 0)
        FROM user_credit_packs
        WHERE user_id = up.id AND (expires_at IS NULL OR expires_at > NOW())
    ) AS extra_credits_available
FROM user_profiles up
LEFT JOIN LATERAL (
    SELECT daily_eval_limit FROM get_user_active_plan(up.id)
) sp ON true
LEFT JOIN exams e ON e.user_id = up.id
LEFT JOIN evaluations ev ON ev.exam_id = e.id
GROUP BY up.id, up.subscription_tier, sp.daily_eval_limit;
