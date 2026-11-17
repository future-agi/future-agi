package streaming

import (
	"encoding/json"
	"fmt"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// SSEWriter writes Server-Sent Events to an HTTP response.
type SSEWriter struct {
	w       http.ResponseWriter
	flusher http.Flusher
}

// NewSSEWriter creates a new SSE writer. Returns nil if the ResponseWriter doesn't support flushing.
func NewSSEWriter(w http.ResponseWriter) *SSEWriter {
	flusher, ok := w.(http.Flusher)
	if !ok {
		return nil
	}
	return &SSEWriter{w: w, flusher: flusher}
}

// WriteHeaders sets the SSE response headers. Must be called before any chunks.
func (s *SSEWriter) WriteHeaders() {
	s.w.Header().Set("Content-Type", "text/event-stream")
	s.w.Header().Set("Cache-Control", "no-cache")
	s.w.Header().Set("Connection", "keep-alive")
	s.w.WriteHeader(http.StatusOK)
	s.flusher.Flush()
}

// WriteChunk writes a single SSE chunk and flushes.
func (s *SSEWriter) WriteChunk(chunk models.StreamChunk) error {
	data, err := json.Marshal(chunk)
	if err != nil {
		return fmt.Errorf("marshaling chunk: %w", err)
	}

	if _, err := fmt.Fprintf(s.w, "data: %s\n\n", data); err != nil {
		return fmt.Errorf("writing chunk: %w", err)
	}

	s.flusher.Flush()
	return nil
}

// WriteDone writes the [DONE] sentinel and flushes.
func (s *SSEWriter) WriteDone() error {
	if _, err := fmt.Fprint(s.w, "data: [DONE]\n\n"); err != nil {
		return err
	}
	s.flusher.Flush()
	return nil
}

// WriteRaw writes pre-serialized JSON as an SSE data event and flushes.
func (s *SSEWriter) WriteRaw(data []byte) error {
	if _, err := fmt.Fprintf(s.w, "data: %s\n\n", data); err != nil {
		return fmt.Errorf("writing raw data: %w", err)
	}
	s.flusher.Flush()
	return nil
}

// WriteError writes an error as an SSE event and flushes.
func (s *SSEWriter) WriteError(apiErr *models.APIError) error {
	errResp := models.ErrorResponse{
		Error: models.ErrorDetail{
			Message: apiErr.Message,
			Type:    apiErr.Type,
			Code:    apiErr.Code,
		},
	}
	data, err := json.Marshal(errResp)
	if err != nil {
		return err
	}

	if _, err := fmt.Fprintf(s.w, "data: %s\n\n", data); err != nil {
		return err
	}
	s.flusher.Flush()
	return nil
}
