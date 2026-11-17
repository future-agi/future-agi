//go:build cataloguepreflighttest

package egress

import (
	"context"
	"errors"
	"net"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/netguard"
	"gopkg.in/yaml.v3"
)

// These tests never listen, connect, or perform DNS. Successful resolution is
// checked at the address-planning boundary; actual transport entrypoints stop
// at an asserted resolver sentinel or the unchanged product refusal.
func preflightFixture(t *testing.T, address string, allow []string, lookup CataloguePreflightLookup) *CataloguePreflightDialer {
	t.Helper()
	d, err := NewCataloguePreflightDialer(Options{PrivateAllow: allow, DialTimeout: time.Second}, address, lookup)
	if err != nil {
		t.Fatal(err)
	}
	return d
}

func TestCataloguePreflightNumericPlanPreservesNameAndPort(t *testing.T) {
	calls := 0
	d := preflightFixture(t, "cn30a-docs.test:8443", []string{"cn30a-docs.test:8443"}, func(_ context.Context, host, port string) ([]net.IPAddr, error) {
		calls++
		if host != "cn30a-docs.test" || port != "8443" {
			t.Fatalf("resolver input = %q:%q", host, port)
		}
		return []net.IPAddr{{IP: net.ParseIP("127.0.0.1")}, {IP: net.ParseIP("::1")}}, nil
	})
	for range 2 {
		got, err := d.guard.cataloguePreflightAddresses(context.Background(), d, false)
		if err != nil || !reflect.DeepEqual(got, []string{"127.0.0.1:8443", "[::1]:8443"}) {
			t.Fatalf("numeric plan = %v, %v", got, err)
		}
	}
	if calls != 2 {
		t.Fatalf("resolver calls = %d, want one fresh lookup per dial plan", calls)
	}
}

func TestCataloguePreflightNameBasedAllowRemainsExact(t *testing.T) {
	for _, tc := range []struct {
		name, address string
		allow         []string
	}{
		{"missing", "cn30a-docs.test:8443", nil},
		{"wrong-name", "cn30a-unlisted.test:8443", []string{"cn30a-docs.test:8443"}},
		{"wrong-port", "cn30a-docs.test:8444", []string{"cn30a-docs.test:8443"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			calls := 0
			d := preflightFixture(t, tc.address, tc.allow, func(context.Context, string, string) ([]net.IPAddr, error) {
				calls++
				return []net.IPAddr{{IP: net.ParseIP("127.0.0.1")}}, nil
			})
			conn, err := d.DialContext(context.Background(), "tcp", tc.address)
			var refused *RefusedError
			if conn != nil || !errors.As(err, &refused) || refused.Class != netguard.Loopback || refused.Reason != ReasonNotAllowed || calls != 1 {
				t.Fatalf("dial = %v, %v; resolver calls = %d", conn, err, calls)
			}
		})
	}
}

func TestCataloguePreflightMetadataWinsBeforeAnyPhysicalDial(t *testing.T) {
	for _, answers := range [][]net.IPAddr{
		{{IP: net.ParseIP("169.254.169.254")}},
		{{IP: net.ParseIP("127.0.0.1")}, {IP: net.ParseIP("169.254.169.254")}},
		{{IP: net.ParseIP("::ffff:169.254.169.254")}},
	} {
		calls := 0
		d := preflightFixture(t, "metadata.google.internal:8443", []string{"metadata.google.internal:8443"}, func(context.Context, string, string) ([]net.IPAddr, error) {
			calls++
			return answers, nil
		})
		conn, err := d.DialContext(context.Background(), "tcp", d.address)
		var refused *RefusedError
		if conn != nil || !errors.As(err, &refused) || refused.Class != netguard.Forbidden || refused.Reason != ReasonForbidden || calls != 1 {
			t.Fatalf("metadata dial = %v, %v; resolver calls = %d", conn, err, calls)
		}
	}
}

func TestCataloguePreflightActualHTTPAndTLSUseResolver(t *testing.T) {
	for _, scheme := range []string{"http", "https"} {
		t.Run(scheme, func(t *testing.T) {
			sentinel := errors.New("fixture resolver reached before any network attempt")
			calls := 0
			d := preflightFixture(t, "cn30a-docs.test:8443", []string{"cn30a-docs.test:8443"}, func(ctx context.Context, host, port string) ([]net.IPAddr, error) {
				calls++
				if host != "cn30a-docs.test" || port != "8443" {
					t.Errorf("logical address changed: %s:%s", host, port)
				}
				if _, ok := ctx.Deadline(); !ok {
					t.Error("resolver has no dial timeout")
				}
				return nil, sentinel
			})
			client := d.HTTPClient(time.Second, "X-Fixture-Credential")
			defer client.CloseIdleConnections()
			resp, err := client.Get(scheme + "://" + d.address + "/mcp")
			if resp != nil || !errors.Is(err, sentinel) || calls != 1 {
				t.Fatalf("actual transport = %v, %v; resolver calls = %d", resp, err, calls)
			}
		})
	}
}

func TestCataloguePreflightNoFallbackOrImplicitRegistration(t *testing.T) {
	calls := 0
	d := preflightFixture(t, "cn30a-docs.test:8443", nil, func(context.Context, string, string) ([]net.IPAddr, error) {
		calls++
		return nil, errors.New("must not resolve an unregistered address")
	})
	for _, address := range []string{"localhost:8443", "cn30a-other.test:8443", "cn30a-docs.test:8444", "127.0.0.1:8443"} {
		if conn, err := d.DialContext(context.Background(), "tcp", address); conn != nil || err == nil {
			t.Fatalf("unregistered %q = %v, %v", address, conn, err)
		}
	}
	if conn, err := d.DialContext(context.Background(), "udp", d.address); conn != nil || err == nil {
		t.Fatalf("unexpected UDP dial = %v, %v", conn, err)
	}
	if calls != 0 {
		t.Fatalf("unregistered lookup calls = %d", calls)
	}
	other := mustNew(t)
	if conn, err := other.dial(d.context(context.Background()), "tcp", d.address, false); conn != nil || err == nil {
		t.Fatalf("fixture context leaked across dialers: %v, %v", conn, err)
	}
}

func TestCataloguePreflightMalformedAndNonLoopbackAnswersFailClosed(t *testing.T) {
	for _, answers := range [][]net.IPAddr{
		nil,
		{{IP: nil}},
		{{IP: net.IP{1, 2}}},
		{{IP: net.ParseIP("::1"), Zone: "fixture-zone"}},
		{{IP: net.ParseIP("192.0.2.1")}},
	} {
		calls := 0
		d := preflightFixture(t, "cn30a-docs.test:8443", []string{"cn30a-docs.test:8443"}, func(context.Context, string, string) ([]net.IPAddr, error) {
			calls++
			return answers, nil
		})
		if conn, err := d.DialContext(context.Background(), "tcp", d.address); conn != nil || err == nil || calls != 1 {
			t.Fatalf("invalid answers = %v, %v; resolver calls = %d", conn, err, calls)
		}
	}
}

func TestCataloguePreflightLocalhostRemainsRejected(t *testing.T) {
	lookup := func(context.Context, string, string) ([]net.IPAddr, error) {
		t.Fatal("invalid fixture must not invoke resolver")
		return nil, nil
	}
	for _, address := range []string{"localhost:8443", "127.0.0.1:8443", "cn30a-docs.test:0", "cn30a-docs.test:08443", "cn30a-docs.test:65536"} {
		if _, err := NewCataloguePreflightDialer(Options{}, address, lookup); err == nil {
			t.Errorf("fixture accepted %q", address)
		}
	}
	if _, err := NewCataloguePreflightDialer(Options{}, "cn30a-docs.test:8443", nil); err == nil {
		t.Error("nil resolver accepted")
	}

	// Exercise the actual unchanged CN30a provider variable pattern, not a
	// test-owned relaxed copy. Django's separate _HOSTNAME rule is unchanged.
	path := filepath.Join("..", "..", "..", "scripts", "scenarios", "fixtures", "CN30a", "catalog", "apps", "cn30a-docs", "providers", "key.yaml.in")
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Transport struct {
			URLVariables map[string]struct {
				Pattern string `yaml:"pattern"`
			} `yaml:"urlVariables"`
		} `yaml:"transport"`
	}
	if err := yaml.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	pattern := fixture.Transport.URLVariables["mockHost"].Pattern
	if pattern == "" {
		t.Fatal("CN30a mockHost pattern missing")
	}
	re := regexp.MustCompile(pattern)
	if re.MatchString("localhost") || !re.MatchString("cn30a-docs.test") {
		t.Fatalf("CN30a must reject localhost and accept the dotted fixture: %s", pattern)
	}
}
