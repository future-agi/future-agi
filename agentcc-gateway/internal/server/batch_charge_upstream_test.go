package server

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/batch"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// The batch charge regression below runs the real gateway: the OpenAI-format
// adapter against a loopback upstream, attempt accounting, cost finalization,
// the error response boundary, the batch HTTP adapter, the processor and the
// batch JSON. Only the upstream is a stand-in.

type batchChargeWire struct {
	Summary struct {
		Cost           *float64 `json:"total_cost"`
		Known          bool     `json:"cost_known"`
		Unpriced       int      `json:"unpriced_requests"`
		Completed      int      `json:"completed"`
		Failed         int      `json:"failed"`
		BillableFailed int      `json:"billable_failed_requests"`
	} `json:"summary"`
	Results []struct {
		Status string   `json:"status"`
		Error  string   `json:"error"`
		Cost   *float64 `json:"cost"`
		Known  bool     `json:"cost_known"`
	} `json:"results"`
}

func chargeEvidenceGateway(t *testing.T, cfg *config.Config) *Server {
	t.Helper()
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	t.Cleanup(func() { registry.Close() })
	engine := pipeline.NewEngine(costplugin.New(true, testModelDBPtr().Load, nil, nil))
	srv := New(cfg, "", registry, engine, nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	return srv
}

func runChargeBatch(t *testing.T, srv *Server, model string) (batchChargeWire, string) {
	t.Helper()
	p := batch.NewProcessor(batch.RunAsChatRequest(func() http.Handler { return srv.httpServer.Handler }))
	defer p.Stop()
	b := p.Submit([]*models.ChatCompletionRequest{{
		Model: model, Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"batch charge"`)}},
	}}, 1)
	deadline := time.Now().Add(10 * time.Second)
	for b.GetCompletedAt() == nil && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if b.GetCompletedAt() == nil {
		t.Fatal("batch did not finish")
	}
	data, err := json.Marshal(batchGetResponse(b))
	if err != nil {
		t.Fatal(err)
	}
	var got batchChargeWire
	if err := json.Unmarshal(data, &got); err != nil {
		t.Fatal(err)
	}
	if len(got.Results) != 1 {
		t.Fatalf("want one result: %s", data)
	}
	return got, string(data)
}

// answerModelList answers the model discovery and connectivity probes.
func answerModelList(w http.ResponseWriter, r *http.Request) bool {
	if r.Method == http.MethodPost && strings.HasSuffix(r.URL.Path, "/chat/completions") {
		return false
	}
	w.Header().Set("Content-Type", "application/json")
	io.WriteString(w, `{"object":"list","data":[]}`)
	return true
}

// The provider accepted the call and answered 200, then the body was cut
// short. Whatever it generated and charged cannot be read, so the item and the
// batch carry no number, and the item keeps the reason.
func TestBatchTruncatedProviderAnswerIsUnknownCharge(t *testing.T) {
	var calls atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if answerModelList(w, r) {
			return
		}
		calls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Content-Length", "4096")
		w.WriteHeader(http.StatusOK)
		io.WriteString(w, `{"id":"chatcmpl-truncated","object":"chat.completion","choices":[{"index":0,"message":{"role":"assistant","content":"partial`)
		w.(http.Flusher).Flush()
		if conn, _, err := w.(http.Hijacker).Hijack(); err == nil {
			conn.Close()
		}
	}))
	defer upstream.Close()

	cfg := config.DefaultConfig()
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: upstream.URL, APIFormat: "openai", Models: []string{"gpt-4o"}}
	got, data := runChargeBatch(t, chargeEvidenceGateway(t, cfg), "gpt-4o")

	if calls.Load() < 1 {
		t.Fatalf("the upstream was never called: %s", data)
	}
	// The reason is the provider's 200 that could not be read; c2.5 hides the
	// raw provider text behind a correlation id, so the code carries it.
	item := got.Results[0]
	if item.Status != "error" || !strings.HasPrefix(item.Error, "provider_200:") {
		t.Errorf("item lost its truncation reason: %s", data)
	}
	if item.Known || item.Cost != nil {
		t.Errorf("unreadable provider answer shown as a known charge: %s", data)
	}
	if got.Summary.Known || got.Summary.Cost != nil || got.Summary.Unpriced != 1 || got.Summary.Failed != 1 {
		t.Errorf("batch total must be unavailable: %s", data)
	}
}
