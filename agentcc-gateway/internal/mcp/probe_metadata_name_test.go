package mcp

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp/egress"
)

// resolvedProbeDialer replaces DNS only, not the product's HTTP transport,
// post-resolution SSRF check or probe error mapping. Resolve names through the
// injected resolver before handing each literal to the REAL product dialer:
// the outer test guard otherwise refuses a hostname before DNS can run (746e).
// This is deliberately not evidence that the unmodified hostname dial reaches
// DNS under that guard; the separate 97a invocation proves its masked result.
type resolvedProbeDialer struct {
	*egress.Dialer
	resolver *probeDNSResolver
}

func (d resolvedProbeDialer) HTTPClient(timeout time.Duration, sensitive ...string) *http.Client {
	client := d.Dialer.HTTPClient(timeout, sensitive...)
	transport := client.Transport.(*http.Transport)
	productDial := transport.DialContext
	transport.DialContext = func(ctx context.Context, network, address string) (net.Conn, error) {
		host, port, err := net.SplitHostPort(address)
		if err != nil {
			return nil, err
		}
		// An absolute name avoids the VM's resolver search suffixes, including
		// for the single-label metadata alias. Every query goes to our stub.
		ips, err := d.resolver.LookupIP(ctx, "ip4", host+".")
		if err != nil {
			return nil, err
		}
		if len(ips) != 1 || !ips[0].Equal(net.ParseIP("169.254.169.254")) {
			return nil, fmt.Errorf("unexpected loopback DNS fixture answer")
		}
		return productDial(ctx, network, net.JoinHostPort(ips[0].String(), port))
	}
	return client
}

// The stub accepts exactly one uncompressed IN/A question for the fixture
// name and answers with one metadata address. It needs no external DNS library
// or module fetch. Resolver EDNS additions are not copied into the response.
func metadataDNSAnswer(query []byte, name string) ([]byte, error) {
	if len(query) < 12 || binary.BigEndian.Uint16(query[4:6]) != 1 {
		return nil, fmt.Errorf("malformed loopback DNS query")
	}
	pos := 12
	var labels []string
	for {
		if pos >= len(query) {
			return nil, fmt.Errorf("truncated loopback DNS question")
		}
		n := int(query[pos])
		pos++
		if n == 0 {
			break
		}
		if n > 63 || pos+n > len(query) {
			return nil, fmt.Errorf("invalid loopback DNS name")
		}
		labels = append(labels, string(query[pos:pos+n]))
		pos += n
	}
	if strings.Join(labels, ".") != name || pos+4 > len(query) ||
		binary.BigEndian.Uint16(query[pos:pos+2]) != 1 || binary.BigEndian.Uint16(query[pos+2:pos+4]) != 1 {
		return nil, fmt.Errorf("unexpected loopback DNS question")
	}
	// Header: response, authoritative, recursion available; one question and
	// one answer. Answer name points at the question; IN/A, TTL 0, four bytes.
	answer := []byte{query[0], query[1], 0x85, 0x80, 0, 1, 0, 1, 0, 0, 0, 0}
	answer = append(answer, query[12:pos+4]...)
	return append(answer, 0xc0, 0x0c, 0, 1, 0, 1, 0, 0, 0, 0, 0, 4, 169, 254, 169, 254), nil
}

func Test746eProbeMetadataNamesAfterLoopbackDNS(t *testing.T) {
	for _, name := range []string{"metadata.google.internal", "metadata"} {
		t.Run(name, func(t *testing.T) {
			var queries atomic.Int32
			dns, err := net.ListenPacket("udp4", "127.0.0.1:0")
			if err != nil {
				t.Fatal(err)
			}
			defer dns.Close()
			done := make(chan struct{})
			go func() {
				defer close(done)
				buf := make([]byte, 4096)
				for {
					n, peer, err := dns.ReadFrom(buf)
					if err != nil {
						return
					}
					wire, err := metadataDNSAnswer(buf[:n], name)
					if err != nil {
						t.Error(err)
						return
					}
					queries.Add(1)
					if _, err := dns.WriteTo(wire, peer); err != nil {
						t.Error(err)
						return
					}
				}
			}()
			defer func() { dns.Close(); <-done }()
			var resolverDials atomic.Int32
			resolver := &probeDNSResolver{Dial: func(ctx context.Context, network, _ string) (net.Conn, error) {
				resolverDials.Add(1)
				return (&net.Dialer{}).DialContext(ctx, "udp4", dns.LocalAddr().String())
			}}
			guard, err := egress.New(egress.Options{})
			if err != nil {
				t.Fatal(err)
			}
			s := NewServer(ServerConfig{})
			defer s.Close()
			s.SetDeps(Deps{Dialer: resolvedProbeDialer{guard, resolver}})
			// Preserve the runner's ledger: take a byte offset, never replace its
			// path or hide records from its zero-attempt audit. Without the cloud
			// overlay the product and DNS assertions still run normally.
			ledger := os.Getenv("CMP_GOGUARD_LOG")
			offset := int64(0)
			if ledger != "" {
				if st, err := os.Stat(ledger); err == nil {
					offset = st.Size()
				} else if !os.IsNotExist(err) {
					t.Fatal(err)
				}
			}
			res := s.Probe(context.Background(), ProbeRequest{OrgID: "org-a", URL: "http://" + name + "/computeMetadata/v1/", Transport: "http"})
			if res.OK || errorCode(res) != ProbeErrEgressRefused {
				t.Errorf("product returned ok=%v error=%s, want false egress_refused", res.OK, errorCode(res))
			}
			if queries.Load() == 0 || resolverDials.Load() == 0 {
				t.Fatal("injected resolver and loopback DNS stub were not reached")
			}
			if ledger != "" {
				wire, err := os.ReadFile(ledger)
				if err != nil {
					t.Fatal(err)
				}
				var refused, attempts int
				for _, line := range strings.Split(strings.TrimSpace(string(wire[offset:])), "\n") {
					var row struct {
						Kind, Host, Port string
						PID              int
					}
					if err := json.Unmarshal([]byte(line), &row); err != nil {
						t.Fatal(err)
					}
					// Other packages run concurrently and share the invocation's
					// ledger. Its tripwire still audits every process; this assertion
					// belongs only to this non-parallel test's process.
					if row.PID != os.Getpid() {
						continue
					}
					if row.Kind == "app-refused-before-connect" && row.Host == "169.254.169.254" && row.Port == "80" {
						refused++
					} else {
						attempts++
					}
				}
				if refused != 1 || attempts != 0 {
					t.Errorf("app-refused-before-connect=%d counted attempts=%d, want 1 and 0", refused, attempts)
				}
			}
		})
	}
}
