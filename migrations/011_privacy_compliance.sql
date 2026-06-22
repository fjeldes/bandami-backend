-- 011: Privacy compliance — consent tracking + review requests
-- GDPR Art.7, Art.22, Chilean Law 19.628

CREATE TABLE IF NOT EXISTS legal_documents (
    id TEXT PRIMARY KEY,
    doc_type TEXT NOT NULL,
    version TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    published_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    active BOOLEAN DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_legal_documents_type ON legal_documents(doc_type);

CREATE TABLE IF NOT EXISTS user_consents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    document_id TEXT NOT NULL REFERENCES legal_documents(id),
    consent_type TEXT NOT NULL,
    granted BOOLEAN NOT NULL,
    ip_address TEXT,
    user_agent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_consents_user ON user_consents(user_id);
CREATE INDEX IF NOT EXISTS idx_user_consents_type ON user_consents(consent_type);

CREATE TABLE IF NOT EXISTS review_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    evaluation_id UUID NOT NULL REFERENCES evaluations(id),
    user_id UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    reviewer_id UUID,
    reviewer_notes TEXT,
    resolved_band REAL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_review_requests_status ON review_requests(status);
CREATE INDEX IF NOT EXISTS idx_review_requests_user ON review_requests(user_id);

-- Seed initial legal documents
INSERT INTO legal_documents (id, doc_type, version, content_hash, published_at, active)
VALUES
    ('tos_v1', 'tos', '2026-06-20', 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855', NOW(), TRUE),
    ('privacy_v1', 'privacy', '2026-06-20', 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855', NOW(), TRUE),
    ('ai_processing_v1', 'ai_processing', '2026-06-22', 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855', NOW(), TRUE)
ON CONFLICT (id) DO NOTHING;
