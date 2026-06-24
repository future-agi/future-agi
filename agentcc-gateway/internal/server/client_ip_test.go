package server

import (
	"net/http/httptest"
	"testing"
)

func TestExtractClientIP(t *testing.T) {
	const peer = "203.0.113.10:54321" // socket peer (untrusted client)

	tests := []struct {
		name           string
		trustedProxies int
		xff            string
		xRealIP        string
		want           string
	}{
		{
			name:           "no trusted proxies ignores spoofed XFF",
			trustedProxies: 0,
			xff:            "10.0.0.5",
			want:           "203.0.113.10",
		},
		{
			name:           "no trusted proxies ignores spoofed X-Real-IP",
			trustedProxies: 0,
			xRealIP:        "10.0.0.5",
			want:           "203.0.113.10",
		},
		{
			name:           "one trusted proxy uses client appended before it",
			trustedProxies: 1,
			xff:            "198.51.100.7, 203.0.113.10",
			want:           "198.51.100.7",
		},
		{
			name:           "spoofed left entries cannot beat the trusted hop count",
			trustedProxies: 1,
			// Attacker injects 9.9.9.9; real client is 198.51.100.7, proxy appended last.
			xff:  "9.9.9.9, 198.51.100.7, 203.0.113.10",
			want: "198.51.100.7",
		},
		{
			name:           "two trusted proxies",
			trustedProxies: 2,
			xff:            "198.51.100.7, 203.0.113.10, 203.0.113.11",
			want:           "198.51.100.7",
		},
		{
			name:           "fewer entries than trusted hops falls back to leftmost",
			trustedProxies: 3,
			xff:            "198.51.100.7",
			want:           "198.51.100.7",
		},
		{
			name:           "trusted proxy with X-Real-IP only",
			trustedProxies: 1,
			xRealIP:        "198.51.100.7",
			want:           "198.51.100.7",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			h := &Handlers{trustedProxies: tt.trustedProxies}
			req := httptest.NewRequest("POST", "/v1/chat/completions", nil)
			req.RemoteAddr = peer
			if tt.xff != "" {
				req.Header.Set("X-Forwarded-For", tt.xff)
			}
			if tt.xRealIP != "" {
				req.Header.Set("X-Real-IP", tt.xRealIP)
			}
			if got := h.extractClientIP(req); got != tt.want {
				t.Errorf("extractClientIP() = %q, want %q", got, tt.want)
			}
		})
	}
}
