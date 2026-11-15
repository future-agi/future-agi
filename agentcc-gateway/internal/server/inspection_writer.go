package server

import (
	"bufio"
	"errors"
	"io"
	"net"
	"net/http"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

var errInspectionRelease = errors.New("response release refused by inspection boundary")

// inspectionWriter never collects provider content. It checks the request-local
// decision before headers, each write, flush and upgrade, and writes a small
// gateway error directly to the underlying writer on refusal.
type inspectionWriter struct {
	dst         http.ResponseWriter
	state       *models.InspectionState
	wrote       bool
	blocked     bool
	errorBody   bool // one write by the trusted, sanitized gateway error encoder
	errorStatus int
}

func (w *inspectionWriter) Header() http.Header { return w.dst.Header() }

func (w *inspectionWriter) permit() bool {
	if w.blocked {
		return false
	}
	if err := w.state.ReleaseError(); err != nil {
		w.blocked = true
		if !w.wrote {
			clear(w.dst.Header())
			models.WriteError(w.dst, err)
			w.wrote = true
		}
		return false
	}
	return true
}

func (w *inspectionWriter) WriteHeader(status int) {
	if w.errorBody && !w.blocked && status == w.errorStatus {
		w.dst.WriteHeader(status)
		w.wrote = true
		return
	}
	if !w.permit() {
		return
	}
	w.dst.WriteHeader(status)
	if status >= 200 || status == http.StatusSwitchingProtocols {
		w.wrote = true
	}
}

func (w *inspectionWriter) Write(p []byte) (int, error) {
	if w.errorBody && !w.blocked {
		w.errorBody, w.blocked, w.wrote = false, true, true
		return w.dst.Write(p)
	}
	if !w.permit() {
		return 0, errInspectionRelease
	}
	w.wrote = true
	return w.dst.Write(p)
}

// ReadFrom must not delegate to the destination's optimized path, which could
// otherwise copy content without calling Write and checking the decision.
func (w *inspectionWriter) ReadFrom(r io.Reader) (int64, error) {
	return io.Copy(struct{ io.Writer }{w}, r)
}

func (w *inspectionWriter) finish() {
	if !w.wrote {
		w.permit()
	}
}

// GuardrailError is called only by the gateway error serializer, not by status
// codes. A raw upstream 4xx/5xx is still content and cannot bypass permit. When
// inspection is required, permit exactly one sanitized JSON encoder write and
// suppress subsequent content. Keeping serialization in the gateway formatter
// preserves native error envelopes without buffering any upstream body.
func (w *inspectionWriter) GuardrailError(err *models.APIError) *models.APIError {
	if !w.state.Required(models.InspectionInput) && !w.state.Required(models.InspectionOutput) {
		return err
	}
	safe := safeInspectionError(err)
	if !w.wrote && !w.blocked {
		clear(w.dst.Header())
		w.errorBody, w.errorStatus = true, safe.Status
	} else {
		w.blocked = true
	}
	return safe
}

func safeInspectionError(err *models.APIError) *models.APIError {
	status := err.Status
	if status < 400 || status > 599 {
		status = http.StatusInternalServerError
	}
	// Do not echo arbitrary upstream code/type/message/param/header strings.
	safe := &models.APIError{Status: status, Type: models.ErrTypeServer, Code: "request_failed", Message: "Request failed before inspected content could be released"}
	switch err.Code {
	case "input_guardrail_unsupported", "output_guardrail_unsupported", "stream_guardrail_unsupported", "content_blocked", "guardrail_unavailable":
		safe.Code = err.Code
		safe.Type = models.ErrTypeGuardrail
		safe.Message = err.Code + ": request refused by guardrail policy"
	case "invalid_api_key":
		safe.Code, safe.Type, safe.Message = err.Code, models.ErrTypeAuthentication, "Invalid or missing API key"
	case "permission_denied":
		safe.Code, safe.Type, safe.Message = err.Code, models.ErrTypePermission, "Permission denied"
	case "streaming_not_supported":
		// Preserve the legacy native-completion capability error, but not its
		// message (which can contain the caller's model or upstream content).
		safe.Code, safe.Message = err.Code, "Streaming is not supported for this operation"
	case "missing_model", "missing_prompt", "missing_input", "missing_voice", "missing_messages", "invalid_json", "invalid_input", "invalid_request", "read_error", "request_too_large":
		// Known gateway validation codes are protocol metadata; retain them,
		// but never the parser message that may quote caller content.
		safe.Code, safe.Type, safe.Message = err.Code, models.ErrTypeInvalidRequest, "Invalid request"
	}
	return safe
}

// ResponseController can use these methods without exposing an Unwrap path
// around the release boundary. Operations that release content stay guarded.
func (w *inspectionWriter) SetReadDeadline(deadline time.Time) error {
	return http.NewResponseController(w.dst).SetReadDeadline(deadline)
}
func (w *inspectionWriter) SetWriteDeadline(deadline time.Time) error {
	return http.NewResponseController(w.dst).SetWriteDeadline(deadline)
}
func (w *inspectionWriter) EnableFullDuplex() error {
	return http.NewResponseController(w.dst).EnableFullDuplex()
}

type inspectionFlusher struct{ w *inspectionWriter }

func (f inspectionFlusher) Flush() { _ = f.FlushError() }
func (f inspectionFlusher) FlushError() error {
	if !f.w.permit() {
		return errInspectionRelease
	}
	f.w.wrote = true
	return http.NewResponseController(f.w.dst).Flush()
}

type inspectionHijacker struct{ w *inspectionWriter }

func (h inspectionHijacker) Hijack() (net.Conn, *bufio.ReadWriter, error) {
	if !h.w.permit() {
		return nil, nil, errInspectionRelease
	}
	conn, rw, err := h.w.dst.(http.Hijacker).Hijack()
	if err == nil {
		h.w.wrote = true
	}
	return conn, rw, err
}

type inspectionPusher struct{ w *inspectionWriter }

func (p inspectionPusher) Push(target string, opts *http.PushOptions) error {
	if !p.w.permit() {
		return errInspectionRelease
	}
	return p.w.dst.(http.Pusher).Push(target, opts)
}

type inspectionCloseNotifier struct{ http.CloseNotifier }

// Preserve exactly the destination's optional HTTP interfaces. Advertising a
// Hijacker/Flusher where none exists changes handler control flow. Embedding only
// the guarded implementations also prevents io.Copy and ResponseController from
// unwrapping to an unchecked destination.
func newInspectionWriter(dst http.ResponseWriter, state *models.InspectionState) (*inspectionWriter, http.ResponseWriter) {
	w := &inspectionWriter{dst: dst, state: state}
	f, h, p := inspectionFlusher{w}, inspectionHijacker{w}, inspectionPusher{w}
	mask := 0
	if _, ok := dst.(http.Flusher); ok {
		mask |= 1
	}
	if _, ok := dst.(http.Hijacker); ok {
		mask |= 2
	}
	if _, ok := dst.(http.Pusher); ok {
		mask |= 4
	}
	cn, ok := dst.(http.CloseNotifier)
	if ok {
		mask |= 8
	}
	c := inspectionCloseNotifier{cn}
	switch mask {
	case 1:
		return w, struct {
			*inspectionWriter
			inspectionFlusher
		}{w, f}
	case 2:
		return w, struct {
			*inspectionWriter
			inspectionHijacker
		}{w, h}
	case 3:
		return w, struct {
			*inspectionWriter
			inspectionFlusher
			inspectionHijacker
		}{w, f, h}
	case 4:
		return w, struct {
			*inspectionWriter
			inspectionPusher
		}{w, p}
	case 5:
		return w, struct {
			*inspectionWriter
			inspectionFlusher
			inspectionPusher
		}{w, f, p}
	case 6:
		return w, struct {
			*inspectionWriter
			inspectionHijacker
			inspectionPusher
		}{w, h, p}
	case 7:
		return w, struct {
			*inspectionWriter
			inspectionFlusher
			inspectionHijacker
			inspectionPusher
		}{w, f, h, p}
	case 8:
		return w, struct {
			*inspectionWriter
			inspectionCloseNotifier
		}{w, c}
	case 9:
		return w, struct {
			*inspectionWriter
			inspectionFlusher
			inspectionCloseNotifier
		}{w, f, c}
	case 10:
		return w, struct {
			*inspectionWriter
			inspectionHijacker
			inspectionCloseNotifier
		}{w, h, c}
	case 11:
		return w, struct {
			*inspectionWriter
			inspectionFlusher
			inspectionHijacker
			inspectionCloseNotifier
		}{w, f, h, c}
	case 12:
		return w, struct {
			*inspectionWriter
			inspectionPusher
			inspectionCloseNotifier
		}{w, p, c}
	case 13:
		return w, struct {
			*inspectionWriter
			inspectionFlusher
			inspectionPusher
			inspectionCloseNotifier
		}{w, f, p, c}
	case 14:
		return w, struct {
			*inspectionWriter
			inspectionHijacker
			inspectionPusher
			inspectionCloseNotifier
		}{w, h, p, c}
	case 15:
		return w, struct {
			*inspectionWriter
			inspectionFlusher
			inspectionHijacker
			inspectionPusher
			inspectionCloseNotifier
		}{w, f, h, p, c}
	default:
		return w, w
	}
}
