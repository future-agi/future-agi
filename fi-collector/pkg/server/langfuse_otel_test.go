package server

import (
	"bytes"
	"context"
	"encoding/json"
	"math"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"

	"github.com/future-agi/future-agi/fi-collector/pkg/pricing"
)

// The Langfuse Python SDK 5.2.0 fixture shared with the converter tests.
const langfuseSDKFixture = "../../exporter/clickhouse25exporter/testdata/langfuse_5_2_0_otlp.json"

// TestLangfuseOTelRouteStoresMappedSpans posts the Langfuse SDK's OTLP/HTTP
// JSON body to the route the SDK sends to, with the production pricer, and
// checks the spans rows ClickHouse receives: the generation is an llm span
// with its model, tokens, a cost priced from them and its input/output; the
// root is a chain with its input/output and no cost.
func TestLangfuseOTelRouteStoresMappedSpans(t *testing.T) {
	body, err := os.ReadFile(langfuseSDKFixture)
	if err != nil {
		t.Fatal(err)
	}
	table, err := pricing.LoadTable("")
	if err != nil {
		t.Fatal(err)
	}
	wantCost, ok := table.Cost("gpt-4o-mini", 12, 9)
	if !ok || wantCost <= 0 {
		t.Fatalf("gpt-4o-mini table price = %v, %v; want a positive price", wantCost, ok)
	}
	ch, writer := newCHBodies(t)
	s := New(Config{}, writer, nil, nil, nil, WithPricer(pricing.New(table, nil)))
	req := httptest.NewRequest(http.MethodPost, "/api/public/otel/v1/traces", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	s.handleHTTPTraces(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status %d: %s", rec.Code, rec.Body)
	}
	s.drainNow(context.Background())

	// Assert the row encoding at the writer boundary; numbers decode as float64.
	rows := map[string]map[string]any{}
	ch.mu.Lock()
	defer ch.mu.Unlock()
	for _, batch := range ch.tables["spans"] {
		for _, line := range bytes.Split(bytes.TrimSpace(batch), []byte("\n")) {
			var row map[string]any
			if err := json.Unmarshal(line, &row); err != nil {
				t.Fatal(err)
			}
			name, _ := row["name"].(string)
			rows[name] = row
		}
	}
	gen, root := rows["llm-call"], rows["parity-langfuse-chat"]
	if len(rows) != 2 || gen == nil || root == nil {
		t.Fatalf("spans rows = %d, want the root and the generation", len(rows))
	}
	for _, c := range []struct {
		row    map[string]any
		column string
		want   any
	}{
		{gen, "observation_type", "llm"},
		{gen, "model", "gpt-4o-mini"},
		{gen, "prompt_tokens", float64(12)},
		{gen, "completion_tokens", float64(9)},
		{gen, "total_tokens", float64(21)},
		{root, "observation_type", "chain"},
		{root, "cost", float64(0)},
	} {
		if got := c.row[c.column]; got != c.want {
			t.Errorf("span %v: %s = %#v, want %#v", c.row["name"], c.column, got, c.want)
		}
	}
	if cost, _ := gen["cost"].(float64); math.Abs(cost-wantCost) > 1e-12 {
		t.Errorf("generation cost = %v, want %v (gpt-4o-mini, 12 in, 9 out)", gen["cost"], wantCost)
	}
	// Input and output are the SDK's own values, which stay stored as sent.
	for _, row := range []map[string]any{gen, root} {
		raw, _ := row["attrs_string"].(map[string]any)
		for column, key := range map[string]string{"input": "langfuse.observation.input", "output": "langfuse.observation.output"} {
			if want, _ := raw[key].(string); want == "" || row[column] != want {
				t.Errorf("span %v: %s = %#v, want %s %q", row["name"], column, row[column], key, want)
			}
		}
	}
}
