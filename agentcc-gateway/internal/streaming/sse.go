package streaming

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
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

// Event is one server-sent event as it arrived. Raw holds every byte of it,
// the blank line that ends it included, so a relay that writes Raw forwards
// the stream unchanged.
type Event struct {
	Raw  []byte
	Name string // the event: field, empty when the event has none
}

// Data is the event's data: lines, joined by newlines. It is read from Raw on
// demand, since a relay looks inside only the few events it acts on.
func (e Event) Data() []byte {
	var data []byte
	hasData := false
	for raw := e.Raw; len(raw) > 0; {
		field := raw
		if i := bytes.IndexAny(raw, "\r\n"); i >= 0 {
			field = raw[:i]
			if raw[i] == '\r' && i+1 < len(raw) && raw[i+1] == '\n' {
				i++
			}
			raw = raw[i+1:]
		} else {
			raw = nil
		}
		if !bytes.HasPrefix(field, []byte("data:")) {
			continue
		}
		if hasData {
			data = append(data, '\n')
		}
		data = append(data, fieldValue(field, "data:")...)
		hasData = true
	}
	return data
}

// MaxEventBytes bounds one event. A relay holds each event whole before it
// writes it, so an upstream that never ends one would grow the buffer until
// the request deadline. The bound is generous because the last event of a
// Responses stream carries the whole response, generated images included.
const MaxEventBytes = 32 << 20

// ErrEventTooLarge ends a stream whose event outgrew MaxEventBytes.
var ErrEventTooLarge = errors.New("stream event larger than 32 MiB")

// EventReader splits a relayed stream into whole events. A relay needs the
// boundaries to act on one event — read usage from it, hold back the last one
// — without parsing or rewriting the others.
type EventReader struct {
	r *bufio.Reader
	// afterCR says the last line ended in a CR that was the last byte read.
	// A LF that follows belongs to that line break.
	afterCR bool
}

// NewEventReader reads events from r.
func NewEventReader(r io.Reader) *EventReader {
	return &EventReader{r: bufio.NewReader(r)}
}

// Next returns the next event. When the stream ends, the event holds whatever
// arrived after the last blank line and the error says how it ended: io.EOF on
// a clean close, the read error otherwise. An event that has not ended when
// the error comes is incomplete, and a client parser would drop it.
func (er *EventReader) Next() (Event, error) {
	var ev Event
	hasField := false
	for {
		lineStart, err := er.readLine(&ev.Raw)
		if len(ev.Raw) > MaxEventBytes {
			return ev, ErrEventTooLarge
		}
		field := bytes.TrimRight(ev.Raw[lineStart:], "\r\n")
		switch {
		case len(field) == 0:
			// A blank line ends an event; blank lines before one stay in its Raw.
			if hasField && err == nil {
				return ev, nil
			}
		case bytes.HasPrefix(field, []byte("event:")):
			ev.Name = string(fieldValue(field, "event:"))
			hasField = true
		default:
			hasField = true
		}
		if err != nil {
			return ev, err
		}
	}
}

// readLine appends the next line, line break included, to raw, and returns
// where the line starts in raw. A line breaks at LF, CRLF or a lone CR, as
// the SSE spec allows. A CR ends its line at once: waiting for the next byte
// to see whether it is a LF would hold back an event that has already ended.
func (er *EventReader) readLine(raw *[]byte) (int, error) {
	if er.afterCR {
		er.afterCR = false
		next, err := er.r.Peek(1)
		if err != nil {
			return len(*raw), err
		}
		if next[0] == '\n' {
			*raw = append(*raw, '\n')
			er.r.Discard(1)
		}
	}
	start := len(*raw)
	for {
		if er.r.Buffered() == 0 {
			if _, err := er.r.Peek(1); err != nil {
				return start, err
			}
		}
		buf, _ := er.r.Peek(er.r.Buffered())
		i := bytes.IndexAny(buf, "\r\n")
		if i < 0 {
			*raw = append(*raw, buf...)
			er.r.Discard(len(buf))
			if len(*raw) > MaxEventBytes {
				return start, ErrEventTooLarge
			}
			continue // the line goes on
		}
		end := i + 1
		if buf[i] == '\r' {
			switch {
			case end < len(buf) && buf[end] == '\n':
				end++
			case end == len(buf):
				er.afterCR = true
			}
		}
		*raw = append(*raw, buf[:end]...)
		er.r.Discard(end)
		return start, nil
	}
}

// fieldValue is a field's value with the one optional space after the colon removed.
func fieldValue(field []byte, name string) []byte {
	return bytes.TrimPrefix(field[len(name):], []byte(" "))
}
