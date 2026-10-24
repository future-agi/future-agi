package server

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Release note C4: /v1/search and /v1/realtime answer 501.
// The gateway's openai provider (the adapter every OpenAI-format provider
// uses) serves neither search nor realtime, and no shipped adapter does
// (capabilities TestNoShippedAdapterServesSearchVideoOrRealtime).
func TestSearchAndRealtimeAnswer501(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()
	srv := createTestServer(t, mock.URL)

	t.Run("search", func(t *testing.T) {
		body := `{"query":"capital of France","search_provider":"openai"}`
		req := httptest.NewRequest(http.MethodPost, "/v1/search", bytes.NewBufferString(body))
		req.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)
		if w.Code != http.StatusNotImplemented {
			t.Fatalf("POST /v1/search: status %d, want 501; body %s", w.Code, w.Body.String())
		}
		var e models.ErrorResponse
		if err := json.Unmarshal(w.Body.Bytes(), &e); err != nil || e.Error.Code != "not_supported" ||
			!strings.Contains(e.Error.Message, "does not support search") {
			t.Errorf("POST /v1/search: body %s, want not_supported: the provider does not support search", w.Body.String())
		}
	})

	t.Run("realtime", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/v1/realtime?model=gpt-4o", nil)
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)
		if w.Code != http.StatusNotImplemented {
			t.Fatalf("GET /v1/realtime: status %d, want 501; body %s", w.Code, w.Body.String())
		}
		if !strings.Contains(w.Body.String(), "does not support realtime") {
			t.Errorf("GET /v1/realtime: body %s, want: the provider does not support realtime", w.Body.String())
		}
	})
}
