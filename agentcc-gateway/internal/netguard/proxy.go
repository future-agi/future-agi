package netguard

import (
	"context"
	"log/slog"
	"net"
	"net/http"
	"net/url"
	"strings"
	"sync"
)

// lookupIPAddr resolves a request's host for checkHost; a variable so tests
// can stub DNS.
var lookupIPAddr = net.DefaultResolver.LookupIPAddr

// Proxied returns the dialer and the proxy function of a transport that may
// reach, through the operator's proxy, a destination someone other than the
// operator chose. proxy picks the proxy for a request as http.Transport.Proxy
// does; nil means connect directly.
//
// Through a proxy the transport dials the proxy, not the destination, so a
// check at dial time would see only the proxy's address. The request's host is
// therefore checked before a proxy is picked, whether one is or not: a cloud
// metadata name is refused before any lookup, and every address the host
// resolves to must be Allowed. A connection made directly (no proxy set, or
// the host is in NO_PROXY) is checked again as DialContext checks it, on the
// address dialled. Only an address that proxy returned is dialled unchecked:
// the operator chose it, and a proxy is often on a private network or
// loopback. A request whose own host is that address was checked like any
// other.
//
// The check runs whenever the transport picks a proxy, which it does for every
// request except one it sends on an open HTTP/2 connection. Over HTTP/1.1 each
// request is checked, one sent over a pooled connection included: that costs
// a DNS lookup per call, and a lookup that fails fails the request even when
// a live connection was there to use (the check fails closed). A request on
// an open HTTP/2 connection, as to an HTTPS vendor that speaks it, reuses the
// check made when that connection was opened. The chat providers'
// DialContext checks only when it dials.
//
// The proxy resolves the host again for itself. A host that answers the
// gateway with a public address and the proxy with an internal one gets past
// this check; the proxy's own rules have to stop that.
func Proxied(d net.Dialer, allowPrivate bool, proxy func(*http.Request) (*url.URL, error)) (
	dial func(ctx context.Context, network, address string) (net.Conn, error),
	pick func(*http.Request) (*url.URL, error),
) {
	checked := DialContext(d, allowPrivate)
	var proxies sync.Map // the address dialled for each proxy picked
	pick = func(req *http.Request) (*url.URL, error) {
		if err := checkHost(req.Context(), req.URL.Hostname(), allowPrivate); err != nil {
			return nil, err
		}
		proxyURL, err := proxy(req)
		if err == nil && proxyURL != nil {
			proxies.Store(proxyAddr(proxyURL), struct{}{})
		}
		return proxyURL, err
	}
	dial = func(ctx context.Context, network, address string) (net.Conn, error) {
		if _, ok := proxies.Load(address); ok {
			return d.DialContext(ctx, network, address)
		}
		return checked(ctx, network, address)
	}
	return dial, pick
}

// defaultProxyPorts are the ports net/http dials for a proxy URL with none.
var defaultProxyPorts = map[string]string{"http": "80", "https": "443", "socks5": "1080", "socks5h": "1080"}

// proxyAddr is the address the transport dials to reach proxy u.
func proxyAddr(u *url.URL) string {
	port := u.Port()
	if port == "" {
		port = defaultProxyPorts[u.Scheme]
	}
	return net.JoinHostPort(u.Hostname(), port)
}

// checkHost refuses a request to host when host names a cloud metadata service
// or resolves to any address not Allowed. Like DialContext, it logs what it
// refused and leaves the address out of the error.
func checkHost(ctx context.Context, host string, allowPrivate bool) error {
	if IsMetadataHost(host) {
		slog.Warn("refused an outbound request", "host", host, "class", Forbidden)
		return &refusedError{&BlockedError{Class: Forbidden}}
	}
	if i := strings.IndexByte(host, '%'); i >= 0 {
		host = host[:i] // IPv6 zone
	}
	var ips []net.IP
	if ip := net.ParseIP(host); ip != nil {
		ips = []net.IP{ip}
	} else {
		addrs, err := lookupIPAddr(ctx, host)
		if err != nil {
			return err
		}
		for _, a := range addrs {
			ips = append(ips, a.IP)
		}
	}
	if len(ips) == 0 {
		return &refusedError{&BlockedError{}}
	}
	for _, ip := range ips {
		if class := Classify(ip); !class.Allowed(allowPrivate) {
			slog.Warn("refused an outbound request", "host", host, "ip", ip, "class", class)
			return &refusedError{&BlockedError{IP: ip, Class: class}}
		}
	}
	return nil
}
