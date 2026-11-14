-- Read-only compatibility gate for the local bootstrap, not a source migration.
-- IF NOT EXISTS must not make an incompatible existing index look ready.
WITH
    [
        ('organization_id', 'String'),
        ('workspace_id', 'String'),
        ('project_id', 'String'),
        ('source_kind', 'LowCardinality(String)'),
        ('attribute_key', 'String'),
        ('attribute_type', 'LowCardinality(String)')
    ] AS scope_columns,
    [
        ('first_seen', 'SimpleAggregateFunction(min, DateTime64(6, \'UTC\'))'),
        ('last_seen', 'SimpleAggregateFunction(max, DateTime64(6, \'UTC\'))')
    ] AS time_columns,
    arrayConcat(scope_columns, [('key_folded', 'String')], time_columns) AS key_columns,
    arrayConcat(scope_columns, [
        ('value_fingerprint', 'FixedString(64)'),
        ('value_json', 'String'),
        ('value_search_text_folded', 'String')
    ], time_columns) AS value_columns,
    'organization_id, workspace_id, project_id, source_kind, attribute_key, attribute_type' AS key_identity,
    concat(key_identity, ', value_fingerprint, value_json') AS value_identity
SELECT count() = 2
FROM system.tables AS t
INNER JOIN
(
    SELECT
        table,
        arrayMap(x -> (x.2, x.3), arraySort(groupArray((position, name, type)))) AS columns,
        countIf(default_kind != '' OR default_expression != '') AS defaults
    FROM system.columns
    WHERE database = {database:String}
      AND table IN ('observed_attribute_keys', 'observed_attribute_values')
    GROUP BY table
) AS c ON c.table = t.name
WHERE t.database = {database:String}
  AND t.name IN ('observed_attribute_keys', 'observed_attribute_values')
  AND t.engine = 'AggregatingMergeTree'
  AND c.columns = if(t.name = 'observed_attribute_keys', key_columns, value_columns)
  AND c.defaults = 0
  AND t.sorting_key = if(t.name = 'observed_attribute_keys', key_identity, value_identity)
  AND t.primary_key = t.sorting_key
  AND t.partition_key = 'cityHash64(workspace_id) % 64'
  AND position(t.create_table_query, 'CONSTRAINT valid_source CHECK source_kind IN (\'custom_attribute\', \'system_attribute\')') > 0
  AND position(t.create_table_query, 'CONSTRAINT valid_interval CHECK first_seen <= last_seen') > 0
  AND position(t.create_table_query, if(t.name = 'observed_attribute_keys',
      'CONSTRAINT valid_type CHECK attribute_type IN (\'string\', \'number\', \'boolean\', \'array\', \'map\', \'json\')',
      'CONSTRAINT valid_type CHECK attribute_type IN (\'string\', \'number\', \'boolean\', \'array\')')) > 0
  AND NOT match(t.create_table_query, '\\bTTL\\b');
