CREATE TABLE IF NOT EXISTS user_payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    subscription_id UUID REFERENCES user_subscriptions(id) ON DELETE SET NULL,
    amount_clp INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'CLP',
    flow_order TEXT,
    flow_invoice_id TEXT,
    period_start TIMESTAMPTZ,
    period_end TIMESTAMPTZ,
    payment_type TEXT NOT NULL DEFAULT 'recurring',
    status TEXT NOT NULL DEFAULT 'paid',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_user_payments_user_id ON user_payments(user_id);
CREATE INDEX IF NOT EXISTS idx_user_payments_subscription_id ON user_payments(subscription_id);
CREATE INDEX IF NOT EXISTS idx_user_payments_flow_order ON user_payments(flow_order);
