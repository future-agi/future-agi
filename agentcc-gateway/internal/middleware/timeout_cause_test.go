package middleware

import (
	"context"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// When the client's own timeout header ends a request, the request's context
// says so through its cause, so the gateway can tell the request's own time
// limit from its ceiling. The context still ends with DeadlineExceeded, and
// the cause reads as one, so nothing that checks the deadline decides
// differently.
func TestTimeoutNamesTheClientsOwnLimitAsTheCause(t *testing.T) {
	cases := []struct {
		name        string
		header, val string
		ceiling     time.Duration
		ownLimit    bool
	}{
		{"the request-timeout header, below the ceiling", "x-agentcc-request-timeout", "40", time.Minute, true},
		{"the timeout header, below the ceiling", "x-agentcc-timeout", "40ms", time.Minute, true},
		{"no header: the ceiling", "", "", 40 * time.Millisecond, false},
		{"a header above the ceiling: the ceiling", "x-agentcc-request-timeout", "60000", 40 * time.Millisecond, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var err, cause error
			h := Timeout(tc.ceiling)(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) {
				select {
				case <-r.Context().Done():
				case <-time.After(5 * time.Second):
				}
				err, cause = r.Context().Err(), context.Cause(r.Context())
			}))
			req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil)
			if tc.header != "" {
				req.Header.Set(tc.header, tc.val)
			}
			h.ServeHTTP(httptest.NewRecorder(), req)

			if !errors.Is(err, context.DeadlineExceeded) {
				t.Fatalf("context error = %v, want DeadlineExceeded", err)
			}
			if !errors.Is(cause, context.DeadlineExceeded) || cause.Error() != context.DeadlineExceeded.Error() {
				t.Fatalf("cause = %v, want one that reads as DeadlineExceeded", cause)
			}
			var netErr net.Error
			if !errors.As(cause, &netErr) || !netErr.Timeout() {
				t.Fatalf("cause = %v, want a timeout to net.Error", cause)
			}
			if own := cause != context.DeadlineExceeded; own != tc.ownLimit {
				t.Fatalf("cause names the client's own limit = %v, want %v", own, tc.ownLimit)
			}
		})
	}
}
