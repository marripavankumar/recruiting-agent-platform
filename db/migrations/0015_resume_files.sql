-- 0015: original resume files (BRD section 11 document storage). The file itself lives in the
-- document store (RAP_DOCUMENT_STORES); this column holds its reference: key, file name, type,
-- size, SHA-256 and when it was stored. NULL when only resume text is known.

ALTER TABLE candidate ADD COLUMN resume_file jsonb;
