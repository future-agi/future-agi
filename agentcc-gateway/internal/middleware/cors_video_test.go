package middleware

import (
	"slices"
	"testing"
)

// Browser clients of /v1/videos need the idempotency and dialect headers.
func TestDefaultCORSHeadersAllowVideoHeaders(t *testing.T) {
	for _, h := range []string{"Idempotency-Key", "X-AgentCC-Video-Dialect"} {
		if !slices.Contains(defaultCORSHeaders, h) {
			t.Fatal(h)
		}
	}
}
