package chwriter

import (
	"context"
	"crypto/sha256"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"testing"
)

func TestSkillSchemaReceiptMatchesPackagedMigration(t *testing.T) {
	b, err := os.ReadFile("../../../futureagi/tracer/services/clickhouse/v2/schema/028_skill_attribution.sql")
	if err != nil {
		t.Fatal(err)
	}
	if fmt.Sprintf("%x", sha256.Sum256(b)) != skillSchemaSHA {
		t.Fatal("startup probe must match the packaged migration")
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
