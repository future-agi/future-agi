package egress

import (
	"context"
	"net"
	"net/http"
	"net/textproto"
	"sync"
	"time"
)

// callbackPlaintext is how a callback's plaintext http:// dial is checked:
// with the address rules alone (no HTTPS rule). See CallbackClient.
const callbackPlaintext = false

// callbackRedirectHeaders are the only request headers a callback keeps on a
// redirect that leaves the first request's origin.
var callbackRedirectHeaders = map[string]bool{
	"Accept":       true,
	"Content-Type": true,
	"User-Agent":   true,
}

// noExceptions is the guard a nil *Dialer stands for: no private_allow
// entry, so no private or loopback address, and never a metadata one.
var noExceptions = sync.OnceValue(func() *Dialer {
	d, _ := New(Options{}) // an empty list cannot fail to parse
	return d
})

// CallbackClient returns a client for a callback an organization's config
// names: a guardrail webhook or external guardrail service, an org alert
// channel. Every
// connection goes through the guard's address rules, after DNS and again on
// every redirect hop: Forbidden addresses (link-local, cloud metadata,
// multicast, unspecified) never, private and loopback addresses only when an
// entry of the operator list mcp.egress.private_allow matches. That list is
// the one exception for every callback an organization's config names; org
// config can open nothing.
//
// Unlike HTTPClient there is no HTTPS rule: plaintext http reaches a public
// address too, since what the guard protects is the operator's network,
// which the address decides, not the scheme.
//
// The client uses no proxy (the address checked is always the
// destination's), follows at most MaxRedirects redirects, each to http or
// https, and a redirect that leaves the first request's origin keeps none of
// its headers but Accept, Content-Type and User-Agent: the organization's own
// headers, the key headers an adapter sets and the Referer (the first URL, which may
// carry a token) stay with the origin they were meant for. Every client of
// one Dialer shares one transport, so connections are pooled across checks
// and organizations; each client has its own timeout (0 leaves the bound to
// the request's context).
//
// A nil Dialer is the guard with no operator exceptions, so a caller that was
// given no guard still refuses every private, loopback and metadata address.
func (d *Dialer) CallbackClient(timeout time.Duration) *http.Client {
	if d == nil {
		d = noExceptions()
	}
	d.callbackOnce.Do(func() {
		d.callbackTransport = &http.Transport{
			Proxy:                 nil,
			DialContext:           d.dialCallback,
			DialTLSContext:        d.dialTLS,
			MaxIdleConns:          100,
			MaxIdleConnsPerHost:   16,
			IdleConnTimeout:       90 * time.Second,
			ExpectContinueTimeout: time.Second,
		}
	})
	return &http.Client{
		Transport:     d.callbackTransport,
		Timeout:       timeout,
		CheckRedirect: checkCallbackRedirect,
	}
}

// dialCallback is the callback transport's dialer for http:// requests.
func (d *Dialer) dialCallback(ctx context.Context, network, address string) (net.Conn, error) {
	return d.dial(ctx, network, address, callbackPlaintext)
}

func checkCallbackRedirect(req *http.Request, via []*http.Request) error {
	if len(via) >= MaxRedirects {
		return ErrTooManyRedirects
	}
	if req.URL.Scheme != "http" && req.URL.Scheme != "https" {
		return ErrRedirectScheme
	}
	if origin(req) != origin(via[0]) {
		for name := range req.Header {
			if !callbackRedirectHeaders[textproto.CanonicalMIMEHeaderKey(name)] {
				delete(req.Header, name)
			}
		}
	}
	return nil
}
