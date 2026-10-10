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

// Timeout creates a context.WithTimeout for each request.
// Uses the request's timeout header if set (see RequestTimeoutHeader), otherwise the default.
// Paths listed in skipPaths are excluded because they manage their own timeouts
// (e.g. /v1/chat/completions uses per-model timeouts).
func Timeout(defaultTimeout time.Duration, skipPaths ...string) func(http.Handler) http.Handler {
	skip := make(map[string]struct{}, len(skipPaths))
	for _, p := range skipPaths {
		skip[p] = struct{}{}
	}

	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if _, ok := skip[r.URL.Path]; ok {
				next.ServeHTTP(w, r)
				return
			}

			timeout := defaultTimeout
			if d, ok := RequestTimeoutHeader(r); ok {
				timeout = d
			}

			ctx, cancel := context.WithTimeout(r.Context(), timeout)
			defer cancel()

			next.ServeHTTP(w, r.WithContext(ctx))
		})
	}
}
