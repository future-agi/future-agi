//go:build cataloguepreflighttest

package egress

import (
	"context"
	"errors"
	"log/slog"
	"net"
	"net/http"
	"strconv"
	"strings"
	"syscall"
	"time"
)

// CataloguePreflightLookup resolves one pre-registered logical host and port.
// A future contained route launcher must supply its owned loopback DNS fixture
// here and assert that fixture was reached. There is no system-DNS fallback.
// This API is compiled ONLY with cataloguepreflighttest; it is not a product
// option, environment switch, org setting, or authorization to run a probe.
type CataloguePreflightLookup func(context.Context, string, string) ([]net.IPAddr, error)

// CataloguePreflightDialer scopes a resolver to one dialer and one exact logical
// host:port. It retains the real HTTPClient, dialTLS, TLS verification and
// private_allow policy. No synthetic physical routing is exposed.
type CataloguePreflightDialer struct {
	guard   *Dialer
	address string
	lookup  CataloguePreflightLookup
}

type cataloguePreflightContextKey struct{}

// NewCataloguePreflightDialer creates a test-only adapter for mcp.Deps.Dialer.
// It does not change the production constructor or install itself globally.
// The caller must separately qualify containment before any route execution.
func NewCataloguePreflightDialer(o Options, address string, lookup CataloguePreflightLookup) (*CataloguePreflightDialer, error) {
	host, port, err := net.SplitHostPort(address)
	if err != nil || net.ParseIP(host) != nil || !strings.Contains(host, ".") || strings.HasSuffix(host, ".") {
		return nil, errors.New("catalogue preflight requires an exact dotted fixture host:port")
	}
	n, err := strconv.Atoi(port)
	if err != nil || n < 1 || n > 65535 || strconv.Itoa(n) != port || lookup == nil {
		return nil, errors.New("catalogue preflight requires a numeric port and explicit resolver")
	}
	guard, err := New(o)
	if err != nil {
		return nil, err
	}
	return &CataloguePreflightDialer{guard: guard, address: address, lookup: lookup}, nil
}

func (d *CataloguePreflightDialer) context(ctx context.Context) context.Context {
	return context.WithValue(ctx, cataloguePreflightContextKey{}, d)
}

// DialContext retains the logical address until inside the real egress dial.
func (d *CataloguePreflightDialer) DialContext(ctx context.Context, network, address string) (net.Conn, error) {
	return d.guard.DialContext(d.context(ctx), network, address)
}

// HTTPClient wraps only request context. In particular it does NOT replace
// DialContext or DialTLSContext on the product transport, or override SNI.
func (d *CataloguePreflightDialer) HTTPClient(timeout time.Duration, sensitive ...string) *http.Client {
	client := d.guard.HTTPClient(timeout, sensitive...)
	client.Transport = &cataloguePreflightTransport{owner: d, transport: client.Transport.(*http.Transport)}
	return client
}

type cataloguePreflightTransport struct {
	owner     *CataloguePreflightDialer
	transport *http.Transport
}

func (t *cataloguePreflightTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	return t.transport.RoundTrip(req.Clone(t.owner.context(req.Context())))
}

func (t *cataloguePreflightTransport) CloseIdleConnections() {
	t.transport.CloseIdleConnections()
}

func (d *Dialer) dial(ctx context.Context, network, address string, plaintext bool) (net.Conn, error) {
	if synthetic, ok := ctx.Value(catalogueSyntheticContextKey{}).(*CatalogueSyntheticDialer); ok {
		return d.catalogueSyntheticDial(ctx, synthetic, network, address, plaintext)
	}
	fixture, ok := ctx.Value(cataloguePreflightContextKey{}).(*CataloguePreflightDialer)
	if !ok {
		return d.dialDefault(ctx, network, address, plaintext)
	}
	if fixture.guard != d || address != fixture.address {
		return nil, errors.New("catalogue preflight: unregistered dial")
	}
	if network != "tcp" && network != "tcp4" && network != "tcp6" {
		return nil, errors.New("catalogue preflight: unsupported network")
	}
	ctx, cancel := context.WithTimeout(ctx, d.timeout)
	defer cancel()
	host, _, _ := net.SplitHostPort(address)
	resolved, err := d.cataloguePreflightAddresses(ctx, fixture, plaintext)
	if err != nil {
		var refused *RefusedError
		if errors.As(err, &refused) {
			slog.Warn("mcp egress refused a connection", "address", address, "ip", refused.ip,
				"class", refused.Class.String(), "reason", string(refused.Reason))
		}
		return nil, err
	}
	nd := net.Dialer{
		Timeout: d.timeout,
		Control: func(_, resolved string, _ syscall.RawConn) error {
			return d.check(host, resolved, plaintext)
		},
	}
	for _, numeric := range resolved {
		var conn net.Conn
		conn, err = nd.DialContext(ctx, network, numeric)
		if err == nil {
			return conn, nil
		}
		var refused *RefusedError
		if errors.As(err, &refused) {
			return nil, refused
		}
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
	}
	return nil, err
}

// Resolve afresh for each physical dial. Check ALL logical answers before
// handing any numeric address to net.Dialer. The existing check retains the
// original name for private_allow and Forbidden precedence. dialTLS still
// receives the original address, so its certificate name is unchanged.
func (d *Dialer) cataloguePreflightAddresses(ctx context.Context, fixture *CataloguePreflightDialer, plaintext bool) ([]string, error) {
	host, port, _ := net.SplitHostPort(fixture.address)
	answers, err := fixture.lookup(ctx, host, port)
	if err != nil {
		return nil, err
	}
	if len(answers) == 0 {
		return nil, errors.New("catalogue preflight: resolver returned no addresses")
	}
	resolved := make([]string, 0, len(answers))
	for _, answer := range answers {
		// Zone identifiers are not part of this loopback fixture contract.
		if answer.IP.To16() == nil || answer.Zone != "" {
			return nil, errors.New("catalogue preflight: invalid resolver answer")
		}
		numeric := net.JoinHostPort(answer.IP.String(), port)
		if err := d.check(host, numeric, plaintext); err != nil {
			return nil, err
		}
		// This seam is not permission for a public/private transport. Even a
		// product-allowed non-loopback answer cannot leave the test fixture.
		if !answer.IP.IsLoopback() {
			return nil, errors.New("catalogue preflight: physical destination is not loopback")
		}
		resolved = append(resolved, numeric)
	}
	return resolved, nil
}
