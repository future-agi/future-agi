package server

import (
	"bytes"
	"context"
	"fmt"
	"log/slog"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// A stream that a blocking check on the answer reads is held.
// A check on the answer reads the whole answer, which a stream has only once
// it ends, so the stream's body waits until the check has passed. When such a
// check applies (rc.Flags.AnswerBlockers, or a streaming checker that stops
// streams), the handler writes the stream into a heldStream: the response's
// status and headers go out at once, its body waits. Once the stream has
// ended and the post-plugins have run on the assembled answer, the handler
// settles it: a passing answer is written frame for frame as the provider
// sent it; a blocked one is dropped, and the client gets only the route's
// error event.
// A request with no blocking check on the answer is never held.

// maxHeldStreamBytes bounds what a held stream buffers: 10 MiB, what the
// gateway reads of a provider's non-streamed answer (each provider reads one
// through io.LimitReader(resp.Body, 10*1024*1024)). A held stream over it is
// refused with the guardrail's error.
const maxHeldStreamBytes = 10 << 20

// The reasons a held stream gives when the checks could not read its whole
// answer (markAnswerUnreadableBecause). The guardrail plugin puts them in its
// refusal, 403 answer_unavailable naming the check.
var (
	answerOverHoldBound = fmt.Sprintf("the streamed answer is over the %d MiB the gateway holds for the checks that read it", maxHeldStreamBytes>>20)
	answerPartlyRead    = "part of the streamed answer could not be assembled for the checks that read it"
)

// heldStream is the client's http.ResponseWriter for a held stream. Header
// and WriteHeader pass through. Write buffers the body, up to
// maxHeldStreamBytes; past it the buffer is dropped and the stream counts as
// over its bound. Flush passes through while nothing is buffered, so the
// headers go out at once, as on a stream that is not held. release writes the
// buffer and lets later writes through; discard drops it.
type heldStream struct {
	w       http.ResponseWriter
	flusher http.Flusher
	buf     bytes.Buffer
	holding bool
	over    bool
}

// newHeldStream holds w's body, or returns nil when w cannot flush (the
// handler then answers as it does for any stream on such a writer).
func newHeldStream(w http.ResponseWriter) *heldStream {
	flusher, ok := w.(http.Flusher)
	if !ok {
		return nil
	}
	return &heldStream{w: w, flusher: flusher, holding: true}
}

func (s *heldStream) Header() http.Header { return s.w.Header() }

func (s *heldStream) WriteHeader(code int) { s.w.WriteHeader(code) }

func (s *heldStream) Write(p []byte) (int, error) {
	if !s.holding {
		return s.w.Write(p)
	}
	if s.over {
		return len(p), nil
	}
	if s.buf.Len()+len(p) > maxHeldStreamBytes {
		s.over = true
		s.buf = bytes.Buffer{}
		return len(p), nil
	}
	return s.buf.Write(p)
}

func (s *heldStream) Flush() {
	if !s.holding || s.buf.Len() == 0 {
		s.flusher.Flush()
	}
}

// release writes what the stream holds to the client and lets later writes
// through. Safe on nil.
func (s *heldStream) release() {
	if s == nil || !s.holding {
		return
	}
	s.holding = false
	if s.buf.Len() > 0 {
		if _, err := s.w.Write(s.buf.Bytes()); err != nil {
			slog.Warn("error writing a held stream", "error", err)
		}
		s.buf = bytes.Buffer{}
	}
	s.flusher.Flush()
}

// discard drops what the stream holds and lets later writes through. Safe on
// nil.
func (s *heldStream) discard() {
	if s == nil {
		return
	}
	s.holding = false
	s.buf = bytes.Buffer{}
}

// beforeChecks is called right before the post-plugins run on a held
// stream's answer: when the stream went over its bound, or read is false
// (the assembly the checks read is not the whole answer: a choice past
// maxStreamCaptureChoices, or a line past maxSSELineBytes), the checks
// cannot read what the client would get, and the guardrail plugin refuses it
// (answer_unavailable). Safe on nil.
func (s *heldStream) beforeChecks(rc *models.RequestContext, read bool) {
	if s == nil {
		return
	}
	switch {
	case s.over:
		markAnswerUnreadableBecause(rc, answerOverHoldBound)
	case !read:
		markAnswerUnreadableBecause(rc, answerPartlyRead)
	}
}

// settle ends a held stream once its post-plugins have run: when an output
// check withheld the answer it drops what the stream holds and sends the
// guardrail's error, frame(err) in the route's own format; otherwise it
// releases what the stream holds, as it came. With held nil (a stream that
// is not held) it does nothing. It reports whether the answer was withheld.
func settleHeldStream(held *heldStream, rc *models.RequestContext, frame func(*models.APIError) []byte) (withheld bool) {
	if held == nil {
		return false
	}
	apiErr := withheldAnswerError(rc)
	if apiErr == nil {
		held.release()
		return false
	}
	held.discard()
	if _, err := held.w.Write(frame(apiErr)); err != nil {
		slog.Warn("error writing the guardrail's stream error event", "request_id", rc.RequestID, "error", err)
		return true
	}
	held.flusher.Flush()
	return true
}

// endStream ends a stream that ended normally, once its post-plugins have
// run: a held stream is settled (settleHeldStream); one that is not held
// ends with the guardrail's error event when an output check refused the
// answer (endWithheldStream).
func endStream(w http.ResponseWriter, flusher http.Flusher, held *heldStream, rc *models.RequestContext, frame func(*models.APIError) []byte) {
	if held != nil {
		settleHeldStream(held, rc, frame)
		return
	}
	endWithheldStream(w, flusher, rc, frame)
}

// checksContext is the context a stream's post-plugins run on. For a held
// stream it is always one the request's end does not cancel, with ctx's
// values: the request's deadline, or a client gone, may end the request
// before the checks start or while one is still running, and the checks
// that decide whether a held answer goes out run to their end, each within
// its own timeout. A stream that is not held runs them on ctx, or on a
// background context when detach is set (the request's context ended in the
// stream loop).
func checksContext(ctx context.Context, held *heldStream, detach bool) context.Context {
	switch {
	case held != nil:
		return context.WithoutCancel(ctx)
	case detach:
		return context.Background()
	}
	return ctx
}

// holdsTheStream says whether a stream handler holds rc's stream: a check on
// the answer that would block it applies (rc.Flags.AnswerBlockers).
func holdsTheStream(rc *models.RequestContext) bool {
	return rc.Flags.AnswerBlockers != 0
}
