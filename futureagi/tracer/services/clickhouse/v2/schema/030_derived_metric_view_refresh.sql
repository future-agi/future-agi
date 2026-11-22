-- A fresh receipt repairs installations that applied 029 with a runner that
-- did not distribute ordinary view DDL. Do not rewrite the earlier receipt.
CREATE OR REPLACE VIEW derived_metrics AS
SELECT r.*
FROM derived_metric_results AS r
INNER JOIN (SELECT * FROM derived_metric_commits FINAL) AS c
    ON r.org_id = c.org_id AND r.workspace_id = c.workspace_id
    AND r.project_id = c.project_id AND r.metric_id = c.metric_id
    AND r.run_id = c.run_id AND r.result_id = c.result_id;
