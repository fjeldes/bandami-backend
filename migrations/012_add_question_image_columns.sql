-- ============================================================
-- Add image support columns to question_bank
-- ============================================================

ALTER TABLE question_bank ADD COLUMN img_url TEXT;
ALTER TABLE question_bank ADD COLUMN img_info TEXT;

-- Comments for documentation
COMMENT ON COLUMN question_bank.img_url IS 'URL to image hosted on GCS for writing task 1 academic (graphs, charts, etc.)';
COMMENT ON COLUMN question_bank.img_info IS 'Full text description of the image for AI evaluation (data points, trends, etc.)';
