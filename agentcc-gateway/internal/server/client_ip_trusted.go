package server

import (
	"net"
	"net/http"
	"strings"
)

// trustedClientIP returns the client address used for network access checks.
//
// With trustedProxies = 0 (the default) the address is the TCP peer and
// X-Forwarded-For is ignored, so a caller cannot choose its own address.
//
// With N trusted proxy hops in front of the gateway (ip_acl.trusted_proxies),
// each appending the address it received the request from, the client is the
// Nth X-Forwarded-For entry from the right. Entries further left were supplied
// by the caller and are ignored. A request carrying fewer entries than N did
// not pass through the configured chain and is judged by its TCP peer.
//
// A selected entry that is not an IP address yields "", which network checks
// refuse, rather than falling back to the proxy's own (possibly allowed)
// address.
func trustedClientIP(r *http.Request, trustedProxies int) string {
	peer := r.RemoteAddr
	if host, _, err := net.SplitHostPort(peer); err == nil {
		peer = host
	}
	if trustedProxies <= 0 {
		return peer
	}
	var hops []string
	for _, line := range r.Header.Values("X-Forwarded-For") {
		for _, part := range strings.Split(line, ",") {
			// Proxies never append empty entries; skipping them keeps an empty
			// header equivalent to an absent one.
			if part = strings.TrimSpace(part); part != "" {
				hops = append(hops, part)
			}
		}
	}
	if len(hops) < trustedProxies {
		return peer
	}
	return canonicalIP(hops[len(hops)-trustedProxies])
}

// canonicalIP accepts a bare IPv4/IPv6 address, optionally bracketed or with a
// port as some proxies write it, and returns it in canonical form or "".
func canonicalIP(s string) string {
	if host, _, err := net.SplitHostPort(s); err == nil {
		s = host
	}
	s = strings.TrimSuffix(strings.TrimPrefix(s, "["), "]")
	if ip := net.ParseIP(s); ip != nil {
		return ip.String()
	}
	return ""
}
