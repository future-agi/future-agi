package egress

import (
	"context"
	"errors"
	"go/ast"
	"go/parser"
	"go/token"
	"net"
	"path/filepath"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/netguard"
)

func mustNew(t *testing.T, allow ...string) *Dialer {
	t.Helper()
	d, err := New(Options{PrivateAllow: allow, DialTimeout: 2 * time.Second})
	if err != nil {
		t.Fatalf("New(%v): %v", allow, err)
	}
	return d
}

// refusal dials address through d and returns the refusal, failing the test
// when the dial was not refused by the guard.
func refusal(t *testing.T, d *Dialer, address string) *RefusedError {
	t.Helper()
	conn, err := d.DialContext(context.Background(), "tcp", address)
	if conn != nil {
		conn.Close()
		t.Fatalf("dial %s connected; want a refusal", address)
	}
	var refused *RefusedError
	if !errors.As(err, &refused) {
		t.Fatalf("dial %s: err = %v (%T), want a *RefusedError", address, err, err)
	}
	return refused
}

// listen starts a TCP listener on loopback that accepts and closes.
func listen(t *testing.T) string {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { ln.Close() })
	go func() {
		for {
			c, err := ln.Accept()
			if err != nil {
				return
			}
			c.Close()
		}
	}()
	return ln.Addr().String()
}

// TestDialerRefusesPrivateAndMetadata: with no operator exceptions, loopback,
// private (RFC 1918, CGNAT, unique local), link-local, multicast, unspecified
// and cloud-metadata addresses are refused at dial time, before any packet
// is sent, and after DNS: a name that resolves to loopback is also refused.
func TestDialerRefusesPrivateAndMetadata(t *testing.T) {
	d := mustNew(t)
	for _, c := range []struct {
		addr  string
		class netguard.Class
	}{
		{"10.1.2.3:80", netguard.Private},
		{"172.16.0.1:443", netguard.Private},
		{"192.168.1.10:8080", netguard.Private},
		{"100.64.0.1:80", netguard.Private}, // CGNAT
		{"[fd12:3456::1]:80", netguard.Private},
		{"127.0.0.1:80", netguard.Loopback},
		{"127.9.9.9:80", netguard.Loopback},
		{"[::1]:80", netguard.Loopback},
		{"169.254.169.254:80", netguard.Forbidden}, // AWS, GCP, Azure, OCI metadata
		{"169.254.170.2:80", netguard.Forbidden},
		{"[fe80::1]:80", netguard.Forbidden},
		{"224.0.0.1:80", netguard.Forbidden},
		{"0.0.0.0:80", netguard.Forbidden},
		{"100.100.100.200:80", netguard.Forbidden}, // Alibaba metadata
		{"[fd00:ec2::254]:80", netguard.Forbidden}, // AWS IMDS over IPv6
		{"168.63.129.16:80", netguard.Forbidden},   // Azure WireServer
		{"[::ffff:169.254.169.254]:80", netguard.Forbidden},
	} {
		t.Run(c.addr, func(t *testing.T) {
			r := refusal(t, d, c.addr)
			if r.Class != c.class {
				t.Errorf("class = %v, want %v", r.Class, c.class)
			}
			want := ReasonNotAllowed
			if c.class == netguard.Forbidden {
				want = ReasonForbidden
			}
			if r.Reason != want {
				t.Errorf("reason = %q, want %q", r.Reason, want)
			}
			host, _, _ := net.SplitHostPort(c.addr)
			if strings.Contains(r.Error(), host) {
				t.Errorf("the refusal %q names the address; it must not map the network for the caller", r.Error())
			}
		})
	}

	t.Run("after DNS: a name that resolves to loopback", func(t *testing.T) {
		if r := refusal(t, d, "localhost:80"); r.Class != netguard.Loopback {
			t.Errorf("localhost class = %v, want loopback", r.Class)
		}
	})

	t.Run("a listening loopback server is refused, so the refusal is the guard's", func(t *testing.T) {
		addr := listen(t)
		refusal(t, d, addr)
		conn, err := mustNew(t, addr).DialContext(context.Background(), "tcp", addr)
		if err != nil {
			t.Fatalf("the same server with its address in private_allow: %v", err)
		}
		conn.Close()
	})
}

// TestPrivateAllowOperatorListOnly: a private or loopback address is reached
// only when an entry of the operator list mcp.egress.private_allow matches it
// (a CIDR, an IP with or without a port, or a host name with or without a
// port, matched against the name dialled), and the list, given to New, is
// the guard's only knob.
func TestPrivateAllowOperatorListOnly(t *testing.T) {
	addr := listen(t)
	host, port, _ := net.SplitHostPort(addr)
	other := strconv.Itoa(mustAtoi(t, port) + 1)
	for _, c := range []struct {
		allow []string
		ok    bool
	}{
		{nil, false},
		{[]string{addr}, true},
		{[]string{host + ":" + other}, false},
		{[]string{host}, true},
		{[]string{"127.0.0.0/8"}, true},
		{[]string{"10.0.0.0/8", "192.168.0.0/16"}, false},
		{[]string{"localhost:" + port}, false}, // a name entry matches the name dialled, not the address
	} {
		d := mustNew(t, c.allow...)
		conn, err := d.DialContext(context.Background(), "tcp", addr)
		if c.ok {
			if err != nil {
				t.Errorf("private_allow %v: dial %s: %v, want a connection", c.allow, addr, err)
				continue
			}
			conn.Close()
			continue
		}
		var refused *RefusedError
		if !errors.As(err, &refused) || refused.Class != netguard.Loopback {
			t.Errorf("private_allow %v: dial %s: err = %v, want a loopback refusal", c.allow, addr, err)
		}
		if conn != nil {
			conn.Close()
		}
	}

	t.Run("host name entries", func(t *testing.T) {
		d := mustNew(t, "mcp.internal:8443", "Tools.LAN.")
		for _, c := range []struct {
			host, resolved string
			ok             bool
		}{
			{"mcp.internal", "10.0.0.5:8443", true},
			{"MCP.internal.", "10.0.0.5:8443", true},
			{"mcp.internal", "10.0.0.5:9000", false},
			{"tools.lan", "192.168.1.4:80", true},
			{"other.internal", "10.0.0.5:8443", false},
			{"10.0.0.5", "10.0.0.5:8443", false},
		} {
			err := d.check(c.host, c.resolved, false)
			if (err == nil) != c.ok {
				t.Errorf("check(%q, %q) = %v, want ok %v", c.host, c.resolved, err, c.ok)
			}
		}
	})

	t.Run("an invalid entry is refused at New", func(t *testing.T) {
		for _, bad := range []string{"", "10.0.0.0/33", "host:0", "host:notaport", ":80"} {
			if _, err := New(Options{PrivateAllow: []string{bad}}); err == nil {
				t.Errorf("New accepted private_allow entry %q", bad)
			}
		}
	})

	t.Run("the list is the only knob", func(t *testing.T) {
		var fields []string
		ot := reflect.TypeOf(Options{})
		for i := 0; i < ot.NumField(); i++ {
			fields = append(fields, ot.Field(i).Name)
		}
		if got := strings.Join(fields, ","); got != "PrivateAllow,DialTimeout,TLSConfig" {
			t.Errorf("Options fields = %s; a new field must not open private egress", got)
		}
		dt := reflect.TypeOf(Dialer{})
		for i := 0; i < dt.NumField(); i++ {
			if dt.Field(i).IsExported() {
				t.Errorf("Dialer exports field %s; the guard is fixed at New", dt.Field(i).Name)
			}
		}
		var methods []string
		pt := reflect.TypeOf(&Dialer{})
		for i := 0; i < pt.NumMethod(); i++ {
			methods = append(methods, pt.Method(i).Name)
		}
		sort.Strings(methods)
		// CallbackClient builds a client from the same list; it reads
		// the list and changes nothing.
		if got := strings.Join(methods, ","); got != "CallbackClient,DialContext,HTTPClient" {
			t.Errorf("*Dialer methods = %s; none may change the operator list", got)
		}
	})
}

// TestMetadataRefusedEvenWhenListed: cloud-metadata addresses stay refused
// when the list names them, a range around them, or everything. The load
// check refuses such entries first; the dialer does not rely on it.
func TestMetadataRefusedEvenWhenListed(t *testing.T) {
	listed := []string{
		"169.254.0.0/16", "169.254.169.254", "169.254.169.254:80", "0.0.0.0/0", "::/0",
		"100.100.100.200:80", "fd00:ec2::254", "168.63.129.16/32", "metadata.google.internal",
		"fd20:ce::254", "[fd20:ce::254]:80", "fd20:ce::254/128",
	}
	d := mustNew(t, listed...)
	for _, addr := range []string{
		"169.254.169.254:80", "[fd00:ec2::254]:80", "[fd20:ce::254]:80", "100.100.100.200:80", "168.63.129.16:80",
		"[::ffff:169.254.169.254]:80", "[fe80::1]:80",
	} {
		r := refusal(t, d, addr)
		if r.Class != netguard.Forbidden || r.Reason != ReasonForbidden {
			t.Errorf("%s: refused as %v/%s, want forbidden", addr, r.Class, r.Reason)
		}
	}
	// A plaintext request to the metadata service is refused as forbidden,
	// not as a missing https: no exception reaches it.
	_, err := d.HTTPClient(2 * time.Second).Get("http://169.254.169.254/latest/meta-data/")
	var refused *RefusedError
	if !errors.As(err, &refused) || refused.Reason != ReasonForbidden {
		t.Fatalf("GET metadata: err = %v, want a forbidden refusal", err)
	}

	for _, entry := range listed {
		cfg := config.DefaultConfig()
		cfg.MCP.Egress.PrivateAllow = []string{entry}
		if err := cfg.Validate(); err == nil || !strings.Contains(err.Error(), "metadata") {
			t.Errorf("config load accepted private_allow %q (err %v); config load refuses metadata entries", entry, err)
		}
	}
}

// TestPrivateAllowWideRangeKeepsMetadataRefused: a wider private range that
// holds one metadata address (100.64.0.0/10 holds 100.100.100.200;
// fd00:ec2::/64 holds fd00:ec2::254) is accepted at load, and the dialer
// still refuses that address as Forbidden while another address of the
// range passes.
func TestPrivateAllowWideRangeKeepsMetadataRefused(t *testing.T) {
	allow := []string{"100.64.0.0/10", "fd00:ec2::/64", "fd20:ce::/64"}
	cfg := config.DefaultConfig()
	cfg.MCP.Egress.PrivateAllow = allow
	if err := cfg.Validate(); err != nil {
		t.Fatalf("config load refused %v: %v", allow, err)
	}
	d := mustNew(t, allow...)
	for _, addr := range []string{"100.100.100.200:80", "[fd00:ec2::254]:80", "[fd20:ce::254]:80"} {
		r := refusal(t, d, addr)
		if r.Class != netguard.Forbidden {
			t.Errorf("%s refused as %v, want Forbidden", addr, r.Class)
		}
	}
	strict := mustNew(t)
	for _, addr := range []string{"100.64.0.1:80", "100.127.255.254:443", "[fd00:ec2::1]:80", "[fd00:ec2::253]:443", "[fd20:ce::253]:443"} {
		for _, plaintext := range []bool{false, true} {
			if err := d.check("", addr, plaintext); err != nil {
				t.Errorf("%s (plaintext %v) with %v listed: %v, want it to pass", addr, plaintext, allow, err)
			}
		}
		if err := strict.check("", addr, false); err == nil {
			t.Errorf("%s passed with nothing listed", addr)
		}
	}
}

// TestEmbeddedIPv4IsGuardedAsItsAddress:
// an org callback whose name resolves to a NAT64, 6to4, IPv4-compatible or
// Teredo address of a private, loopback or metadata IPv4 address is refused
// as that address; an IPv4 CIDR in the list does not open the IPv6 form, and
// metadata stays refused whatever the list holds. A public embedded address
// still passes.
func TestEmbeddedIPv4IsGuardedAsItsAddress(t *testing.T) {
	strict := mustNew(t)
	for _, c := range []struct {
		addr  string
		class netguard.Class
	}{
		{"[64:ff9b::a00:5]:443", netguard.Private},
		{"[64:ff9b::7f00:1]:443", netguard.Loopback},
		{"[2002:a00:5::1]:443", netguard.Private},
		{"[::a00:5]:443", netguard.Private},
		{"[2001:0:808:808:0:ffff:f5ff:fffa]:443", netguard.Private}, // teredo, client 10.0.0.5
	} {
		r := refusal(t, strict, c.addr)
		if r.Class != c.class || r.Reason != ReasonNotAllowed {
			t.Errorf("%s: refused as %v/%s, want %v/%s", c.addr, r.Class, r.Reason, c.class, ReasonNotAllowed)
		}
	}
	wide := mustNew(t, "10.0.0.0/8", "64:ff9b::/96")
	for _, addr := range []string{"[64:ff9b::a9fe:a9fe]:80", "[2002:a9fe:a9fe::]:80", "[::a9fe:a9fe]:80"} {
		r := refusal(t, wide, addr)
		if r.Class != netguard.Forbidden || r.Reason != ReasonForbidden {
			t.Errorf("%s: refused as %v/%s, want forbidden", addr, r.Class, r.Reason)
		}
	}
	if err := mustNew(t, "10.0.0.0/8").check("", "[64:ff9b::a00:5]:443", false); err == nil {
		t.Error("the IPv4 entry 10.0.0.0/8 opened the NAT64 form of 10.0.0.5")
	}
	if err := wide.check("", "[64:ff9b::a00:5]:443", false); err != nil {
		t.Errorf("64:ff9b::/96 listed, NAT64 form of 10.0.0.5: %v, want it to pass", err)
	}
	if err := strict.check("", "[64:ff9b::808:808]:443", false); err != nil {
		t.Errorf("NAT64 form of a public address: %v, want it to pass", err)
	}
}

// TestEgressUsesNetguardClassification: every refusal carries the class
// netguard.Classify gives the address, and this package declares no address
// range of its own (no string literal in it parses as an IP or a CIDR).
func TestEgressUsesNetguardClassification(t *testing.T) {
	d := mustNew(t)
	for _, ip := range []string{
		"8.8.8.8", "192.0.2.10", "2001:db8::1", "10.0.0.1", "172.31.255.254", "192.168.0.1", "100.64.0.1",
		"fc00::1", "127.0.0.1", "::1", "169.254.169.254", "0.0.0.0", "224.0.0.251", "ff02::1", "fe80::1",
		"100.100.100.200", "fd00:ec2::254", "168.63.129.16", "255.255.255.255", "::",
	} {
		want := netguard.Classify(net.ParseIP(ip))
		// plaintext so that a public address is also refused (https_required).
		err := d.check("", net.JoinHostPort(ip, "80"), true)
		var refused *RefusedError
		if !errors.As(err, &refused) {
			t.Errorf("%s: err = %v, want a refusal", ip, err)
			continue
		}
		if refused.Class != want {
			t.Errorf("%s: refusal class %v, netguard.Classify says %v", ip, refused.Class, want)
		}
		if want == netguard.Public && refused.Reason != ReasonHTTPSRequired {
			t.Errorf("%s: public plaintext refused as %s, want https_required", ip, refused.Reason)
		}
	}

	files, err := filepath.Glob("*.go")
	if err != nil {
		t.Fatal(err)
	}
	importsNetguard := false
	fset := token.NewFileSet()
	for _, name := range files {
		if strings.HasSuffix(name, "_test.go") {
			continue
		}
		f, err := parser.ParseFile(fset, name, nil, 0)
		if err != nil {
			t.Fatal(err)
		}
		for _, imp := range f.Imports {
			if imp.Path.Value == `"github.com/futureagi/agentcc-gateway/internal/netguard"` {
				importsNetguard = true
			}
		}
		ast.Inspect(f, func(n ast.Node) bool {
			lit, ok := n.(*ast.BasicLit)
			if !ok || lit.Kind != token.STRING {
				return true
			}
			s, err := strconv.Unquote(lit.Value)
			if err != nil {
				return true
			}
			if _, _, err := net.ParseCIDR(s); err == nil || net.ParseIP(s) != nil {
				t.Errorf("%s: %s declares an address or range; the ranges are netguard's", fset.Position(lit.Pos()), lit.Value)
			}
			return true
		})
	}
	if !importsNetguard {
		t.Error("package egress does not import netguard")
	}
}

func mustAtoi(t *testing.T, s string) int {
	t.Helper()
	n, err := strconv.Atoi(s)
	if err != nil {
		t.Fatal(err)
	}
	return n
}
