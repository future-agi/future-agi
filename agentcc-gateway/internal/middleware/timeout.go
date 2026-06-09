package middleware

import (
	"context"
	"net/http"
	"strconv"
	"time"
)

// Timeout creates a context.WithTimeout for each request.
// Uses the x-agentcc-request-timeout header (milliseconds) if set, otherwise uses the default.
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

			if h := r.Header.Get("x-agentcc-request-timeout"); h != "" {
				if ms, err := strconv.ParseInt(h, 10, 64); err == nil && ms > 0 {
					timeout = time.Duration(ms) * time.Millisecond
				}
			}

			ctx, cancel := context.WithTimeout(r.Context(), timeout)
			defer cancel()

			next.ServeHTTP(w, r.WithContext(ctx))
		})
	}
}
