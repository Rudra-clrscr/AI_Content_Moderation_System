-- For databases created from the first version of create_table.sql,
-- where gate_version was INT. Safe to re-run.
IF EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID(N'dbo.moderation_events')
      AND name = N'gate_version'
      AND system_type_id = TYPE_ID(N'int')
)
    ALTER TABLE dbo.moderation_events ALTER COLUMN gate_version VARCHAR(50) NULL;
