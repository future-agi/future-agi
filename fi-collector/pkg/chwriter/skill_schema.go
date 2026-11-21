package chwriter

import (
	"context"
	_ "embed"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// The collector image builds from fi-collector/ alone. Generate the two SELECTs
// from the packaged migration; the drift test checks this embedded projection.
//go:generate go test -run ^TestSkillSchemaReceiptMatchesPackagedMigration$ -write-skill-selects

//go:embed skill_view_selects.sql
var skillViewSelects string

// This release writes to a single local spans table, not a Distributed router
// or a replicated history. A receipt alone survives DETACH/DROP. Check the
// live source -> MV edges, local destinations and full SELECTs. No application data
// is scanned, and no command (including DDL) runs with writer privileges.
const skillSchemaSHA = "ad2619673601e7df807bd62fe6ba0117e24b8f50e911c05c182ec0d2790c973b"

// as_select is ClickHouse's single-line rendering of the stored SELECT AST.
// Render the packaged SELECT with the same server, qualifying *its* spans
// reference. Never rewrite, normalize literals in, or execute the live SELECT.
var skillSchemaProbe = skillViewSelects + `SELECT count() = 6
    AND (SELECT count() FROM system.databases WHERE name = currentDatabase() AND engine IN ('Atomic', 'Ordinary')) = 1
    AND (SELECT argMax(sha256, applied_at) FROM schema_versions
         WHERE filename = '028_skill_attribution.sql' AND notes IN ('schema-topology/v1/local', '')) = '` + skillSchemaSHA + `'
FROM system.tables WHERE database = currentDatabase() AND (
    (name = 'schema_versions' AND engine = 'MergeTree') OR
    (name = 'spans' AND engine = 'ReplacingMergeTree'
     AND hasAll(arrayZip(dependencies_database, dependencies_table),
         [(currentDatabase(), 'skill_trace_links_mv'), (currentDatabase(), 'skill_load_failures_mv')])) OR
    (name IN ('skill_trace_links', 'skill_load_failures') AND engine = 'ReplacingMergeTree') OR
    (name IN ('skill_trace_links_mv', 'skill_load_failures_mv') AND engine = 'MaterializedView'
     AND as_select = if(name = 'skill_trace_links_mv', expected_links, expected_failures)
     AND startsWith(create_table_query, concat('CREATE MATERIALIZED VIEW ', currentDatabase(), '.', name,
         ' TO ', currentDatabase(), '.', replaceRegexpOne(name, '_mv$', ''), ' ')))) FORMAT TabSeparated`

// SkillSchemaReady probes the exact HTTP target, database and credentials used
// for Insert. Never follow redirects or accept a partial/late exception body.
func (w *Writer) SkillSchemaReady(ctx context.Context) bool {
	if w.cfg.Table != "spans" {
		return false
	}
	ctx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	q := url.Values{"database": {w.cfg.Database}, "readonly": {"1"}, "max_threads": {"1"}, "max_execution_time": {"1"}, "wait_end_of_query": {"1"}}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, w.cfg.URL+"?"+q.Encode(), strings.NewReader(skillSchemaProbe))
	if err != nil {
		return false
	}
	if w.cfg.Username != "" {
		req.SetBasicAuth(w.cfg.Username, w.cfg.Password)
	}
	client := *w.client
	client.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	resp, err := client.Do(req)
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 3))
	return err == nil && resp.StatusCode == http.StatusOK && resp.Header.Get("X-ClickHouse-Exception-Code") == "" && resp.Trailer.Get("X-ClickHouse-Exception-Code") == "" && string(body) == "1\n"
}
