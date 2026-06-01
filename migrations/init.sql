-- ============================================================
-- IELTS SaaS — Initial Schema for plain PostgreSQL
-- No dependency on Supabase Auth
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- ENUMS
-- ============================================================
DO $$ BEGIN
    CREATE TYPE exam_type_enum       AS ENUM ('writing', 'speaking');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE exam_status_enum     AS ENUM ('pending', 'processing', 'completed', 'failed');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE writing_task_enum    AS ENUM ('task1', 'task2');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE ai_provider_enum     AS ENUM ('gemini', 'openai');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE subscription_status_enum AS ENUM ('active', 'canceled', 'past_due', 'expired', 'trialing');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE subscription_tier_enum AS ENUM ('free', 'premium');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE eval_type_enum       AS ENUM ('daily', 'credit_pack');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ============================================================
-- TABLES
-- ============================================================

CREATE TABLE IF NOT EXISTS user_profiles (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email               TEXT NOT NULL UNIQUE,
    full_name           TEXT,
    hashed_password     TEXT,
    email_confirmed_at  TIMESTAMPTZ,
    google_id           TEXT,
    subscription_tier   subscription_tier_enum NOT NULL DEFAULT 'free',
    stripe_customer_id  TEXT,
    avatar_url          TEXT,
    role                TEXT NOT NULL DEFAULT 'user',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '7 days'),
    revoked     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user ON refresh_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_hash ON refresh_tokens(token_hash);

CREATE TABLE IF NOT EXISTS question_bank (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exam_type       exam_type_enum NOT NULL,
    task_type       writing_task_enum,
    difficulty      SMALLINT NOT NULL DEFAULT 1 CHECK (difficulty BETWEEN 1 AND 5),
    prompt_text     TEXT NOT NULL,
    title           TEXT,
    module          TEXT DEFAULT 'general',
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_question_bank_type ON question_bank(exam_type, task_type);

CREATE TABLE IF NOT EXISTS exams (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    question_id         UUID REFERENCES question_bank(id),
    exam_type           exam_type_enum NOT NULL,
    task_type           writing_task_enum,
    status              exam_status_enum NOT NULL DEFAULT 'pending',
    attempt_number      INTEGER NOT NULL DEFAULT 1,
    time_taken_seconds  INTEGER,
    eval_source         eval_type_enum NOT NULL DEFAULT 'daily',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_exams_user_id       ON exams(user_id);
CREATE INDEX IF NOT EXISTS idx_exams_status        ON exams(status);
CREATE INDEX IF NOT EXISTS idx_exams_created_at    ON exams(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_exams_user_date     ON exams(user_id, ((created_at AT TIME ZONE 'UTC')::date));
CREATE INDEX IF NOT EXISTS idx_exams_user_date_eval ON exams(user_id, ((created_at AT TIME ZONE 'UTC')::date), eval_source);

CREATE TABLE IF NOT EXISTS evaluations (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exam_id                 UUID NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    user_submission         TEXT NOT NULL,
    audio_url               TEXT,
    overall_band            NUMERIC(2,1) CHECK (overall_band >= 0 AND overall_band <= 9),
    criteria_scores         JSONB NOT NULL DEFAULT '{}',
    detailed_feedback       TEXT NOT NULL DEFAULT '',
    grammar_corrections     JSONB DEFAULT '[]',
    provider_used           ai_provider_enum NOT NULL DEFAULT 'gemini',
    ai_model_used           TEXT,
    tokens_used             INTEGER,
    processing_time_ms      INTEGER,
    feedback_unlocks_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_evaluations_exam_id    ON evaluations(exam_id);
CREATE INDEX IF NOT EXISTS idx_evaluations_band       ON evaluations(overall_band);
CREATE INDEX IF NOT EXISTS idx_evaluations_unlocks    ON evaluations(feedback_unlocks_at);

CREATE TABLE IF NOT EXISTS subscription_plans (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug                TEXT NOT NULL UNIQUE,
    name                TEXT NOT NULL,
    description         TEXT,
    price_cents         INTEGER NOT NULL DEFAULT 0,
    currency            TEXT NOT NULL DEFAULT 'usd',
    interval            TEXT NOT NULL DEFAULT 'month' CHECK (interval IN ('one_time', 'month', 'year')),
    daily_eval_limit    INTEGER NOT NULL DEFAULT 0,
    provider            ai_provider_enum NOT NULL DEFAULT 'gemini',
    feedback_delay_hours INTEGER NOT NULL DEFAULT 0,
    stripe_price_id     TEXT,
    stripe_product_id   TEXT,
    sort_order          INTEGER NOT NULL DEFAULT 0,
    is_active           BOOLEAN NOT NULL DEFAULT true,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_subscriptions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    plan_id                 UUID NOT NULL REFERENCES subscription_plans(id),
    status                  subscription_status_enum NOT NULL DEFAULT 'active',
    stripe_subscription_id  TEXT,
    current_period_start    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    current_period_end      TIMESTAMPTZ NOT NULL DEFAULT 'infinity',
    canceled_at             TIMESTAMPTZ,
    auto_renew              BOOLEAN NOT NULL DEFAULT true,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_subscriptions_user ON user_subscriptions(user_id);
CREATE INDEX IF NOT EXISTS idx_user_subscriptions_active ON user_subscriptions(user_id, status);

CREATE TABLE IF NOT EXISTS credit_transactions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    amount              INTEGER NOT NULL,
    transaction_type    TEXT NOT NULL CHECK (transaction_type IN ('daily_eval', 'credit_pack_use', 'credit_pack_purchase', 'refund', 'bonus')),
    description         TEXT,
    stripe_payment_id   TEXT,
    metadata            JSONB DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_credit_tx_user_id ON credit_transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_credit_tx_date   ON credit_transactions(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS user_credit_packs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    credits_total   INTEGER NOT NULL CHECK (credits_total > 0),
    credits_used    INTEGER NOT NULL DEFAULT 0 CHECK (credits_used >= 0),
    purchased_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_user_credit_packs_user ON user_credit_packs(user_id);
CREATE INDEX IF NOT EXISTS idx_credit_packs_available  ON user_credit_packs(user_id, credits_total, credits_used);

-- ============================================================
-- FUNCTIONS
-- ============================================================

CREATE OR REPLACE FUNCTION get_user_active_plan(p_user_id UUID)
RETURNS TABLE(
    plan_id             UUID,
    daily_eval_limit    INTEGER,
    provider            ai_provider_enum,
    feedback_delay_hours INTEGER,
    subscription_status subscription_status_enum
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        sp.id,
        sp.daily_eval_limit,
        sp.provider,
        sp.feedback_delay_hours,
        us.status
    FROM user_subscriptions us
    JOIN subscription_plans sp ON sp.id = us.plan_id
    WHERE us.user_id = p_user_id
      AND us.status = 'active'
      AND us.current_period_end > NOW()
    ORDER BY sp.sort_order
    LIMIT 1;

    IF NOT FOUND THEN
        RETURN QUERY
        SELECT sp.id, sp.daily_eval_limit, sp.provider, sp.feedback_delay_hours, 'active'::subscription_status_enum
        FROM subscription_plans sp
        WHERE sp.slug = 'free'
        LIMIT 1;
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION get_daily_eval_count(p_user_id UUID)
RETURNS INTEGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO v_count
    FROM exams
    WHERE user_id = p_user_id
      AND created_at::date = CURRENT_DATE
      AND eval_source = 'daily';
    RETURN v_count;
END;
$$;

CREATE OR REPLACE FUNCTION consume_credit_pack(p_user_id UUID)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    v_pack_id UUID;
BEGIN
    SELECT id INTO v_pack_id
    FROM user_credit_packs
    WHERE user_id = p_user_id
      AND credits_used < credits_total
      AND (expires_at IS NULL OR expires_at > NOW())
    ORDER BY purchased_at
    LIMIT 1
    FOR UPDATE;

    IF v_pack_id IS NULL THEN
        RETURN FALSE;
    END IF;

    UPDATE user_credit_packs
    SET credits_used = credits_used + 1
    WHERE id = v_pack_id;

    INSERT INTO credit_transactions (user_id, amount, transaction_type, description)
    VALUES (p_user_id, -1, 'credit_pack_use', 'Credit pack consumed');

    RETURN TRUE;
END;
$$;

-- ============================================================
-- VIEW: Dashboard del usuario
-- ============================================================

CREATE OR REPLACE VIEW user_dashboard_stats AS
SELECT
    up.id AS user_id,
    up.subscription_tier,
    COALESCE(sp.daily_eval_limit, 4) AS daily_eval_limit,
    (
        SELECT COUNT(*) FROM exams
        WHERE user_id = up.id AND created_at::date = CURRENT_DATE AND eval_source = 'daily'
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

-- ============================================================
-- SEED DATA: Planes por defecto
-- ============================================================
INSERT INTO subscription_plans (slug, name, description, price_cents, interval, daily_eval_limit, provider, feedback_delay_hours, sort_order)
VALUES
    ('free', 'Free', '4 daily evaluations with basic AI. Full feedback within 24h.', 0, 'month', 4, 'gemini', 24, 1),
    ('exam_week_pass', 'Exam Week Pass', '7-day full access with advanced AI. Perfect if your exam is this week.', 499, 'one_time', 30, 'openai', 0, 5),
    ('premium', 'Premium', '30 daily evaluations with advanced AI. Instant feedback. History & progress tracking.', 1499, 'month', 30, 'openai', 0, 10),
    ('credit_pack_10', 'Pack 10 Credits', '10 extra evaluations with no daily limit. Advanced AI.', 799, 'one_time', 0, 'openai', 0, 20),
    ('credit_pack_25', 'Pack 25 Credits', '25 extra evaluations with no daily limit. Advanced AI. Best value.', 1499, 'one_time', 0, 'openai', 0, 21)
ON CONFLICT (slug) DO NOTHING;

-- ============================================================
-- SEED DATA: Banco de preguntas
-- ============================================================
INSERT INTO question_bank (exam_type, task_type, difficulty, prompt_text, title, module) VALUES
('writing', 'task1', 1, 'The graph below shows the average monthly temperatures in three major cities (London, New York, and Sydney). Summarize the information by selecting and reporting the main features, and make comparisons where relevant. Write at least 150 words.', 'Average Monthly Temperatures', 'academic'),
('writing', 'task1', 2, 'The bar chart below shows the percentage of adults who participated in various leisure activities in four different countries in 2022. Summarize the information by selecting and reporting the main features, and make comparisons where relevant. Write at least 150 words.', 'Leisure Activities by Country', 'academic'),
('writing', 'task1', 2, 'The table below gives information about the amount of CO2 emissions produced per person in five European countries from 2005 to 2020. Summarize the information by selecting and reporting the main features, and make comparisons where relevant. Write at least 150 words.', 'CO2 Emissions Per Capita', 'academic'),
('writing', 'task2', 1, 'Some people believe that unpaid community service should be a compulsory part of high school programs. To what extent do you agree or disagree? Give reasons for your answer and include any relevant examples from your own knowledge or experience. Write at least 250 words.', 'Community Service in Schools', 'general'),
('writing', 'task2', 2, 'In many countries, the number of people living in cities is increasing at a rapid rate. Some people believe this is a positive development, while others argue it has negative consequences. Discuss both views and give your own opinion. Write at least 250 words.', 'Urbanization Trends', 'general'),
('writing', 'task2', 3, 'Technology is replacing many traditional jobs with automation and artificial intelligence. Some people believe this will result in mass unemployment, while others argue that new types of jobs will emerge. Discuss both views and give your own opinion. Write at least 250 words.', 'Technology & Employment', 'general'),
('writing', 'task2', 3, 'Some people think that governments should spend money on the protection of wild animals, while others believe it is better to spend money on the human population. Discuss both views and give your opinion. Write at least 250 words.', 'Wildlife Protection vs Human Needs', 'general'),
('writing', 'task2', 2, 'In many countries, fast food is becoming cheaper and more widely available. Do you think the advantages of this development outweigh the disadvantages? Write at least 250 words.', 'Fast Food Availability', 'general'),
('speaking', NULL, 1, 'Describe a place you have visited that you particularly liked. You should say: where it is, when you went there, what you did there, and explain why you liked it.', 'Describe a place you have visited', 'general'),
('speaking', NULL, 2, 'Describe a person who has influenced you in your life. You should say: who this person is, how you know them, what they do, and explain why this person has influenced you.', 'Describe a person who influenced you', 'general'),
('speaking', NULL, 1, 'Describe your favorite way to spend your free time. You should say: what activity it is, when you usually do it, who you do it with, and explain why you enjoy it.', 'Describe your favorite free time activity', 'general'),
('speaking', NULL, 2, 'Describe an achievement you are proud of. You should say: what you achieved, when it happened, how you achieved it, and explain why you felt proud.', 'Describe an achievement you are proud of', 'general'),
('speaking', NULL, 3, 'Describe a change you would like to make in your daily routine. You should say: what the change is, why you want to make it, what difficulties you might face, and explain how this change would improve your life.', 'Describe a change in your daily routine', 'general'),
('speaking', NULL, 2, 'Describe a piece of technology that you find useful. You should say: what it is, when you first used it, how often you use it, and explain why you find it useful.', 'Describe a useful piece of technology', 'general')
ON CONFLICT DO NOTHING;
