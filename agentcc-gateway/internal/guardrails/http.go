package guardrails

import (
	"net"
	"net/http"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/netguard"
)

// TenantHTTPClient guards actual connections, including DNS changes and redirects.
// Do not use environment proxies: they would resolve tenant destinations outside the guard.
func TenantHTTPClient(timeout time.Duration, allowPrivate bool) *http.Client {
	return &http.Client{
		Timeout: timeout,
		Transport: &http.Transport{
			DialContext:           netguard.DialContext(net.Dialer{Timeout: timeout, KeepAlive: 30 * time.Second}, allowPrivate),
			ForceAttemptHTTP2:     true,
			MaxIdleConns:          100,
			IdleConnTimeout:       90 * time.Second,
			TLSHandshakeTimeout:   10 * time.Second,
			ExpectContinueTimeout: time.Second,
		},
	}
}
