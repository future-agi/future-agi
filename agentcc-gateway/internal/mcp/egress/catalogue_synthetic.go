//go:build cataloguepreflighttest

package egress

import (
	"context"
	"errors"
	"net"
	"net/http"
	"sync/atomic"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/cataloguepreflight/identity"
)

// CatalogueSyntheticDialer is a separate assertion-red fixture, NOT a resolver
// option on the product. It maps exactly one logical metadata address to an
// owned numeric loopback socket, only AFTER the real address policy succeeds.
// Green must never reach that socket. A disposable check-only mutation can.
// The original dialTLS still verifies the logical hostname's certificate.
// This is synthetic routing, never evidence of contact with live metadata.
type CatalogueSyntheticDialer struct {
	guard             *Dialer
	address, physical string
	lookup            CataloguePreflightLookup
	reached           atomic.Int64
}

type catalogueSyntheticContextKey struct{}

func NewCatalogueSyntheticDialer(o Options, address, physical string, lookup CataloguePreflightLookup) (*CatalogueSyntheticDialer, error) {
	host, port, err := net.SplitHostPort(address)
	ip, physicalPort, physicalErr := net.SplitHostPort(physical)
	parsed := net.ParseIP(ip)
	if err != nil || physicalErr != nil || host != "metadata.google.internal" ||
		parsed == nil || !parsed.IsLoopback() || physicalPort != port {
		return nil, errors.New("synthetic route requires exact metadata name and owned loopback at the same port")
	}
	base, err := NewCataloguePreflightDialer(o, address, lookup)
	if err != nil {
		return nil, err
	}
	return &CatalogueSyntheticDialer{guard: base.guard, address: address, physical: physical, lookup: lookup}, nil
}

func (d *CatalogueSyntheticDialer) context(ctx context.Context) context.Context {
	return context.WithValue(ctx, catalogueSyntheticContextKey{}, d)
}

func (d *CatalogueSyntheticDialer) DialContext(ctx context.Context, network, address string) (net.Conn, error) {
	return d.guard.DialContext(d.context(ctx), network, address)
}

func (d *CatalogueSyntheticDialer) HTTPClient(timeout time.Duration, sensitive ...string) *http.Client {
	c := d.guard.HTTPClient(timeout, sensitive...)
	c.Transport = &catalogueSyntheticTransport{owner: d, transport: c.Transport.(*http.Transport)}
	return c
}

func (d *CatalogueSyntheticDialer) PhysicalAttempts() int64 { return d.reached.Load() }

type catalogueSyntheticTransport struct {
	owner     *CatalogueSyntheticDialer
	transport *http.Transport
}

func (t *catalogueSyntheticTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	return t.transport.RoundTrip(r.Clone(t.owner.context(r.Context())))
}

func (t *catalogueSyntheticTransport) CloseIdleConnections() { t.transport.CloseIdleConnections() }

func (d *Dialer) catalogueSyntheticDial(ctx context.Context, f *CatalogueSyntheticDialer, network, address string, plaintext bool) (net.Conn, error) {
	if f.guard != d || address != f.address || network != "tcp" || plaintext {
		return nil, errors.New("unregistered synthetic TLS dial")
	}
	ctx, cancel := context.WithTimeout(ctx, d.timeout)
	defer cancel()
	host, port, _ := net.SplitHostPort(address)
	answers, err := f.lookup(ctx, host, port)
	if err != nil {
		return nil, err
	}
	if len(answers) != 1 || answers[0].Zone != "" || !answers[0].IP.Equal(net.ParseIP(identity.MetadataIP)) {
		return nil, errors.New("synthetic route requires the exact registered logical metadata answer")
	}
	// This is the actual product check, not a fixture reimplementation. The
	// mutation harness removes only check's body in a disposable compiler input.
	if err := d.check(host, net.JoinHostPort(answers[0].IP.String(), port), plaintext); err != nil {
		return nil, err
	}
	f.reached.Add(1)
	// No hostname, routing table, public address, or TLS override is involved.
	// The frozen outer Go guard remains on this ordinary numeric net.Dialer.
	return (&net.Dialer{Timeout: d.timeout}).DialContext(ctx, network, f.physical)
}
