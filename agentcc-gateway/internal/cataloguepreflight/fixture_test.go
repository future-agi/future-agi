//go:build cataloguepreflighttest

package cataloguepreflight

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"net"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp/egress"
)

// These are deliberately network-free: no fixture Start, socket, TLS
// handshake, gateway, Django import, catalogue add, or launcher execution.
func TestCatalogueFixtureDNSPackets(t *testing.T) {
	q, err := DNSQuestion(PositiveName, 1029)
	if err != nil {
		t.Fatal(err)
	}
	a, err := DNSAnswer(q, PositiveName, net.ParseIP("127.0.0.1"))
	if err != nil || !bytes.Equal(a[len(a)-4:], []byte{127, 0, 0, 1}) {
		t.Fatal("answer", err)
	}
	for _, bad := range [][]byte{nil, q[:11], append(append([]byte{}, q...), 0), append([]byte{0, 1, 0}, q[3:]...)} {
		if _, err := DNSAnswer(bad, PositiveName, net.ParseIP("127.0.0.1")); err == nil {
			t.Fatal("accepted malformed packet")
		}
	}
	if _, err := DNSAnswer(q, "cn30a-other.test", net.ParseIP("127.0.0.1")); err == nil {
		t.Fatal("accepted wrong name")
	}
	for _, name := range []string{"localhost", "cn30a.test.", "-a.test", "a..test", "A.test"} {
		if _, err := DNSQuestion(name, 1); err == nil {
			t.Fatal("accepted invalid fixture name", name)
		}
	}
}

func TestCatalogueFixtureCertificateIdentity(t *testing.T) {
	c, err := NewCertificates(PositiveName)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := c.Leaf.Verify(x509.VerifyOptions{Roots: c.Roots, DNSName: PositiveName}); err != nil {
		t.Fatal(err)
	}
	if len(c.Leaf.IPAddresses) != 0 {
		t.Fatal("IP SAN substitutes for DNS identity")
	}
	for _, name := range []string{"127.0.0.1", "localhost", "cn30a-other.test"} {
		if _, err := c.Leaf.Verify(x509.VerifyOptions{Roots: c.Roots, DNSName: name}); err == nil {
			t.Fatal("accepted wrong SAN")
		}
	}
	other, err := NewCertificates(PositiveName)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := c.Leaf.Verify(x509.VerifyOptions{Roots: other.Roots, DNSName: PositiveName}); err == nil {
		t.Fatal("accepted untrusted CA")
	}
	if Digest(c.CA.Raw) == Digest(c.Leaf.Raw) {
		t.Fatal("certificate digests identical")
	}
}

func TestCatalogueFixtureSyntheticGreenNeverDials(t *testing.T) {
	calls := 0
	lookup := func(_ context.Context, host, port string) ([]net.IPAddr, error) {
		calls++
		if host != MetadataName || port != "443" {
			t.Fatal("logical identity lost")
		}
		return []net.IPAddr{{IP: net.ParseIP("169.254.169.254")}}, nil
	}
	d, err := egress.NewCatalogueSyntheticDialer(egress.Options{}, MetadataName+":443", "127.0.0.1:443", lookup)
	if err != nil {
		t.Fatal(err)
	}
	client := d.HTTPClient(0)
	defer client.CloseIdleConnections()
	_, err = client.Get("https://" + MetadataName + "/mcp")
	var refused *egress.RefusedError
	if !errors.As(err, &refused) || refused.Reason != egress.ReasonForbidden || calls != 1 || d.PhysicalAttempts() != 0 {
		t.Fatal("synthetic green did not refuse before any physical dial", err, calls, d.PhysicalAttempts())
	}
}

func TestCatalogueFixtureSyntheticRegistration(t *testing.T) {
	lookup := func(context.Context, string, string) ([]net.IPAddr, error) {
		t.Fatal("resolver called during construction")
		return nil, nil
	}
	for _, physical := range []string{"192.0.2.1:443", "localhost:443", "127.0.0.1:444", "127.0.0.1:0"} {
		if _, err := egress.NewCatalogueSyntheticDialer(egress.Options{}, MetadataName+":443", physical, lookup); err == nil {
			t.Fatal("accepted unowned physical route", physical)
		}
	}
	if _, err := egress.NewCatalogueSyntheticDialer(egress.Options{TLSConfig: &tls.Config{}}, PositiveName+":443", "127.0.0.1:443", lookup); err == nil {
		t.Fatal("expanded synthetic host scope")
	}
}
