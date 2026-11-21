package chwriter

import (
	"context"
	"crypto/sha256"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"testing"
)

var writeSkillSelects = flag.Bool("write-skill-selects", false, "regenerate the embedded 039 view SELECTs")

func TestSkillSchemaReceiptMatchesPackagedMigration(t *testing.T) {
	b, err := os.ReadFile("../../../futureagi/tracer/services/clickhouse/v2/schema/039_skill_attribution.sql")
	if err != nil {
		t.Fatal(err)
	}
	if fmt.Sprintf("%x", sha256.Sum256(b)) != skillSchemaSHA {
		t.Fatal("startup probe must match the packaged migration")
	}
	// This is a projection of exactly two known 039 declarations, not a SQL
	// rewriter. Fail generation if the migration no longer has this shape.
	var expressions []string
	for _, view := range []struct{ table, alias string }{
		{"skill_trace_links", "expected_links"}, {"skill_load_failures", "expected_failures"},
	} {
		header := "CREATE MATERIALIZED VIEW IF NOT EXISTS " + view.table + "_mv TO " + view.table + " AS\n"
		migration := string(b)
		_, after, found := strings.Cut(migration, header)
		body, _, end := strings.Cut(after, ";\n")
		const source = "\nFROM spans\nWHERE "
		if !found || !end || strings.Count(migration, header) != 1 || !strings.HasPrefix(body, "SELECT ") ||
			strings.ContainsAny(body, ";$") || strings.Count(body, source) != 1 {
			t.Fatalf("unexpected 039 definition of %s", view.table)
		}
		// Heredocs preserve every byte of the packaged projections and filters.
		qualified := strings.Replace(body, source, "\nFROM $mv$, currentDatabase(), $mv$.spans\nWHERE ", 1)
		expressions = append(expressions, "formatQuerySingleLine(concat($mv$"+qualified+"$mv$)) AS "+view.alias)
	}
	want := "-- Code generated from 039_skill_attribution.sql by go generate; DO NOT EDIT.\nWITH\n" + strings.Join(expressions, ",\n") + "\n"
	if *writeSkillSelects {
		if err := os.WriteFile("skill_view_selects.sql", []byte(want), 0644); err != nil {
			t.Fatal(err)
		}
	} else if skillViewSelects != want {
		t.Fatal("embedded SELECTs differ from 039: run go generate ./pkg/chwriter")
	}
}

func TestSkillSchemaProbeRequiresConfirmedReadOnlyResult(t *testing.T) {
	for _, tc := range []struct {
		result, trailer string
		ready           bool
	}{
		{"1\n", "", true}, {"0\n", "", false}, {"1", "", false}, {"1\nexception", "", false}, {"1\n", "241", false},
	} {
		cfg := mkConfig(t, "http://offline.invalid")
		cfg.Database, cfg.Username, cfg.Password = "test_boot", "reader", "fixture"
		w, err := New(cfg)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { _ = w.Close() })
		w.client.Transport = confirmationRoundTripper(func(r *http.Request) (*http.Response, error) {
			q := r.URL.Query()
			user, pass, ok := r.BasicAuth()
			sql, err := io.ReadAll(r.Body)
			if !ok || user != cfg.Username || pass != cfg.Password || q.Get("database") != cfg.Database || q.Get("readonly") != "1" || err != nil || string(sql) != skillSchemaProbe {
				t.Fatal("probe did not use target-bound read-only request")
			}
			trailer := make(http.Header)
			trailer.Set("X-ClickHouse-Exception-Code", tc.trailer)
			return &http.Response{StatusCode: http.StatusOK, Body: io.NopCloser(strings.NewReader(tc.result)), Trailer: trailer}, nil
		})
		if got := w.SkillSchemaReady(context.Background()); got != tc.ready {
			t.Errorf("result %q trailer %q: got %t", tc.result, tc.trailer, got)
		}
	}
}
