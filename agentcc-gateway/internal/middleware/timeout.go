package middleware

import (
	"context"
	"log/slog"
	"net/http"
	"strconv"
	"time"
)

// RequestTimeoutHeader reads the timeout a client asked for on this request.
// x-agentcc-timeout takes a Go duration ("30s", "500ms"); x-agentcc-request-timeout
// takes integer milliseconds. Every route accepts both, and the duration header
// wins when both are valid. A value that does not parse is ignored; the
// x-agentcc-timeout-ms response header shows the timeout that was applied.
func RequestTimeoutHeader(r *http.Request) (time.Duration, bool) {
	if v := r.Header.Get("x-agentcc-timeout"); v != "" {
		if d, err := time.ParseDuration(v); err == nil && d > 0 {
			return d, true
		}
	}
	if v := r.Header.Get("x-agentcc-request-timeout"); v != "" {
		if ms, err := strconv.ParseInt(v, 10, 64); err == nil && ms > 0 {
			return time.Duration(ms) * time.Millisecond, true
		}
	}
	return 0, false
}

// Timeout bounds every request at maxTimeout, the one ceiling the gateway
// enforces, and at the client's own header when that asks for less.
//
// It deliberately does not use the server default: a handler that knows the
// model resolves its own timeout within this envelope (see
// Handlers.resolveTimeout), and that timeout is already clamped to the same
// maximum. An envelope at the default would silently truncate a longer
// per-model timeout, which is what it used to do on every route except
// /v1/chat/completions.
func Timeout(maxTimeout time.Duration) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			timeout := maxTimeout
			if d, ok := RequestTimeoutHeader(r); ok && d < timeout {
				timeout = d
			}

			setWriteDeadline(w, timeout)

			ctx, cancel := context.WithTimeout(r.Context(), timeout)
			defer cancel()

			next.ServeHTTP(w, r.WithContext(ctx))
		})
	}
}

// writeGrace is how long after the request's own deadline the response may
// still be written. It only has to cover the last write: the deadline above
// ends the handler, which then writes its 504.
const writeGrace = 10 * time.Second

// setWriteDeadline moves the response write deadline past this request's own
// deadline, so the deadline above is what ends the request.
//
// server.write_timeout arms a write deadline when the request headers arrive,
// and nothing resets it. A response that takes longer than that cannot be
// written at all — not the body, and not the error either. The handler keeps
// running to the end, bills the upstream call it completed, and the client
// gets a closed connection with no status. Holding the deadline here keeps
// write_timeout as what it reads like, a default for an idle or slow client,
// and leaves the ceiling as the only limit that ends a request.
//
// A ResponseWriter that cannot carry a deadline is left alone: the request
// then still ends at its own deadline, and only the write stays bounded by
// write_timeout, which is the behaviour before this.
func setWriteDeadline(w http.ResponseWriter, timeout time.Duration) {
	if err := http.NewResponseController(w).SetWriteDeadline(time.Now().Add(timeout + writeGrace)); err != nil {
		slog.Debug("could not hold the response write deadline; server.write_timeout still bounds the response",
			"error", err, "timeout", timeout.String())
	}
}
