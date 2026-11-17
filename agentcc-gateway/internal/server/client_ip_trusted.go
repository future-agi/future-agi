package server

import (
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/middleware"
)

// trustedClientIP returns the client address used for network access checks
// with trustedProxies hops in front of the gateway: the rule the video routes
// read ip_acl.trusted_proxies with (middleware.HopsClientIP, which the
// resolver applies before any handler runs).
func trustedClientIP(r *http.Request, trustedProxies int) string {
	return middleware.HopsClientIP(r, trustedProxies)
}
