package anthropic

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
		io.WriteString(w, "event: message_start\ndata: {\"type\":\"message_start\",\"message\":{\"id\":\"msg_1\",\"type\":\"message\",\"role\":\"assistant\",\"model\":\"claude-sonnet-5-5\",\"content\":[],\"usage\":{\"input_tokens\":5,\"output_tokens\":1}}}\n\nevent: content_block_start\ndata: {\"type\":\"content_block_start\",\"index\":0,\"content_block\":{\"type\":\"text\",\"text\":\"\"}}\n\nevent: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"text_delta\",\"text\":\"hi\"}}\n\nevent: error\ndata: {\"type\":\"error\",\"error\":{\"type\":\"overloaded_error\",\"message\":\"Overloaded\"}}\n\n")
	}))
	defer ts.Close()

	var req models.ChatCompletionRequest
	if err := json.Unmarshal([]byte(`{"model":"claude-sonnet-5-5","stream":true,"messages":[{"role":"user","content":"hi"}]}`), &req); err != nil {
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
	if apiErr.Message != "Overloaded" || apiErr.Status < http.StatusInternalServerError {
		t.Errorf("error = %d %q, want a 5xx carrying %q", apiErr.Status, apiErr.Message, "Overloaded")
	}
	if got == 0 {
		t.Error("the chunks before the error were not delivered")
	}
}
