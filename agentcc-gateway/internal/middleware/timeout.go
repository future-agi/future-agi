package middleware

import (
	"context"
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

			ctx, cancel := context.WithTimeout(r.Context(), timeout)
			defer cancel()

			next.ServeHTTP(w, r.WithContext(ctx))
		})
	}
}
