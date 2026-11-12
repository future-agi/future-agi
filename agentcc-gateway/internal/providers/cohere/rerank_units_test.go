package cohere

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// TestCohereRerankSearchUnits: a rerank response's
// search_units are Cohere's billed_units.search_units, the unit a rerank call
// is billed by, not billed_units.input_tokens.
func TestCohereRerankSearchUnits(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"id":"rr","results":[{"index":0,"relevance_score":0.9}],
			"meta":{"billed_units":{"search_units":3,"input_tokens":812}}}`))
	}))
	defer ts.Close()

	resp, err := newTestProvider(t, ts.URL).Rerank(context.Background(), &models.RerankRequest{
		Model: "rerank-english-v3.0", Query: "q", Documents: []string{"a"},
	})
	if err != nil {
		t.Fatalf("Rerank: %v", err)
	}
	if resp.Meta == nil || resp.Meta.BilledUnits["search_units"] != 3 {
		t.Fatalf("meta = %+v, want billed_units.search_units 3", resp.Meta)
	}
}
