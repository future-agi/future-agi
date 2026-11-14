package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Scheduled completions and batches call the provider outside the
// /v1/chat/completions handler. The provider itself chooses the Responses
// route, so they carry gpt-5.6 tools too.

func TestScheduledCompletionCarriesGPT56ToolsOverResponses(t *testing.T) {
	srv, upstream := newResponsesBridgeServerWith(t, bridgeServerOptions{configure: func(cfg *config.Config) {
		cfg.Routing.Scheduled.Enabled = true
		cfg.Routing.Scheduled.RetryAttempts = 1
	}})

	at := time.Now().Add(1500 * time.Millisecond).UTC().Truncate(time.Second).Add(time.Second)
	submit := fmt.Sprintf(`{"scheduled_at":%q,"request":%s}`, at.Format(time.RFC3339), chatToolBody("gpt-5.6-terra", false, ""))
	req := httptest.NewRequest(http.MethodPost, "/v1/scheduled", bytes.NewBufferString(submit))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	if w.Code != http.StatusCreated {
		t.Fatalf("submit status = %d: %s", w.Code, w.Body.String())
	}
	var job struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &job); err != nil || job.ID == "" {
		t.Fatalf("submit body %s: %v", w.Body.String(), err)
	}

	var got struct {
		Status   string          `json:"status"`
		Response json.RawMessage `json:"response"`
		Error    string          `json:"error"`
	}
	deadline := time.Now().Add(15 * time.Second)
	for {
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/v1/scheduled/"+job.ID, nil))
		if err := json.Unmarshal(w.Body.Bytes(), &got); err != nil {
			t.Fatalf("job body %s: %v", w.Body.String(), err)
		}
		if got.Status == "completed" || got.Status == "failed" || time.Now().After(deadline) {
			break
		}
		time.Sleep(100 * time.Millisecond)
	}
	if got.Status != "completed" {
		t.Fatalf("scheduled job status = %q, error %q; want completed", got.Status, got.Error)
	}
	checkToolCompletion(t, got.Response)
	checkHits(t, upstream, 0, 1)
}

func TestBatchCarriesGPT56ToolsOverResponses(t *testing.T) {
	srv, upstream := newResponsesBridgeServer(t)

	var req models.ChatCompletionRequest
	if err := json.Unmarshal([]byte(chatToolBody("gpt-5.6-sol", false, "")), &req); err != nil {
		t.Fatal(err)
	}
	b := srv.batchProcessor.Submit([]*models.ChatCompletionRequest{&req}, 1)
	deadline := time.Now().Add(10 * time.Second)
	for b.GetStatus() == "processing" && time.Now().Before(deadline) {
		time.Sleep(20 * time.Millisecond)
	}
	if status := b.GetStatus(); status != "completed" {
		t.Fatalf("batch status = %q, want completed", status)
	}
	if len(b.Results) != 1 || b.Results[0] == nil {
		t.Fatalf("batch results = %+v, want one", b.Results)
	}
	result := b.Results[0]
	if result.Status != "success" {
		t.Fatalf("batch result = %s %q, want success", result.Status, result.Error)
	}
	raw, err := json.Marshal(result.Response)
	if err != nil {
		t.Fatal(err)
	}
	checkToolCompletion(t, raw)
	checkHits(t, upstream, 0, 1)
}
