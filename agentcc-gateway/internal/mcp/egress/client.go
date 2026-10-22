package egress

import (
	"context"
	"crypto/tls"
	"errors"
	"fmt"
	"net"
	"net/http"
	"strings"
	"time"
)

// MaxRedirects is how many redirects a guarded client follows.
const MaxRedirects = 5

var (
	// ErrRedirectScheme is a redirect to a scheme other than http or https.
	ErrRedirectScheme = errors.New("egress refused: a redirect to a scheme other than http or https")
	// ErrTooManyRedirects is a redirect past MaxRedirects.
	ErrTooManyRedirects = fmt.Errorf("egress: stopped after %d redirects", MaxRedirects)
)

// HTTPClient returns a client whose every connection goes through the guard:
// TLS connections after the address checks, plaintext ones only to an
// address in private_allow. It uses no proxy, so the address checked is always
// the destination's, and follows at most
// MaxRedirects redirects, each to http or https and each dialled, so checked,
// again. The sensitive headers (credentials the caller set itself) are
// dropped when a redirect leaves the first request's origin; Go already drops
// Authorization and Cookie there. timeout 0 leaves the bound to the request's
// context.
func (d *Dialer) HTTPClient(timeout time.Duration, sensitive ...string) *http.Client {
	transport := &http.Transport{
		Proxy:                 nil,
		DialContext:           d.dialPlaintext,
		DialTLSContext:        d.dialTLS,
		MaxIdleConns:          10,
		IdleConnTimeout:       30 * time.Second,
		ResponseHeaderTimeout: 30 * time.Second,
		ExpectContinueTimeout: time.Second,
	}
	return &http.Client{
		Transport:     transport,
		Timeout:       timeout,
		CheckRedirect: checkRedirect(sensitive),
	}
}

// dialPlaintext is the transport's dialer for http:// requests, the only ones
// it dials without TLS (DialTLSContext takes https://, and there is no proxy).
func (d *Dialer) dialPlaintext(ctx context.Context, network, address string) (net.Conn, error) {
	return d.dial(ctx, network, address, true)
}

// dialTLS dials through the guard and then runs the TLS handshake, verifying
// the certificate for the host dialled. HTTP/1.1 only.
func (d *Dialer) dialTLS(ctx context.Context, network, address string) (net.Conn, error) {
	conn, err := d.dial(ctx, network, address, false)
	if err != nil {
		return nil, err
	}
	host, _, _ := net.SplitHostPort(address)
	cfg := d.tls.Clone()
	if cfg.ServerName == "" {
		cfg.ServerName = host
	}
	cfg.NextProtos = []string{"http/1.1"}
	tc := tls.Client(conn, cfg)
	hctx, cancel := context.WithTimeout(ctx, d.timeout)
	defer cancel()
	if err := tc.HandshakeContext(hctx); err != nil {
		conn.Close()
		return nil, err
	}
	return tc, nil
}

func checkRedirect(sensitive []string) func(*http.Request, []*http.Request) error {
	return func(req *http.Request, via []*http.Request) error {
		if len(via) >= MaxRedirects {
			return ErrTooManyRedirects
		}
		if req.URL.Scheme != "http" && req.URL.Scheme != "https" {
			return ErrRedirectScheme
		}
		if origin(req) != origin(via[0]) {
			for _, h := range sensitive {
				req.Header.Del(h)
			}
		}
		return nil
	}
}

// origin is a request's scheme and host:port, with the default port spelled.
func origin(r *http.Request) string {
	host := r.URL.Host
	if r.URL.Port() == "" {
		port := "80"
		if r.URL.Scheme == "https" {
			port = "443"
		}
		host = net.JoinHostPort(r.URL.Hostname(), port)
	}
	return r.URL.Scheme + "://" + strings.ToLower(host)
}
