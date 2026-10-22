// Package egress is the gateway's guard for the callbacks an organization's
// config names: guardrail webhooks, external guardrail services, FutureAGI eval
// base_urls and alert channels (CallbackClient). It classifies each resolved
// address with netguard, so provider base_urls and these callbacks share one
// range table, and refuses at dial time, after DNS:
//
//   - Forbidden addresses (link-local, cloud metadata, multicast,
//     unspecified), always, whatever the operator listed;
//   - Private and Loopback addresses, unless an entry of the operator list
//     mcp.egress.private_allow matches;
//   - plaintext HTTP to any address the list does not match (HTTPClient
//     only; CallbackClient has no HTTPS rule).
//
// The operator list is the only exception. Org config has no way to mark a
// server private, and nothing here reads one.
package egress

import (
	"context"
	"crypto/tls"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/netguard"
)

// DefaultDialTimeout bounds one connect when Options.DialTimeout is 0.
const DefaultDialTimeout = 10 * time.Second

// Options configure a Dialer. They come from operator config only.
type Options struct {
	// PrivateAllow is mcp.egress.private_allow: CIDRs, IPs or host names,
	// each with an optional port, that may be reached although they are
	// private or loopback, and over plaintext HTTP. Config load
	// (config.MCPEgressConfig) refuses metadata entries; the dialer refuses
	// metadata addresses regardless.
	PrivateAllow []string
	// DialTimeout bounds one connect; 0 means DefaultDialTimeout.
	DialTimeout time.Duration
	// TLSConfig is the client TLS configuration HTTPClient uses; nil means
	// the system roots. Tests set RootCAs.
	TLSConfig *tls.Config
}

// Dialer is the guard. It is fixed at New and safe for concurrent use.
//
// DialContext enforces the address rules only: it cannot tell a TLS
// connection from a plaintext one. HTTP callers use HTTPClient, which adds
// the HTTPS rule and re-checks redirects, or CallbackClient for a callback
// an organization's config names.
type Dialer struct {
	allow   allowList
	timeout time.Duration
	tls     *tls.Config

	// callbackTransport is every CallbackClient's transport, built once.
	callbackOnce      sync.Once
	callbackTransport *http.Transport
}

// New builds the guard from the operator's options. It refuses an entry it
// cannot parse.
func New(o Options) (*Dialer, error) {
	allow, err := parseAllowList(o.PrivateAllow)
	if err != nil {
		return nil, err
	}
	d := &Dialer{allow: allow, timeout: o.DialTimeout, tls: o.TLSConfig}
	if d.timeout <= 0 {
		d.timeout = DefaultDialTimeout
	}
	if d.tls == nil {
		d.tls = &tls.Config{MinVersion: tls.VersionTLS12}
	}
	return d, nil
}

// Reason says why the guard refused a connection.
type Reason string

const (
	// ReasonForbidden: a netguard Forbidden address, never reachable.
	ReasonForbidden Reason = "forbidden"
	// ReasonNotAllowed: a private or loopback address no private_allow
	// entry matches.
	ReasonNotAllowed Reason = "not_allowed"
	// ReasonHTTPSRequired: plaintext HTTP to an address no private_allow
	// entry matches.
	ReasonHTTPSRequired Reason = "https_required"
)

// RefusedError is a connection the guard refused. Class is what
// netguard.Classify says the address is.
type RefusedError struct {
	Class  netguard.Class
	Reason Reason
	ip     net.IP // logged by the dialer; never in Error
}

// Error explains the refusal by class and reason only, never the address:
// the error can reach the caller who chose the host. The dialer logs the
// address for the operator.
func (e *RefusedError) Error() string {
	switch e.Reason {
	case ReasonHTTPSRequired:
		return "egress refused: plaintext http is allowed only to an address in mcp.egress.private_allow; use https"
	case ReasonNotAllowed:
		return fmt.Sprintf("egress refused: a %s address that mcp.egress.private_allow does not list", e.Class)
	default:
		return fmt.Sprintf("egress refused: a %s address, which is never reachable", e.Class)
	}
}

// DialContext connects to address unless the guard refuses one of the
// addresses it resolves to; the refusal is a *RefusedError.
func (d *Dialer) DialContext(ctx context.Context, network, address string) (net.Conn, error) {
	return d.dial(ctx, network, address, false)
}

// dial checks every address the dialer tries in its Control func, which runs
// after DNS and before the connect, so a refused address is never contacted
// and a name that later resolves elsewhere is checked again.
func (d *Dialer) dial(ctx context.Context, network, address string, plaintext bool) (net.Conn, error) {
	host, _, err := net.SplitHostPort(address)
	if err != nil {
		return nil, fmt.Errorf("egress: %w", err)
	}
	nd := net.Dialer{
		Timeout: d.timeout,
		Control: func(_, resolved string, _ syscall.RawConn) error {
			return d.check(host, resolved, plaintext)
		},
	}
	conn, err := nd.DialContext(ctx, network, address)
	var refused *RefusedError
	if errors.As(err, &refused) {
		slog.Warn("mcp egress refused a connection", "address", address, "ip", refused.ip,
			"class", refused.Class.String(), "reason", string(refused.Reason))
		return nil, refused
	}
	return conn, err
}

// check decides one resolved address (ip:port) of a dial to host: Forbidden
// first, before the list is read, then the list for private and loopback
// addresses, then the list again for plaintext.
func (d *Dialer) check(host, resolved string, plaintext bool) error {
	ipText, port, err := net.SplitHostPort(resolved)
	if err != nil {
		return &RefusedError{Class: netguard.Forbidden, Reason: ReasonForbidden}
	}
	if i := strings.IndexByte(ipText, '%'); i >= 0 {
		ipText = ipText[:i] // IPv6 zone
	}
	ip := net.ParseIP(ipText)
	if ip == nil {
		return &RefusedError{Class: netguard.Forbidden, Reason: ReasonForbidden}
	}
	class := netguard.Classify(ip)
	if class == netguard.Forbidden {
		return &RefusedError{Class: class, Reason: ReasonForbidden, ip: ip}
	}
	listed := d.allow.match(host, ip, port)
	if class != netguard.Public && !listed {
		return &RefusedError{Class: class, Reason: ReasonNotAllowed, ip: ip}
	}
	if plaintext && !listed {
		return &RefusedError{Class: class, Reason: ReasonHTTPSRequired, ip: ip}
	}
	return nil
}
