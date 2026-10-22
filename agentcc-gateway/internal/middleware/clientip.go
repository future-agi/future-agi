package middleware

import (
	"context"
	"log/slog"
	"net"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// ClientIP is the client IP address of a request, as every IP rule in the
// gateway reads it: KeyAuth, and the request pipeline (ip_acl and a key's
// allowed_ips) through rc.Metadata["client_ip"].
//
// It is the address TrustedClientIP resolved for the request. A request that
// did not pass through TrustedClientIP gets the TCP peer address.
func ClientIP(r *http.Request) string {
	if ip, ok := r.Context().Value(clientIPContextKey{}).(string); ok {
		return ip
	}
	return peerIP(r)
}

type clientIPContextKey struct{}

// TrustedClientIP returns middleware that resolves each request's client IP
// with resolver and records it for ClientIP. It must wrap every handler that
// reads ClientIP, KeyAuth included.
func TrustedClientIP(resolver *ClientIPResolver) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			ctx := context.WithValue(r.Context(), clientIPContextKey{}, resolver.Resolve(r))
			next.ServeHTTP(w, r.WithContext(ctx))
		})
	}
}

// ClientIPResolver derives a request's client IP from the proxies the gateway
// is told to trust (server.trusted_proxies or server.trusted_hops). The zero
// value, and a nil resolver, trust no proxy: the client is the TCP peer and
// X-Forwarded-For and X-Real-IP are ignored.
type ClientIPResolver struct {
	proxies []*net.IPNet
	hops    int
}

// NewClientIPResolver builds the resolver for the gateway's
// server.trusted_proxies and server.trusted_hops (config.ParseTrustedProxies
// checks them; Validate already refused a config it refuses).
func NewClientIPResolver(server config.ServerConfig) (*ClientIPResolver, error) {
	nets, err := config.ParseTrustedProxies(server.TrustedProxies, server.TrustedHops)
	if err != nil {
		return nil, err
	}
	return &ClientIPResolver{proxies: nets, hops: server.TrustedHops}, nil
}

// TrustsProxies reports whether the resolver reads X-Forwarded-For at all.
func (c *ClientIPResolver) TrustsProxies() bool {
	return c != nil && (len(c.proxies) > 0 || c.hops > 0)
}

// Resolve returns the client IP of r.
//
// With trusted_proxies, X-Forwarded-For is read only when the TCP peer is one
// of them. It is then walked right to left, past every address that is a
// trusted proxy, and the first other entry is the client; entries to its left
// are never read. When every entry is a trusted proxy, the leftmost one is
// the client.
//
// With trusted_hops N, the client is the Nth X-Forwarded-For entry from the
// right: N is the number of entries the proxies append, the client's
// included (2 for a Google Cloud load balancer, which appends the client and
// its own IP). A request whose header has fewer than N entries did not come
// through them, and its client is the TCP peer. Use trusted_hops only when
// every path to the gateway passes through those proxies; trusted_proxies has
// no such requirement.
//
// X-Real-IP is never read; the client IP is the TCP peer or, through trusted
// proxies, an X-Forwarded-For entry.
func (c *ClientIPResolver) Resolve(r *http.Request) string {
	peer := peerIP(r)
	if !c.TrustsProxies() {
		return peer
	}
	entries := forwardedFor(r)
	if c.hops > 0 {
		if len(entries) < c.hops {
			return peer
		}
		return entries[len(entries)-c.hops]
	}
	if !c.isTrusted(peer) || len(entries) == 0 {
		return peer
	}
	for i := len(entries) - 1; i >= 0; i-- {
		if !c.isTrusted(entries[i]) {
			return entries[i]
		}
	}
	return entries[0]
}

func (c *ClientIPResolver) isTrusted(addr string) bool {
	ip := net.ParseIP(addr)
	if ip == nil {
		return false
	}
	for _, n := range c.proxies {
		if n.Contains(ip) {
			return true
		}
	}
	return false
}

// TrustedProxyWarnings are the start-up warnings about how cfg derives
// client IPs, empty when there is nothing to warn about:
//   - IP rules (ip_acl, or a configured key's allowed_ips) with no trusted
//     proxy: they apply to the TCP peer, which behind a load balancer is the
//     balancer;
//   - ip_acl.trusted_proxies set: it is not read.
//
// Keys synced from the control plane are not known at start-up, so these
// warnings read the config file's keys and ip_acl.
func TrustedProxyWarnings(cfg *config.Config) []string {
	var warnings []string
	trusts := len(cfg.Server.TrustedProxies) > 0 || cfg.Server.TrustedHops > 0
	if !trusts && (cfg.IPACL.Enabled || configKeysHaveAllowedIPs(cfg.Auth.Keys)) {
		warnings = append(warnings, "IP rules (ip_acl, allowed_ips) apply to the TCP peer address because "+
			"server.trusted_proxies is empty; X-Forwarded-For and X-Real-IP are ignored. Behind a load balancer "+
			"or ingress every request comes from it: set server.trusted_proxies to its addresses or CIDRs")
	}
	if cfg.IPACL.TrustedProxies != 0 {
		warnings = append(warnings, "ip_acl.trusted_proxies is not read: set server.trusted_proxies "+
			"(the proxies' addresses or CIDRs) or server.trusted_hops instead")
	}
	return warnings
}

// LogTrustedProxyWarnings logs TrustedProxyWarnings, once at start-up.
func LogTrustedProxyWarnings(cfg *config.Config) {
	for _, msg := range TrustedProxyWarnings(cfg) {
		slog.Warn(msg)
	}
}

func configKeysHaveAllowedIPs(keys []config.AuthKeyConfig) bool {
	for _, k := range keys {
		if len(k.AllowedIPs) > 0 {
			return true
		}
	}
	return false
}

// forwardedFor returns every X-Forwarded-For entry, in order, across repeated
// headers, with surrounding spaces trimmed. Entries are not parsed: one that
// is not an address (such as "unknown") stays as it is, and the IP rules then
// refuse it as an undetermined address.
func forwardedFor(r *http.Request) []string {
	var entries []string
	for _, header := range r.Header.Values("X-Forwarded-For") {
		for _, part := range strings.Split(header, ",") {
			if part = strings.TrimSpace(part); part != "" {
				entries = append(entries, part)
			}
		}
	}
	return entries
}

// peerIP is the host part of the request's TCP peer address.
func peerIP(r *http.Request) string {
	if host, _, err := net.SplitHostPort(r.RemoteAddr); err == nil {
		return host
	}
	return r.RemoteAddr
}
