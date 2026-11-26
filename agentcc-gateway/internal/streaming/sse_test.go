package streaming

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"testing/iotest"
	"time"

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

// readEvents reads every event from a stream, and the error it ended with.
func readEvents(t *testing.T, r io.Reader) ([]Event, error) {
	t.Helper()
	er := NewEventReader(r)
	var events []Event
	for {
		ev, err := er.Next()
		if len(ev.Raw) > 0 {
			events = append(events, ev)
		}
		if err != nil {
			return events, err
		}
	}
}

func TestEventReader_SplitsEventsAndKeepsEveryByte(t *testing.T) {
	stream := ": keep-alive\n\n" +
		"event: message_start\ndata: {\"type\":\"message_start\"}\n\n" +
		"\n" + // a stray blank line belongs to the next event
		"event: message_stop\r\ndata: {\"type\":\"message_stop\"}\r\n\r\n"

	events, err := readEvents(t, strings.NewReader(stream))
	if err != io.EOF {
		t.Fatalf("err = %v, want io.EOF", err)
	}
	if len(events) != 3 {
		t.Fatalf("got %d events, want 3: %q", len(events), events)
	}

	var relayed bytes.Buffer
	for _, ev := range events {
		relayed.Write(ev.Raw)
	}
	if relayed.String() != stream {
		t.Errorf("relayed bytes differ from the stream:\n got %q\nwant %q", relayed.String(), stream)
	}

	if events[1].Name != "message_start" || string(events[1].Data()) != `{"type":"message_start"}` {
		t.Errorf("event 1 = %q / %q", events[1].Name, events[1].Data())
	}
	if events[2].Name != "message_stop" || string(events[2].Data()) != `{"type":"message_stop"}` {
		t.Errorf("CRLF event = %q / %q", events[2].Name, events[2].Data())
	}
}

func TestEventReader_JoinsDataLines(t *testing.T) {
	events, _ := readEvents(t, strings.NewReader("data: {\"a\":\ndata:1}\n\ndata:\ndata: x\n\n"))
	if len(events) != 2 {
		t.Fatalf("got %d events, want 2", len(events))
	}
	if got := string(events[0].Data()); got != "{\"a\":\n1}" {
		t.Errorf("data = %q, want the two lines joined by a newline", got)
	}
	if got := string(events[1].Data()); got != "\nx" {
		t.Errorf("data = %q, want an empty first line kept", got)
	}
}

// A stream cut mid-event hands back what arrived, with the read error, so the
// relay can forward it and then report the failure.
func TestEventReader_ReturnsThePartialEventWithTheError(t *testing.T) {
	cut := errors.New("connection reset")
	r := io.MultiReader(strings.NewReader("data: one\n\ndata: tw"), &failingReader{err: cut})

	events, err := readEvents(t, r)
	if !errors.Is(err, cut) {
		t.Fatalf("err = %v, want the read error", err)
	}
	if len(events) != 2 || string(events[1].Raw) != "data: tw" {
		t.Fatalf("events = %q, want the whole event, then the partial one", events)
	}
}

type failingReader struct{ err error }

func (r *failingReader) Read([]byte) (int, error) { return 0, r.err }

// The SSE spec ends a line at LF, CRLF or a lone CR. A stream that used lone
// CRs used to read as one line that never ended.
func TestEventReader_AcceptsEveryLineBreak(t *testing.T) {
	streams := map[string]string{
		"CR":    "event: a\rdata: 1\r\revent: b\rdata: 2\r\r",
		"CRLF":  "event: a\r\ndata: 1\r\n\r\nevent: b\r\ndata: 2\r\n\r\n",
		"mixed": "event: a\rdata: 1\r\n\nevent: b\ndata: 2\r\r",
	}
	for name, stream := range streams {
		readers := map[string]func() io.Reader{
			"whole":              func() io.Reader { return strings.NewReader(stream) },
			"one byte at a time": func() io.Reader { return iotest.OneByteReader(strings.NewReader(stream)) },
		}
		for how, reader := range readers {
			t.Run(name+", "+how, func(t *testing.T) {
				events, err := readEvents(t, reader())
				if err != io.EOF {
					t.Fatalf("err = %v, want io.EOF", err)
				}
				var relayed bytes.Buffer
				var got []string
				for _, ev := range events {
					relayed.Write(ev.Raw)
					if ev.Name != "" {
						got = append(got, ev.Name+"="+string(ev.Data()))
					}
				}
				if strings.Join(got, " ") != "a=1 b=2" {
					t.Errorf("events = %q, want a=1 b=2", got)
				}
				if relayed.String() != stream {
					t.Errorf("relayed bytes differ from the stream:\n got %q\nwant %q", relayed.String(), stream)
				}
			})
		}
	}
}

// A CR ends its line at once. Waiting for the next byte, to see whether it is
// a LF, would hold an event that has ended until the next one starts.
func TestEventReader_DoesNotWaitAfterACR(t *testing.T) {
	pr, pw := io.Pipe()
	t.Cleanup(func() { pw.Close() })
	go pw.Write([]byte("data: 1\r\r"))

	got := make(chan Event, 1)
	go func() {
		ev, _ := NewEventReader(pr).Next()
		got <- ev
	}()
	select {
	case ev := <-got:
		if string(ev.Data()) != "1" {
			t.Errorf("data = %q, want 1", ev.Data())
		}
	case <-time.After(2 * time.Second):
		t.Fatal("the event was held until more bytes came")
	}
}
