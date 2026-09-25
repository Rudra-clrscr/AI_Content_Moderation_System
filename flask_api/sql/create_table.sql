CREATE TABLE moderation_events (
    request_id VARCHAR(36) PRIMARY KEY,
    content_id VARCHAR(255) NULL,
    content_type VARCHAR(50) NOT NULL,
    content_sha256 VARCHAR(64) NULL,

    status VARCHAR(20) NOT NULL,
    decision VARCHAR(20) NULL,
    decided_by VARCHAR(20) NULL,

    risk_score FLOAT NULL,
    predicted_label VARCHAR(50) NULL,

    label_scores NVARCHAR(MAX) NULL,
    gate_matches NVARCHAR(MAX) NULL,

    gate_version INT NULL,
    model_version VARCHAR(255) NULL,

    thresholds NVARCHAR(MAX) NULL,
    latency_ms NVARCHAR(MAX) NULL,

    decided_at DATETIMEOFFSET NULL,
    created_at DATETIMEOFFSET DEFAULT SYSDATETIMEOFFSET()
);