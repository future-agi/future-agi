package wire

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/mcp/contentpolicy"
)

// ErrNoAnswer is a stream that ended without the answer to its request.
var ErrNoAnswer = errors.New("the upstream's stream ended without an answer")

// errorBodyLimit bounds what is read of a non-2xx answer.
const errorBodyLimit = 64 << 10

// Exchange is one JSON-RPC message POSTed to an upstream.
type Exchange struct {
	Client *http.Client
	URL    string
	Header http.Header // every request header, Accept and Content-Type included
	Body   []byte
	// ID is the request's raw JSON id, or nil for a notification.
	ID json.RawMessage
	// MaxBytes bounds the answer: the JSON body, or each event of an SSE
	// answer, so a long call's progress does not count against its result
	// (<= 0: no limit).
	MaxBytes int64
	// OnMessage receives every other message on an SSE answer: the
	// upstream's notifications and requests, raw.
	OnMessage func(raw []byte)
}

// Reply is what the upstream answered.
type Reply struct {
	Status int
	Header http.Header
	// Message is the JSON-RPC response to the request (a 2xx answer), or
	// the error body of a non-2xx answer when it is one.
	Message []byte
}

// Post sends the exchange and reads the answer: a JSON body or an SSE
// stream, on which every message before the response goes to OnMessage. A
// 202 or 204 has no message. An answer over MaxBytes is ErrTooLarge.
func Post(ctx context.Context, ex Exchange) (Reply, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, ex.URL, bytes.NewReader(ex.Body))
	if err != nil {
		return Reply{}, fmt.Errorf("create request: %w", err)
	}
	req.Header = ex.Header.Clone()
	resp, err := ex.Client.Do(req)
	if err != nil {
		return Reply{}, err
	}
	defer resp.Body.Close()
	rep := Reply{Status: resp.StatusCode, Header: resp.Header}
	switch {
	case resp.StatusCode == http.StatusAccepted || resp.StatusCode == http.StatusNoContent:
		return rep, nil
	case resp.StatusCode/100 != 2:
		body, readErr := readContent(resp.Body, min(contentpolicy.ByteLimit(ex.MaxBytes), errorBodyLimit))
		if readErr == nil {
			rep.Message = body
		}
		return rep, nil
	case IsEventStream(resp.Header):
		rep.Message, err = answerOnStream(resp.Body, ex)
		return rep, err
	}
	rep.Message, err = readContent(resp.Body, ex.MaxBytes)
	return rep, err
}

// IsEventStream reports whether h's Content-Type is text/event-stream.
func IsEventStream(h http.Header) bool {
	mt, _, err := mime.ParseMediaType(h.Get("Content-Type"))
	return err == nil && mt == "text/event-stream"
}

// answerOnStream reads events until the response to ex.ID.
func answerOnStream(body io.Reader, ex Exchange) ([]byte, error) {
	events := NewReader(body, contentpolicy.ByteLimit(ex.MaxBytes))
	for {
		ev, err := events.Next()
		if errors.Is(err, io.EOF) {
			return nil, ErrNoAnswer
		}
		if err != nil {
			return nil, err
		}
		if (ev.Name != "" && ev.Name != "message") || len(ev.Data) == 0 {
			continue
		}
		if err := checkContent(ev.Data, ex.MaxBytes); err != nil {
			return nil, err
		}
		if IsResponseTo(ev.Data, ex.ID) {
			return ev.Data, nil
		}
		if ex.OnMessage != nil {
			ex.OnMessage(ev.Data)
		}
	}
}

// IsResponseTo reports whether raw is a JSON-RPC response (no method) whose
// id is id.
func IsResponseTo(raw []byte, id json.RawMessage) bool {
	if len(id) == 0 {
		return false
	}
	var m struct {
		ID     json.RawMessage `json:"id"`
		Method string          `json:"method"`
	}
	if json.Unmarshal(raw, &m) != nil || m.Method != "" {
		return false
	}
	return bytes.Equal(bytes.TrimSpace(m.ID), bytes.TrimSpace(id))
}
