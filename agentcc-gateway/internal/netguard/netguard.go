// Package netguard decides which addresses the gateway may connect to when
// someone other than the operator chose the destination: an org's provider
// base_url, or a URL inside a request.
package netguard

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"strings"
	"syscall"
)

// Class is what an address is to an outbound connection.
type Class int

const (
	// Public: anywhere else. Always allowed.
	Public Class = iota
	// Private: private and LAN ranges (RFC 1918, CGNAT, unique local), such as
	// a model server on the Docker network. Allowed only if the caller opts in.
	Private
	// Loopback: the gateway itself (in a container, only the container), never
	// a model server. Never allowed.
	Loopback
	// Forbidden: "this network", link-local (cloud metadata answers on
	// 169.254.169.254), multicast, broadcast, and the metadata services that
	// sit inside ranges an operator can open (Alibaba in CGNAT, AWS IMDS over
	// IPv6 in fc00::/7) or in public space (Azure's WireServer). Never allowed.
	Forbidden
)

var (
	forbiddenNets = mustParseCIDRs(
		"0.0.0.0/8", "169.254.0.0/16", "224.0.0.0/4", "255.255.255.255/32",
		"::/128", "fe80::/10", "ff00::/8",
		"100.100.100.200/32", "fd00:ec2::254/128", "168.63.129.16/32",
	)
	loopbackNets = mustParseCIDRs("127.0.0.0/8", "::1/128")
	privateNets  = mustParseCIDRs("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7")
)

func mustParseCIDRs(cidrs ...string) []*net.IPNet {
	nets := make([]*net.IPNet, len(cidrs))
	for i, c := range cidrs {
		_, n, err := net.ParseCIDR(c)
		if err != nil {
			panic(err)
		}
		nets[i] = n
	}
	return nets
}

func inNets(ip net.IP, nets []*net.IPNet) bool {
	for _, n := range nets {
		if n.Contains(ip) {
			return true
		}
	}
	return false
}

// Classify says which class ip is in. IPv4-mapped IPv6 addresses count as
// their IPv4 address (net.IPNet.Contains unmaps them).
func Classify(ip net.IP) Class {
	switch {
	case inNets(ip, forbiddenNets):
		return Forbidden
	case inNets(ip, loopbackNets):
		return Loopback
	case inNets(ip, privateNets):
		return Private
	}
	return Public
}

// Allowed reports whether a connection to an address in class c may go ahead.
func (c Class) Allowed(allowPrivate bool) bool {
	return c == Public || (c == Private && allowPrivate)
}

func (c Class) String() string {
	switch c {
	case Private:
		return "private"
	case Loopback:
		return "loopback"
	case Forbidden:
		return "link-local, metadata, multicast or unspecified"
	default:
		return "public"
	}
}

// BlockedError is a connection a guarded dialer refused.
type BlockedError struct {
	IP    net.IP // nil when the address could not be parsed
	Class Class
}

func (e *BlockedError) Error() string {
	if e.IP == nil {
		return "refused to connect: not an IP address"
	}
	return fmt.Sprintf("refused to connect to %s (%s address)", e.IP, e.Class)
}

// DialContext returns d's DialContext set to refuse connections to any address
// not Allowed. The check runs on the address actually dialled, after DNS, so a
// host that passed an earlier check and now resolves elsewhere (DNS
// rebinding), or a redirect to such a host, is refused too. It replaces
// d.Control.
//
// A refusal is logged with the address, and its error leaves the address out:
// the error can reach the API caller who chose the host, and the address it
// resolved to would map the operator's network for them. errors.As still
// finds the *BlockedError.
func DialContext(d net.Dialer, allowPrivate bool) func(ctx context.Context, network, address string) (net.Conn, error) {
	d.Control = func(_, address string, _ syscall.RawConn) error {
		return check(address, allowPrivate)
	}
	return func(ctx context.Context, network, address string) (net.Conn, error) {
		conn, err := d.DialContext(ctx, network, address)
		var blocked *BlockedError
		if errors.As(err, &blocked) {
			slog.Warn("refused an outbound connection",
				"address", address, "ip", blocked.IP, "class", blocked.Class)
			return nil, &refusedError{blocked}
		}
		return conn, err
	}
}

// refusedError is a refused connection as the dialer's caller sees it.
type refusedError struct{ blocked *BlockedError }

func (e *refusedError) Error() string {
	return "refused to connect: the destination address is not allowed"
}

func (e *refusedError) Unwrap() error { return e.blocked }

func check(address string, allowPrivate bool) error {
	host, _, err := net.SplitHostPort(address)
	if err != nil {
		return &BlockedError{}
	}
	if i := strings.IndexByte(host, '%'); i >= 0 {
		host = host[:i] // IPv6 zone
	}
	ip := net.ParseIP(host)
	if ip == nil {
		return &BlockedError{}
	}
	if class := Classify(ip); !class.Allowed(allowPrivate) {
		return &BlockedError{IP: ip, Class: class}
	}
	return nil
}
