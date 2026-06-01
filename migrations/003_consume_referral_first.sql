-- Migration: Update consume_credit_pack to prioritize referral credits before purchased
-- Referral credits (source='referral') are consumed before purchased credits (source='purchase')

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
    ORDER BY CASE WHEN source = 'referral' THEN 0 ELSE 1 END, purchased_at
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
