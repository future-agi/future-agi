package mock

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"sync/atomic"
	"unicode/utf8"
)

// sseStream writes server-sent events, waiting chunk_delay between them and
// dropping the connection after stream_abort_after events. It meters the
// output each event carries for the call's charge (streamCharge).
type sseStream struct {
	w          http.ResponseWriter
	flusher    http.Flusher
	ctx        context.Context
	delayMs    int
	abortAfter int
	sent       int
	charge     *streamCharge // nil when the stream is not charged
}

func startSSE(w http.ResponseWriter, r *http.Request, resp *Response) *sseStream {
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.WriteHeader(http.StatusOK)
	f, _ := w.(http.Flusher)
	charge, _ := r.Context().Value(streamChargeKey{}).(*streamCharge)
	return &sseStream{w: w, flusher: f, ctx: r.Context(), delayMs: resp.ChunkDelayMs, abortAfter: resp.AbortAfter, charge: charge}
}

// send writes one event; event "" writes a data-only event. It returns false
// once the client has gone, so the caller stops.
func (s *sseStream) send(event string, payload any) bool {
	b, err := json.Marshal(payload)
	if err != nil {
		panic(fmt.Sprintf("mock: unencodable stream event: %v", err))
	}
	return s.write(event, string(b), payload)
}

// done ends an OpenAI-style stream with the [DONE] sentinel.
func (s *sseStream) done() { s.write("", "[DONE]", nil) }

func (s *sseStream) write(event, data string, payload any) bool {
	if s.sent > 0 && !sleepCtx(s.ctx, s.delayMs) {
		return false
	}
	if s.abortAfter > 0 && s.sent >= s.abortAfter {
		// Ends the handler without the chunked terminator: the client sees the
		// connection drop mid-stream, as when an upstream dies.
		panic(http.ErrAbortHandler)
	}
	if event != "" {
		fmt.Fprintf(s.w, "event: %s\n", event)
	}
	fmt.Fprintf(s.w, "data: %s\n\n", data)
	if s.flusher != nil {
		s.flusher.Flush()
	}
	s.sent++
	s.charge.sentEvent(payload)
	return s.ctx.Err() == nil
}

// streamChargeKey carries a call's *streamCharge on its request context from
// the handler that records the charge (server.go) to the stream writer the
// renderers start.
type streamChargeKey struct{}

// streamCharge charges a stream for the output it sent. A stream is charged
// once: in full as soon as all its output is out (before its closing events,
// so a client that read the whole stream finds the charge recorded), or, when
// stream_abort_after or a departed client cuts it short, for the output it
// sent by then. Output is measured in runes of the text the events carry:
// the content deltas and the tool-call argument deltas.
type streamCharge struct {
	total, sent int // output runes: of the whole answer, and in the events sent
	record      func(sent, total int)
	charged     bool
}

func newStreamCharge(resp *Response, record func(sent, total int)) *streamCharge {
	total := utf8.RuneCountInString(resp.Content)
	for _, tc := range resp.ToolCalls {
		total += utf8.RuneCountInString(tc.Arguments)
	}
	return &streamCharge{total: total, record: record}
}

// sentEvent counts the output in an event just written.
func (c *streamCharge) sentEvent(payload any) {
	if c == nil || c.charged {
		return
	}
	c.sent += outputRunes(payload)
	if c.sent >= c.total {
		c.settle(c.total)
	}
}

// end charges a stream not charged yet: in full when it completed, else for
// the output it sent.
func (c *streamCharge) end(completed bool) {
	if c.charged {
		return
	}
	if completed {
		c.settle(c.total)
		return
	}
	c.settle(c.sent)
}

func (c *streamCharge) settle(sent int) {
	c.charged = true
	c.record(min(sent, c.total), c.total)
}

// outputRunes counts the output text in one stream event, in the shapes the
// renderers send: chat choices[].delta.content and
// choices[].delta.tool_calls[].function.arguments; Anthropic
// delta.text and delta.partial_json; and the Responses API's string delta.
func outputRunes(payload any) int {
	p, _ := payload.(map[string]any)
	n := 0
	switch d := p["delta"].(type) {
	case string:
		n += utf8.RuneCountInString(d)
	case map[string]any:
		n += stringRunes(d["text"]) + stringRunes(d["partial_json"])
	}
	choices, _ := p["choices"].([]any)
	for _, c := range choices {
		choice, _ := c.(map[string]any)
		delta, _ := choice["delta"].(map[string]any)
		n += stringRunes(delta["content"])
		calls, _ := delta["tool_calls"].([]any)
		for _, tc := range calls {
			call, _ := tc.(map[string]any)
			fn, _ := call["function"].(map[string]any)
			n += stringRunes(fn["arguments"])
		}
	}
	return n
}

func stringRunes(v any) int {
	s, _ := v.(string)
	return utf8.RuneCountInString(s)
}

// splitPieces cuts text into at most n non-empty pieces of about equal rune
// length; it never splits a rune.
func splitPieces(text string, n int) []string {
	runes := utf8.RuneCountInString(text)
	if runes == 0 {
		return nil
	}
	n = max(1, min(n, runes))
	pieces := make([]string, 0, n)
	r := []rune(text)
	for i := 0; i < n; i++ {
		pieces = append(pieces, string(r[i*runes/n:(i+1)*runes/n]))
	}
	return pieces
}

var idSeq atomic.Int64

// nextID returns a unique id with prefix, such as "gen-mock-7".
func nextID(prefix string) string { return fmt.Sprintf("%s%d", prefix, idSeq.Add(1)) }
