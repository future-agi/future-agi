package streaming

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func strPtr(s string) *string { return &s }

// flushRecorder wraps httptest.ResponseRecorder and implements http.Flusher.
type flushRecorder struct {
	*httptest.ResponseRecorder
	flushed int
}

func (f *flushRecorder) Flush() {
	f.flushed++
}

func newFlushRecorder() *flushRecorder {
	return &flushRecorder{ResponseRecorder: httptest.NewRecorder()}
}

func TestNewSSEWriter_WithFlusher(t *testing.T) {
	w := newFlushRecorder()
	writer := NewSSEWriter(w)
	if writer == nil {
		t.Fatal("expected non-nil SSEWriter")
	}
}

func TestNewSSEWriter_WithoutFlusher(t *testing.T) {
	// httptest.ResponseRecorder without our Flusher wrapper
	w := httptest.NewRecorder()
	// httptest.ResponseRecorder implements http.Flusher, so we need a custom type that doesn't.
	type nonFlusher struct {
		http.ResponseWriter
	}
	nf := nonFlusher{w}
	writer := NewSSEWriter(nf)
	if writer != nil {
		t.Fatal("expected nil SSEWriter for non-flusher")
	}
}

func TestSSEWriter_WriteHeaders(t *testing.T) {
	w := newFlushRecorder()
	writer := NewSSEWriter(w)
	writer.WriteHeaders()

	if ct := w.Header().Get("Content-Type"); ct != "text/event-stream" {
		t.Errorf("expected Content-Type text/event-stream, got %q", ct)
	}
	if cc := w.Header().Get("Cache-Control"); cc != "no-cache" {
		t.Errorf("expected Cache-Control no-cache, got %q", cc)
	}
	if w.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d", w.Code)
	}
	if w.flushed < 1 {
		t.Error("expected Flush to be called")
	}
}

func TestSSEWriter_WriteChunk(t *testing.T) {
	w := newFlushRecorder()
	writer := NewSSEWriter(w)
	writer.WriteHeaders()

	chunk := models.StreamChunk{
		ID:      "chatcmpl-123",
		Object:  "chat.completion.chunk",
		Created: 1234567890,
		Model:   "gpt-4o",
		Choices: []models.StreamChoice{
			{
				Index: 0,
				Delta: models.Delta{
					Content: strPtr("Hello"),
				},
			},
		},
	}

	err := writer.WriteChunk(chunk)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	body := w.Body.String()
	if !strings.Contains(body, "data: ") {
		t.Error("expected SSE data prefix")
	}
	if !strings.Contains(body, "chatcmpl-123") {
		t.Error("expected chunk ID in body")
	}
	if !strings.Contains(body, "Hello") {
		t.Error("expected chunk content in body")
	}
}

func TestSSEWriter_WriteChunkWithAgentccMetadata(t *testing.T) {
	w := newFlushRecorder()
	writer := NewSSEWriter(w)
	writer.WriteHeaders()

	chunk := models.StreamChunk{
		ID:      "chatcmpl-456",
		Object:  "chat.completion.chunk",
		Created: 1234567891,
		Model:   "gpt-4o",
		Choices: []models.StreamChoice{},
		Usage: &models.Usage{
			PromptTokens:     10,
			CompletionTokens: 20,
			TotalTokens:      30,
		},
		AgentccMetadata: &models.AgentccStreamMetadata{
			Cost:      0.0045,
			LatencyMs: 236,
		},
	}

	err := writer.WriteChunk(chunk)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	body := w.Body.String()
	if !strings.Contains(body, `"agentcc_metadata"`) {
		t.Fatalf("expected agentcc_metadata in body, got %q", body)
	}
	if !strings.Contains(body, `"cost":0.0045`) {
		t.Fatalf("expected cost in body, got %q", body)
	}
	if !strings.Contains(body, `"latency_ms":236`) {
		t.Fatalf("expected latency_ms in body, got %q", body)
	}
	if !strings.Contains(body, `"choices":[]`) {
		t.Fatalf("expected empty choices array in body, got %q", body)
	}
}

func TestSSEWriter_WriteDone(t *testing.T) {
	w := newFlushRecorder()
	writer := NewSSEWriter(w)
	writer.WriteHeaders()

	err := writer.WriteDone()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	body := w.Body.String()
	if !strings.Contains(body, "data: [DONE]") {
		t.Errorf("expected [DONE] sentinel, got %q", body)
	}
}

func TestSSEWriter_WriteError(t *testing.T) {
	w := newFlushRecorder()
	writer := NewSSEWriter(w)
	writer.WriteHeaders()

	apiErr := &models.APIError{
		Status:  500,
		Type:    "server_error",
		Code:    "internal_error",
		Message: "something went wrong",
	}

	err := writer.WriteError(apiErr)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	body := w.Body.String()
	if !strings.Contains(body, "data: ") {
		t.Error("expected SSE data prefix")
	}

	// Parse the SSE data line.
	for _, line := range strings.Split(body, "\n") {
		if strings.HasPrefix(line, "data: ") {
			jsonStr := strings.TrimPrefix(line, "data: ")
			if jsonStr == "" {
				continue
			}
			var errResp models.ErrorResponse
			if err := json.Unmarshal([]byte(jsonStr), &errResp); err != nil {
				t.Fatalf("failed to parse error JSON: %v", err)
			}
			if errResp.Error.Message != "something went wrong" {
				t.Errorf("expected error message 'something went wrong', got %q", errResp.Error.Message)
			}
			return
		}
	}
	t.Error("no data line found in SSE output")
}

func TestSSEWriter_MultipleChunksAndDone(t *testing.T) {
	w := newFlushRecorder()
	writer := NewSSEWriter(w)
	writer.WriteHeaders()

	for i := 0; i < 3; i++ {
		chunk := models.StreamChunk{
			ID:    "chunk",
			Model: "test",
		}
		if err := writer.WriteChunk(chunk); err != nil {
			t.Fatalf("chunk %d: %v", i, err)
		}
	}
	if err := writer.WriteDone(); err != nil {
		t.Fatalf("done: %v", err)
	}

	body := w.Body.String()
	count := strings.Count(body, "data: ")
	// 3 chunks + 1 [DONE] = 4 data lines
	if count != 4 {
		t.Errorf("expected 4 data lines, got %d", count)
	}
}
