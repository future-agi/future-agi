package azure

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// An error the upstream sends mid-stream must end the stream with that error.
// Skipped, or passed on as an ordinary chunk, it left a failed stream to read
// as a finished one, and the request was logged as a success.
func TestStreamChatCompletion_UpstreamErrorMidStreamEndsTheStream(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		io.WriteString(w, "data: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":\"gpt-4o\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"hi\"}}]}\n\ndata: {\"error\":{\"message\":\"upstream exploded\",\"type\":\"server_error\",\"code\":\"server_error\"}}\n\n")
	}))
	defer ts.Close()

	var req models.ChatCompletionRequest
	if err := json.Unmarshal([]byte(`{"model":"gpt-4o","stream":true,"messages":[{"role":"user","content":"hi"}]}`), &req); err != nil {
		t.Fatal(err)
	}
	chunks, errs := newTestProvider(t, ts.URL).StreamChatCompletion(context.Background(), &req)

	got := 0
	for range chunks {
		got++
	}
	err := <-errs
	var apiErr *models.APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("stream ended with %v after %d chunks, want the upstream's error", err, got)
	}
	if apiErr.Message != "upstream exploded" || apiErr.Status < http.StatusInternalServerError {
		t.Errorf("error = %d %q, want a 5xx carrying %q", apiErr.Status, apiErr.Message, "upstream exploded")
	}
	if got == 0 {
		t.Error("the chunks before the error were not delivered")
	}
}
