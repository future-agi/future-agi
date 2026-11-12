package main

import (
	"encoding/pem"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
)

// The cloud process gate maps example.com (the httptest certificate's SAN) to
// loopback inside a network-none container. Neither DNS nor vendor traffic is
// possible. Only the trusted control uses SSL_CERT_FILE; no skip_tls anywhere.
func TestHostedOneKeyProcessUntrustedCAFailsClosed(t *testing.T) {
	binary := hostedProcessBinary(t)
	var requests atomic.Int32
	upstream := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests.Add(1)
		w.WriteHeader(http.StatusOK)
	}))
	upstream.Config.ErrorLog = log.New(io.Discard, "", 0)
	upstream.StartTLS()
	defer upstream.Close()
	_, port, err := net.SplitHostPort(upstream.Listener.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	trust := filepath.Join(t.TempDir(), "synthetic-ca.pem")
	if err := os.WriteFile(trust, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: upstream.Certificate().Raw}), 0600); err != nil {
		t.Fatal(err)
	}
	for _, trusted := range []bool{false, true} {
		t.Run(fmt.Sprint(trusted), func(t *testing.T) {
			// Unreachable Redis makes the trusted control stop after the TLS
			// gate, before any control-plane HTTP or provider registration.
			p, text := hostedProcessFixture(t, "http://127.0.0.1:9", freePort(t), processSecret(t))
			text += "managed: {spend_guard: {max_usd_per_minute: 5}}\n"
			text += fmt.Sprintf("providers: {synthetic: {type: openrouter, base_url: 'https://example.com:%s', managed: true, auto_discover: false}}\n", port)
			if err := os.WriteFile(p.path, []byte(text), 0600); err != nil {
				t.Fatal(err)
			}
			p.env = append(p.env, "AGENTCC_HOSTED_ONEKEY_ACTIVATION=true", "SSL_CERT_DIR="+t.TempDir())
			if trusted {
				p.env = append(p.env, "SSL_CERT_FILE="+trust)
			} else {
				p.env = append(p.env, "SSL_CERT_FILE="+filepath.Join(t.TempDir(), "absent.pem"))
			}
			p.start(t, binary)
			p.wait(t, false)
			want := "hosted one-key provider TLS verification failed"
			if trusted {
				want = "hosted shared Redis initialization failed"
			}
			if !strings.Contains(p.out.String(), want) {
				t.Fatalf("startup did not reach expected gate %q", want)
			}
			if processStatus(p.url+"/livez") != 0 {
				t.Fatal("failed startup exposed a listener")
			}
		})
	}
	if requests.Load() != 0 {
		t.Fatal("TLS preflight sent an HTTP request or fell back to insecure transport")
	}
}
