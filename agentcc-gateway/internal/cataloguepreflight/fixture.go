//go:build cataloguepreflighttest

package cataloguepreflight

import (
	"crypto/tls"
	"errors"
	"net"
	"path/filepath"
	"strconv"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-mcpmock/mcpmock"
	"github.com/futureagi/agentcc-gateway/internal/cataloguepreflight/identity"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/egress"
)

const PositiveName = "cn30a-docs.test"
const MetadataName = "metadata.google.internal"

// Each case gets NEW owned sockets and a NEW gateway process/invocation. There
// is no mutable mode endpoint, global dialer, or late allowlist extension.
var Cases = []string{"positive", "tls-wrong-san", "tls-untrusted-ca", "allow-absent", "allow-wrong-port", "allow-other-name", "metadata", "synthetic-metadata"}

type Fixture struct {
	Case, Name, Port string
	DNS              *DNS
	TLS              *TLSFixture
	Certificates     *Certificates
	Mock             *mcpmock.Mock
	Dialer           mcp.Dialer
	Synthetic        *egress.CatalogueSyntheticDialer
}

func Start(caseName, scenarioRoot string) (*Fixture, error) {
	valid := false
	for _, c := range Cases {
		valid = valid || c == caseName
	}
	if !valid {
		return nil, errors.New("unknown catalogue fixture case")
	}
	name, san := PositiveName, PositiveName
	if caseName == "allow-other-name" {
		name, san = "cn30a-unlisted.test", "cn30a-unlisted.test"
	}
	if caseName == "metadata" || caseName == "synthetic-metadata" {
		name, san = MetadataName, MetadataName
	}
	if caseName == "tls-wrong-san" {
		san = "cn30a-wrong.test"
	}
	certs, err := NewCertificates(san)
	if err != nil {
		return nil, err
	}
	fix, err := mcpmock.LoadFixtures(filepath.Join(scenarioRoot, "mcp", "docs"))
	if err != nil {
		return nil, err
	}
	if len(fix.Tools) != 5 {
		return nil, errors.New("CN30a baseline must contain five tools")
	}
	era, err := mcpmock.ParseEra("dual")
	if err != nil {
		return nil, err
	}
	mock, err := mcpmock.New(mcpmock.Config{Era: era, Path: "/mcp", Auth: "none", ServerName: "catalogue-owned-docs"}, fix)
	if err != nil {
		return nil, err
	}
	tlsFixture, err := StartTLS(certs, mock.Handler())
	if err != nil {
		return nil, err
	}
	_, port, _ := net.SplitHostPort(tlsFixture.Address())
	mock.SetPublicURL("https://" + net.JoinHostPort(name, port))
	ip := net.ParseIP("127.0.0.1")
	if name == MetadataName {
		ip = net.ParseIP(identity.MetadataIP)
	}
	dns, err := StartDNS(name, port, ip)
	if err != nil {
		tlsFixture.Close()
		return nil, err
	}
	f := &Fixture{Case: caseName, Name: name, Port: port, DNS: dns, TLS: tlsFixture, Certificates: certs, Mock: mock}
	allow := []string{net.JoinHostPort(PositiveName, port)}
	switch caseName {
	case "allow-absent", "metadata", "synthetic-metadata":
		allow = nil
	case "allow-wrong-port":
		n, _ := strconv.Atoi(port)
		allow = []string{net.JoinHostPort(PositiveName, strconv.Itoa(n%65535+1))}
	}
	roots := certs.Roots
	if caseName == "tls-untrusted-ca" {
		other, err := NewCertificates(name)
		if err != nil {
			f.Close()
			return nil, err
		}
		roots = other.Roots
	}
	opts := egress.Options{PrivateAllow: allow, TLSConfig: &tls.Config{RootCAs: roots, MinVersion: tls.VersionTLS12}}
	address := net.JoinHostPort(name, port)
	if caseName == "synthetic-metadata" {
		f.Synthetic, err = egress.NewCatalogueSyntheticDialer(opts, address, tlsFixture.Address(), dns.Lookup)
		f.Dialer = f.Synthetic
	} else {
		f.Dialer, err = egress.NewCataloguePreflightDialer(opts, address, dns.Lookup)
	}
	if err != nil {
		f.Close()
		return nil, err
	}
	return f, nil
}

func (f *Fixture) Close() { f.DNS.Close(); f.TLS.Close() }

// Observation returns only counts, logical names and one-way synthetic
// credential fingerprints. The mock supplies the actual tools/list witness.
func (f *Fixture) Observation() map[string]any {
	lookups, queries, failures := f.DNS.Counts()
	physical := int64(0)
	if f.Synthetic != nil {
		physical = f.Synthetic.PhysicalAttempts()
	}
	return map[string]any{"case": f.Case, "name": f.Name, "port": f.Port,
		"resolver_calls": lookups, "dns_queries": queries, "dns_failures": failures,
		"synthetic_physical_attempts": physical, "tls": f.TLS.Witnesses(), "client_hello": f.TLS.ClientHellos(),
		"tools_list": f.Mock.State().ReceivedLast(0, "tools/list")}
}
