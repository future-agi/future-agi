package cohere

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// embedRecorder is a Cohere /v2/embed upstream that keeps the input_type of
// every request it was sent.
type embedRecorder struct {
	*httptest.Server
	mu    sync.Mutex
	types []string
}

func startEmbedRecorder(t *testing.T) *embedRecorder {
	t.Helper()
	rec := &embedRecorder{}
	rec.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req cohereEmbedRequest
		_ = json.NewDecoder(r.Body).Decode(&req)
		rec.mu.Lock()
		rec.types = append(rec.types, req.InputType)
		rec.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"id":"e1","embeddings":{"float":[[0.1,0.2]]},"meta":{"billed_units":{"input_tokens":2}}}`))
	}))
	t.Cleanup(rec.Close)
	return rec
}

func (r *embedRecorder) last() string {
	r.mu.Lock()
	defer r.mu.Unlock()
	if len(r.types) == 0 {
		return ""
	}
	return r.types[len(r.types)-1]
}

func embedWith(t *testing.T, p *Provider, inputType string) {
	t.Helper()
	_, err := p.CreateEmbedding(context.Background(), &models.EmbeddingRequest{
		Model: "embed-english-v3.0", Input: json.RawMessage(`"how do I reset my password?"`), InputType: inputType,
	})
	if err != nil {
		t.Fatalf("CreateEmbedding(input_type %q): %v", inputType, err)
	}
}

// A request's own input_type goes out per request, mapped to Cohere's names,
// so a query is embedded as a search_query even on a provider whose static
// x-cohere-input-type header says search_document.
func TestCohereQueryInputTypePerRequest(t *testing.T) {
	rec := startEmbedRecorder(t)
	p, err := New("cohere", config.ProviderConfig{
		BaseURL: rec.URL, APIKey: "test-key",
		Headers: map[string]string{"x-cohere-input-type": "search_document"},
	})
	if err != nil {
		t.Fatal(err)
	}
	for in, want := range map[string]string{
		models.EmbeddingInputTypeQuery:          "search_query",
		models.EmbeddingInputTypeDocument:       "search_document",
		models.EmbeddingInputTypeClassification: "classification",
		models.EmbeddingInputTypeClustering:     "clustering",
	} {
		embedWith(t, p, in)
		if got := rec.last(); got != want {
			t.Errorf("input_type %q went upstream as %q, want %q", in, got, want)
		}
	}
	if !p.AcceptsEmbeddingInputType() {
		t.Error("the Cohere adapter takes a per-request input_type but does not say so")
	}
}

// With no input_type in the request, the static x-cohere-input-type header
// is the default, and without that header search_document.
func TestCohereHeaderDefaultWhenNoInputType(t *testing.T) {
	rec := startEmbedRecorder(t)
	withHeader, err := New("cohere", config.ProviderConfig{
		BaseURL: rec.URL, APIKey: "test-key",
		Headers: map[string]string{"x-cohere-input-type": "classification"},
	})
	if err != nil {
		t.Fatal(err)
	}
	embedWith(t, withHeader, "")
	if got := rec.last(); got != "classification" {
		t.Errorf("no input_type, header classification: sent %q", got)
	}

	embedWith(t, newTestProvider(t, rec.URL), "")
	if got := rec.last(); got != "search_document" {
		t.Errorf("no input_type, no header: sent %q, want search_document", got)
	}
}

// A rerank response reports the search units Cohere billed
// (billed_units.search_units), which the rerank handler records for the
// request; a response that reports none counts zero, never its tokens.
func TestCohereRerankBilledUnits(t *testing.T) {
	for _, tc := range []struct {
		name, meta string
		want       int
	}{
		{"billed", `,"meta":{"billed_units":{"search_units":2,"input_tokens":4096}}`, 2},
		{"tokens only", `,"meta":{"billed_units":{"input_tokens":4096}}`, 0},
		{"no meta", ``, 0},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				w.Write([]byte(`{"id":"rr","results":[{"index":1,"relevance_score":0.8}]` + tc.meta + `}`))
			}))
			defer ts.Close()
			resp, err := newTestProvider(t, ts.URL).Rerank(context.Background(), &models.RerankRequest{
				Model: "rerank-english-v3.0", Query: "q", Documents: []string{"a", "b"},
			})
			if err != nil {
				t.Fatalf("Rerank: %v", err)
			}
			if got := resp.SearchUnits(); got != tc.want {
				t.Errorf("SearchUnits() = %d, want %d", got, tc.want)
			}
		})
	}
}
