package guardrails

import (
	"context"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/netguard"
)

func TestTenantHTTPClientRedirects(t *testing.T) {
	for _, destination := range []string{
		"http://127.0.0.1:80", "http://10.1.2.3:80", "http://169.254.169.254:80",
		"https://127.0.0.1:443", "http://localhost:80",
	} {
		t.Run(destination, func(t *testing.T) {
			var hits atomic.Int32
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				hits.Add(1)
				http.Redirect(w, r, destination, http.StatusFound)
			}))
			defer srv.Close()

			client := TenantHTTPClient(time.Second, false)
			defer client.CloseIdleConnections()
			transport := client.Transport.(*http.Transport)
			if transport.Proxy != nil {
				t.Fatal("tenant transport must not use an environment proxy")
			}
			guardedDial := transport.DialContext
			// Route only the initial synthetic public host to the fixture. Redirects
			// and every other address still go through the production guarded dialer.
			transport.DialContext = func(ctx context.Context, network, address string) (net.Conn, error) {
				if address == "203.0.113.1:80" {
					return (&net.Dialer{}).DialContext(ctx, network, srv.Listener.Addr().String())
				}
				return guardedDial(ctx, network, address)
			}

			resp, err := client.Get("http://203.0.113.1/start")
			if resp != nil {
				resp.Body.Close()
			}
			var blocked *netguard.BlockedError
			if !errors.As(err, &blocked) {
				t.Fatalf("redirect returned %v, want connection-time netguard refusal", err)
			}
			if hits.Load() != 1 {
				t.Fatalf("initial public endpoint hits = %d, want 1", hits.Load())
			}
		})
	}
}

func TestTenantHTTPClientDNSAndPrivateOptIn(t *testing.T) {
	for _, allowPrivate := range []bool{false, true} {
		client := TenantHTTPClient(time.Second, allowPrivate)
		defer client.CloseIdleConnections()
		transport := client.Transport.(*http.Transport)
		for _, address := range []string{"localhost:80", "10.1.2.3:80", "169.254.169.254:80"} {
			ctx, cancel := context.WithTimeout(context.Background(), time.Second)
			conn, err := transport.DialContext(ctx, "tcp", address)
			cancel()
			if conn != nil {
				conn.Close()
			}
			var blocked *netguard.BlockedError
			wantBlocked := address != "10.1.2.3:80" || !allowPrivate
			if errors.As(err, &blocked) != wantBlocked {
				t.Errorf("dial %s (allowPrivate=%v) = %v, want blocked=%v", address, allowPrivate, err, wantBlocked)
			}
		}
	}
}

func TestTenantHTTPClientBypassesEnvironmentProxies(t *testing.T) {
	var proxyHits atomic.Int32
	proxy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		proxyHits.Add(1)
		http.Error(w, "tenant traffic must not reach the environment proxy", http.StatusBadGateway)
	}))
	defer proxy.Close()
	t.Setenv("HTTP_PROXY", proxy.URL)
	t.Setenv("HTTPS_PROXY", proxy.URL)
	t.Setenv("http_proxy", proxy.URL)
	t.Setenv("https_proxy", proxy.URL)

	for _, scheme := range []string{"http", "https"} {
		for _, noProxy := range []string{"", "*"} {
			t.Run(scheme+"/no_proxy="+noProxy, func(t *testing.T) {
				t.Setenv("NO_PROXY", noProxy)
				t.Setenv("no_proxy", noProxy)
				handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					io.WriteString(w, "ok")
				})
				var srv *httptest.Server
				if scheme == "http" {
					srv = httptest.NewServer(handler)
				} else {
					srv = httptest.NewTLSServer(handler)
				}
				defer srv.Close()

				client := TenantHTTPClient(time.Second, false)
				defer client.CloseIdleConnections()
				transport := client.Transport.(*http.Transport)
				if transport.Proxy != nil {
					t.Fatal("tenant transport must not delegate destination resolution to a proxy")
				}
				if scheme == "https" {
					transport.TLSClientConfig = srv.Client().Transport.(*http.Transport).TLSClientConfig.Clone()
					transport.TLSClientConfig.ServerName = "127.0.0.1"
				}
				transport.DialContext = func(ctx context.Context, network, address string) (net.Conn, error) {
					if host, _, _ := net.SplitHostPort(address); host != "203.0.113.1" {
						t.Errorf("unexpected dial address %s", address)
					}
					return (&net.Dialer{}).DialContext(ctx, network, srv.Listener.Addr().String())
				}
				resp, err := client.Get(scheme + "://203.0.113.1/")
				if err != nil {
					t.Fatal(err)
				}
				defer resp.Body.Close()
				body, err := io.ReadAll(resp.Body)
				if err != nil || strings.TrimSpace(string(body)) != "ok" {
					t.Fatalf("public response = %q, %v", body, err)
				}
			})
		}
	}
	if proxyHits.Load() != 0 {
		t.Fatalf("environment proxy received %d requests", proxyHits.Load())
	}
}
