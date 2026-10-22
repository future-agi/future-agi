package netguard

import (
	"context"
	"errors"
	"net"
	"strings"
	"testing"
)

func TestCheck(t *testing.T) {
	tests := []struct {
		address      string
		denied       bool // without the opt-in
		allowPrivate bool // still refused with it
	}{
		{"203.0.113.7:443", false, false},
		{"[2606:4700::1111]:443", false, false},
		{"10.1.2.3:8080", true, false},
		{"100.64.0.1:80", true, false},
		{"[fd12:3456::1]:8000", true, false},
		{"127.0.0.1:8080", true, true},
		{"[::1]:8080", true, true},
		{"[::ffff:127.0.0.1]:80", true, true},
		{"169.254.169.254:80", true, true},
		{"[::ffff:169.254.169.254]:80", true, true},
		{"[fe80::1%lo0]:80", true, true},
		{"100.100.100.200:80", true, true},
		{"168.63.129.16:80", true, true},
		{"[fd00:ec2::254]:80", true, true},
		{"[fd20:ce::254]:80", true, true}, // GCP metadata over IPv6 (IPv6-only instances), inside fc00::/7
		{"255.255.255.255:80", true, true},
		{"0.0.0.0:80", true, true},
		{"not-an-ip:80", true, true},
		{"no-port", true, true},
	}
	for _, tt := range tests {
		t.Run(tt.address, func(t *testing.T) {
			for _, c := range []struct {
				allowPrivate bool
				want         bool
			}{{false, tt.denied}, {true, tt.allowPrivate}} {
				err := check(tt.address, c.allowPrivate)
				var blocked *BlockedError
				if got := errors.As(err, &blocked); got != c.want {
					t.Errorf("check(%q, allowPrivate=%v) = %v, want refused=%v", tt.address, c.allowPrivate, err, c.want)
				}
			}
		})
	}
}

// IPv6 forms that carry an IPv4 address
// are what that IPv4 address is: a NAT64 gateway, a 6to4 relay, a host that
// still routes IPv4-compatible addresses or a Teredo client connects to the
// embedded address. A public embedded address stays public, and
// the IPv6 loopback and unspecified addresses keep their own class.
//
// Django's classify_ip follows the same rule:
// futureagi/tfc/utils/tests/test_ssrf_guard_embedded_ipv4.py holds this table
// row for row and checks that Django agrees (stricter only where Python's
// ipaddress keeps a whole prefix private or reserved). Change both together.
func TestClassify_IPv6FormsThatEmbedAnIPv4Address(t *testing.T) {
	// teredo builds 2001:0::/32 <server> <flags> <port> <client>, the client
	// address and port stored inverted (RFC 4380).
	teredo := func(server, client string) string {
		s, c := net.ParseIP(server).To4(), net.ParseIP(client).To4()
		ip := make(net.IP, net.IPv6len)
		ip[0], ip[1] = 0x20, 0x01
		copy(ip[4:8], s)
		ip[10], ip[11] = 0xff, 0xff // port 0, inverted
		for i := 0; i < 4; i++ {
			ip[12+i] = ^c[i]
		}
		return ip.String()
	}
	tests := []struct {
		name string
		ip   string
		want Class
	}{
		// NAT64, the well-known prefix 64:ff9b::/96 (RFC 6052).
		{"nat64 loopback", "64:ff9b::127.0.0.1", Loopback},
		{"nat64 private", "64:ff9b::10.0.0.5", Private},
		{"nat64 private, hex", "64:ff9b::a00:5", Private},
		{"nat64 cgnat", "64:ff9b::100.64.0.1", Private},
		{"nat64 metadata", "64:ff9b::169.254.169.254", Forbidden},
		{"nat64 alibaba metadata", "64:ff9b::100.100.100.200", Forbidden},
		{"nat64 this network", "64:ff9b::", Forbidden},
		{"nat64 public", "64:ff9b::8.8.8.8", Public},
		// 6to4, 2002::/16 with the IPv4 address in bits 16-47 (RFC 3056).
		{"6to4 loopback", "2002:7f00:1::", Loopback},
		{"6to4 private", "2002:a00:5::1", Private},
		{"6to4 private, other host bits", "2002:c0a8:101:1::abcd", Private},
		{"6to4 metadata", "2002:a9fe:a9fe::", Forbidden},
		{"6to4 public", "2002:808:808::1", Public},
		// IPv4-compatible, ::/96 (RFC 4291, deprecated).
		{"ipv4-compatible loopback", "::127.0.0.1", Loopback},
		{"ipv4-compatible private", "::10.0.0.5", Private},
		{"ipv4-compatible metadata", "::169.254.169.254", Forbidden},
		{"ipv4-compatible public", "::8.8.8.8", Public},
		// The IPv6 loopback and unspecified addresses sit inside ::/96 but are
		// their own class, not 0.0.0.1 and 0.0.0.0.
		{"ipv6 loopback", "::1", Loopback},
		{"ipv6 unspecified", "::", Forbidden},
		// Teredo, 2001::/32: the client (inverted) and the server address; the
		// stricter decides.
		{"teredo client loopback", teredo("8.8.8.8", "127.0.0.1"), Loopback},
		{"teredo client private", teredo("8.8.8.8", "10.0.0.5"), Private},
		{"teredo client metadata", teredo("8.8.8.8", "169.254.169.254"), Forbidden},
		{"teredo server private", teredo("10.0.0.5", "8.8.4.4"), Private},
		{"teredo public", teredo("8.8.8.8", "8.8.4.4"), Public},
		// IPv4-mapped, unchanged (net.IPNet.Contains unmaps it).
		{"ipv4-mapped private", "::ffff:10.0.0.5", Private},
		// Ordinary IPv6 next to those prefixes.
		{"public ipv6", "2606:4700::1111", Public},
		{"2001:db8 is not teredo", "2001:db8::a00:5", Public},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			ip := net.ParseIP(tt.ip)
			if ip == nil {
				t.Fatalf("bad test address %q", tt.ip)
			}
			if got := Classify(ip); got != tt.want {
				t.Errorf("Classify(%s) = %v, want %v", tt.ip, got, tt.want)
			}
		})
	}
}

// The dialer's check uses that class: a NAT64 or 6to4 literal of a private
// or loopback address is refused as one.
func TestCheck_RefusesEmbeddedIPv4AsItsAddress(t *testing.T) {
	for _, c := range []struct {
		address      string
		allowPrivate bool
		refused      bool
	}{
		{"[64:ff9b::a00:5]:80", false, true},
		{"[64:ff9b::a00:5]:80", true, false}, // private, and the caller opted in
		{"[64:ff9b::7f00:1]:80", true, true},
		{"[64:ff9b::a9fe:a9fe]:80", true, true},
		{"[2002:a9fe:a9fe::1]:80", true, true},
		{"[::a00:5]:80", false, true},
		{"[64:ff9b::808:808]:443", false, false},
	} {
		var blocked *BlockedError
		err := check(c.address, c.allowPrivate)
		if got := errors.As(err, &blocked); got != c.refused {
			t.Errorf("check(%q, allowPrivate=%v) = %v, want refused=%v", c.address, c.allowPrivate, err, c.refused)
		}
	}
}

// The check sees the address the dialer connects to, whatever name it was
// given, and the error leaves that address out: it can reach the API caller
// who chose the name.
func TestDialerRefusesBeforeConnecting(t *testing.T) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()
	_, port, _ := net.SplitHostPort(ln.Addr().String())

	_, err = DialContext(net.Dialer{}, true)(context.Background(), "tcp", net.JoinHostPort("localhost", port))

	var blocked *BlockedError
	if !errors.As(err, &blocked) || blocked.Class != Loopback {
		t.Fatalf("Dial = %v, want a loopback *BlockedError", err)
	}
	if msg := err.Error(); strings.Contains(msg, "127.0.0.1") || strings.Contains(msg, "::1") {
		t.Errorf("Dial error %q names the address localhost resolved to", msg)
	}
}
