package protocol

import (
	"mime"
	"net"
	"net/http"
	"net/url"
	"strings"
)

// Media types of the Streamable HTTP transport.
const (
	MediaJSON = "application/json"
	MediaSSE  = "text/event-stream"
)

// AcceptOK reports whether a POST's Accept header lets the gateway answer it:
// it must list both application/json and text/event-stream, wildcards
// included (the transport spec, both eras). A stateless (2026-07-28) request
// must send the header (the client MUST, and that revision has no older
// clients to spare). A legacy request without one is served: RFC 9110
// §12.5.1 reads an absent Accept as any type, and legacy clients and tools
// that omit it are served today (a leniency the lane asked the orchestrator
// to record, INTEGRATOR-REQUESTS.md).
func AcceptOK(h http.Header, stateless bool) bool {
	if len(h.Values("Accept")) == 0 {
		return !stateless
	}
	return accepts(h, MediaJSON) && accepts(h, MediaSSE)
}

// AcceptsSSE reports whether the client listed text/event-stream (or a
// wildcard that covers it), so a response may be streamed. A request without
// an Accept header gets JSON.
func AcceptsSSE(h http.Header) bool {
	return len(h.Values("Accept")) > 0 && accepts(h, MediaSSE)
}

// accepts reports whether one of the Accept header's ranges covers media
// with a non-zero quality.
func accepts(h http.Header, media string) bool {
	typ, sub, _ := strings.Cut(media, "/")
	for _, line := range h.Values("Accept") {
		for _, rng := range strings.Split(line, ",") {
			mt, params, err := mime.ParseMediaType(strings.TrimSpace(rng))
			if err != nil {
				continue
			}
			if q, ok := params["q"]; ok && strings.Trim(q, "0.") == "" {
				continue // q=0: explicitly not acceptable
			}
			rt, rs, _ := strings.Cut(mt, "/")
			if (rt == "*" && rs == "*") || (rt == typ && (rs == "*" || rs == sub)) {
				return true
			}
		}
	}
	return false
}

// ContentTypeOK reports whether a POST body is declared as application/json
// (parameters such as charset allowed).
func ContentTypeOK(h http.Header) bool {
	mt, _, err := mime.ParseMediaType(h.Get("Content-Type"))
	return err == nil && mt == MediaJSON
}

// OriginOK applies the Origin rule (MCA C9): a request without an Origin
// header passes; one with it passes only when the origin is in allowed (an
// exact scheme://host[:port] match, case-insensitive, or "*") or names the
// gateway's own host. The gateway's own host is one of ownHosts (the
// operator's mcp.public_url), or the host the request was sent to when that
// host cannot be an attacker's DNS name: an IP literal or localhost. A Host
// header that is any other DNS name is not trusted, because a DNS-rebinding
// page sends its own name as both Host and Origin.
func OriginOK(origin, requestHost string, allowed, ownHosts []string) bool {
	if origin == "" {
		return true
	}
	for _, a := range allowed {
		if a == "*" || strings.EqualFold(strings.TrimRight(a, "/"), origin) {
			return true
		}
	}
	u, err := url.Parse(origin)
	if err != nil || u.Host == "" || (u.Scheme != "http" && u.Scheme != "https") {
		return false
	}
	for _, own := range ownHosts {
		if sameHost(u, own) {
			return true
		}
	}
	return rebindSafe(requestHost) && sameHost(u, requestHost)
}

// rebindSafe reports whether a Host header names the gateway by something no
// DNS rebinding can forge: an IP literal or localhost.
func rebindSafe(hostport string) bool {
	h, _ := splitHostPort(hostport, "")
	return net.ParseIP(h) != nil || strings.EqualFold(h, "localhost")
}

// sameHost compares an origin's host and port with the request's Host
// header, defaulting each port from its scheme.
func sameHost(origin *url.URL, requestHost string) bool {
	if requestHost == "" {
		return false
	}
	oh, op := splitHostPort(origin.Host, defaultPort(origin.Scheme))
	rh, rp := splitHostPort(requestHost, "")
	if rp == "" {
		rp = defaultPort(origin.Scheme)
	}
	return strings.EqualFold(oh, rh) && op == rp
}

func splitHostPort(hostport, defPort string) (string, string) {
	if h, p, err := net.SplitHostPort(hostport); err == nil {
		return strings.Trim(h, "[]"), p
	}
	return strings.Trim(hostport, "[]"), defPort
}

func defaultPort(scheme string) string {
	if scheme == "https" {
		return "443"
	}
	return "80"
}
