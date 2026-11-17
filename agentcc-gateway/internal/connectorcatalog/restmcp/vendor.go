package restmcp

import (
	"context"
	"crypto/tls"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptrace"
	"net/url"
	"strings"
	"sync/atomic"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp/egress"
)

// Defaults for Options.
const (
	DefaultVendorTimeout  = 30 * time.Second
	DefaultMaxVendorBytes = 8 << 20
)

// Idle vendor connections kept for reuse, in all and per vendor host.
const (
	maxIdleConns        = 128
	maxIdleConnsPerHost = 32
)

// connectTimeout bounds connecting to a vendor (the TCP connect and the TLS
// handshake), within the vendor timeout.
const connectTimeout = egress.DefaultDialTimeout

// Outcomes a call is logged with.
const (
	outcomeOK          = "ok"
	outcomeVendorError = "vendor_error"
	outcomeRefused     = "egress_refused"
	outcomeTransport   = "transport_error"
	outcomeTooLarge    = "too_large"
	outcomeArguments   = "invalid_arguments"
)

// vendorClient makes the REST calls: every connection through the egress
// guard, no redirect followed (a 3xx is the answer), the answer read up to
// a byte limit.
type vendorClient struct {
	http      *http.Client
	replay    map[string]string
	maxBytes  int64
	timeout   time.Duration
	retryBase time.Duration // the first retry's wait when the vendor names none
}

func newVendorClient(o Options) (*vendorClient, error) {
	replay, allow, err := checkReplay(o.Replay)
	if err != nil {
		return nil, err
	}
	d, err := egress.New(egress.Options{PrivateAllow: allow, TLSConfig: o.TLSConfig, DialTimeout: connectTimeout})
	if err != nil {
		return nil, err
	}
	c := d.HTTPClient(0, "Authorization")
	c.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	tr, ok := c.Transport.(*http.Transport)
	if !ok {
		return nil, fmt.Errorf("the egress guard's client has a %T transport, not an *http.Transport", c.Transport)
	}
	// Many users' calls go to the same few vendor hosts: keep enough idle
	// connections per host that concurrent calls reuse them instead of
	// paying a TLS handshake each (Go's default is 2).
	tr.MaxIdleConns, tr.MaxIdleConnsPerHost = maxIdleConns, maxIdleConnsPerHost
	// The vendor timeout alone bounds a call (do's per-attempt context, the
	// headers and the body included). The guard's client also waits at most
	// 30s for response headers, which would cut a call short of a longer
	// --vendor-timeout.
	tr.ResponseHeaderTimeout = 0
	vc := &vendorClient{http: c, replay: replay, maxBytes: o.MaxVendorBytes, timeout: o.VendorTimeout, retryBase: retryBase}
	if vc.maxBytes <= 0 {
		vc.maxBytes = DefaultMaxVendorBytes
	}
	if vc.timeout <= 0 {
		vc.timeout = DefaultVendorTimeout
	}
	return vc, nil
}

// checkReplay validates Options.Replay: each key a vendor origin
// (https://host[:port]), each value a base URL (http or https, no path) on a
// loopback IP address. It returns the map keyed by the normalized origin and
// the egress exceptions that open exactly those targets.
func checkReplay(in map[string]string) (map[string]string, []string, error) {
	out := map[string]string{}
	var allow []string
	for origin, target := range in {
		o, err := url.Parse(origin)
		if err != nil || o.Scheme != "https" || o.Host == "" || (o.Path != "" && o.Path != "/") || o.RawQuery != "" || o.User != nil {
			return nil, nil, fmt.Errorf("replay origin %q must be https://<vendor host>", origin)
		}
		t, err := url.Parse(target)
		if err != nil || (t.Scheme != "http" && t.Scheme != "https") || (t.Path != "" && t.Path != "/") || t.RawQuery != "" || t.User != nil {
			return nil, nil, fmt.Errorf("replay target %q must be http(s)://<loopback address>:<port>", target)
		}
		ip := net.ParseIP(t.Hostname())
		if ip == nil || !ip.IsLoopback() {
			return nil, nil, fmt.Errorf("replay target %q is not a loopback address: a vendor origin is replayed only from this host", target)
		}
		if t.Port() == "" {
			return nil, nil, fmt.Errorf("replay target %q names no port", target)
		}
		out["https://"+strings.ToLower(o.Host)] = t.Scheme + "://" + t.Host
		allow = append(allow, t.Host)
	}
	return out, allow, nil
}

// vendorAnswer is one tool call's outcome at the vendor: its last answer,
// or, when outcome is set, why there was none (failure says it without a
// URL or an address).
type vendorAnswer struct {
	status           int
	header           http.Header
	body             []byte
	outcome, failure string
	attempts         int
}

// call sends the request, retrying as the provider's retry block declares.
func (vc *vendorClient) call(ctx context.Context, t *Tool, r *request, cred *credential) vendorAnswer {
	for attempt := 1; ; attempt++ {
		a := vc.do(ctx, t, r, cred)
		a.attempts = attempt
		if a.outcome != "" || attempt >= maxAttempts || !t.retry.applies(a.status, t.annotations.IdempotentHint) {
			return a
		}
		wait, ok := t.retry.wait(a.header, attempt, vc.retryBase)
		if !ok {
			return a // the vendor asked for a longer wait than a call holds; the error says Retry-After
		}
		if deadline, has := ctx.Deadline(); has && time.Until(deadline) < wait {
			return a
		}
		timer := time.NewTimer(wait)
		select {
		case <-ctx.Done():
			timer.Stop()
			return a
		case <-timer.C:
		}
	}
}

// do sends one vendor request and reads its answer, up to maxBytes.
func (vc *vendorClient) do(ctx context.Context, t *Tool, r *request, cred *credential) vendorAnswer {
	ctx, cancel := context.WithTimeout(ctx, vc.timeout)
	defer cancel()
	// Whether a connection was made, so a timeout says which bound ran out.
	var connected atomic.Bool
	ctx = httptrace.WithClientTrace(ctx, &httptrace.ClientTrace{GotConn: func(httptrace.GotConnInfo) { connected.Store(true) }})
	base := t.origin
	if target, ok := vc.replay[t.origin]; ok {
		base = target
	}
	req, err := r.httpRequest(ctx, base, cred)
	if err != nil {
		return vendorAnswer{outcome: outcomeTransport, failure: err.Error()}
	}
	resp, err := vc.http.Do(req)
	if err != nil {
		outcome, failure := describeTransport(ctx, t.host, vc.timeout, connected.Load(), err)
		return vendorAnswer{outcome: outcome, failure: failure}
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, vc.maxBytes+1))
	switch {
	case err != nil:
		outcome, failure := describeTransport(ctx, t.host, vc.timeout, true, err)
		return vendorAnswer{status: resp.StatusCode, outcome: outcome, failure: failure}
	case int64(len(body)) > vc.maxBytes:
		return vendorAnswer{status: resp.StatusCode, outcome: outcomeTooLarge, failure: fmt.Sprintf(
			"%s answered with more than %d bytes, the runtime's read limit; ask for less, for example fewer results per page",
			t.host, vc.maxBytes)}
	}
	return vendorAnswer{status: resp.StatusCode, header: resp.Header, body: body}
}

// describeTransport names a failure to reach the vendor without its URL or
// the address it resolved to. ctx is the call's (bounded by timeout);
// connected says whether a connection to the vendor was made.
func describeTransport(ctx context.Context, host string, timeout time.Duration, connected bool, err error) (string, string) {
	var refused *egress.RefusedError
	var dnsErr *net.DNSError
	var certErr *tls.CertificateVerificationError
	var netErr net.Error
	switch {
	case errors.As(err, &refused):
		return outcomeRefused, fmt.Sprintf("the call to %s was refused: %s", host, refused.Error())
	case errors.Is(ctx.Err(), context.DeadlineExceeded):
		// The call's own bound, the vendor timeout, ran out.
		return outcomeTransport, fmt.Sprintf("%s did not answer within %s", host, timeout)
	case errors.Is(err, context.Canceled):
		return outcomeTransport, "the call was cancelled"
	case errors.As(err, &dnsErr):
		return outcomeTransport, fmt.Sprintf("%s could not be resolved", host)
	case errors.As(err, &certErr):
		return outcomeTransport, fmt.Sprintf("%s presented a certificate that does not verify", host)
	case errors.Is(err, context.DeadlineExceeded) || (errors.As(err, &netErr) && netErr.Timeout()):
		// A bound inside the call ran out before the vendor timeout.
		if !connected { // the TCP connect or the TLS handshake
			return outcomeTransport, fmt.Sprintf("%s did not accept a connection within %s", host, connectTimeout)
		}
		return outcomeTransport, fmt.Sprintf("the connection to %s timed out", host)
	}
	return outcomeTransport, fmt.Sprintf("%s could not be reached", host)
}
