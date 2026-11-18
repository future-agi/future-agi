package netguard

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync"
	"testing"
)

// testProxy is a forward proxy on loopback that answers every request itself
// and records what it was asked for: the absolute URL of a plain request, the
// authority of a CONNECT (which it refuses).
type testProxy struct {
	*httptest.Server
	mu    sync.Mutex
	asked []string
}

func newTestProxy(t *testing.T) *testProxy {
	t.Helper()
	p := &testProxy{}
	p.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		p.mu.Lock()
		if r.Method == http.MethodConnect {
			p.asked = append(p.asked, "CONNECT "+r.Host)
		} else {
			p.asked = append(p.asked, r.Method+" "+r.URL.String())
		}
		p.mu.Unlock()
		if r.Method == http.MethodConnect {
			w.WriteHeader(http.StatusForbidden)
			return
		}
		io.WriteString(w, "via proxy")
	}))
	t.Cleanup(p.Close)
	return p
}

func (p *testProxy) requests() []string {
	p.mu.Lock()
	defer p.mu.Unlock()
	return append([]string(nil), p.asked...)
}

func (p *testProxy) url(t *testing.T) *url.URL {
	t.Helper()
	u, err := url.Parse(p.URL)
	if err != nil {
		t.Fatal(err)
	}
	return u
}

// stubLookup makes checkHost see the given addresses for host names.
func stubLookup(t *testing.T, answers map[string][]string) {
	t.Helper()
	orig := lookupIPAddr
	lookupIPAddr = func(_ context.Context, host string) ([]net.IPAddr, error) {
		addrs, ok := answers[host]
		if !ok {
			return nil, &net.DNSError{Err: "no such host", Name: host, IsNotFound: true}
		}
		ips := make([]net.IPAddr, len(addrs))
		for i, a := range addrs {
			ips[i] = net.IPAddr{IP: net.ParseIP(a)}
		}
		return ips, nil
	}
	t.Cleanup(func() { lookupIPAddr = orig })
}

func proxiedClient(t *testing.T, allowPrivate bool, proxy func(*http.Request) (*url.URL, error)) *http.Client {
	t.Helper()
	dial, pick := Proxied(net.Dialer{}, allowPrivate, proxy)
	transport := &http.Transport{DialContext: dial, Proxy: pick}
	t.Cleanup(transport.CloseIdleConnections)
	return &http.Client{Transport: transport}
}

// expectRefused checks that a request was refused by the address check, with
// an error that leaves the address out.
func expectRefused(t *testing.T, client *http.Client, target string, leaks ...string) {
	t.Helper()
	resp, err := client.Get(target)
	if err == nil {
		resp.Body.Close()
		t.Fatalf("GET %s: status %d, want refused", target, resp.StatusCode)
	}
	var blocked *BlockedError
	if !errors.As(err, &blocked) {
		t.Fatalf("GET %s: %v, want a *BlockedError", target, err)
	}
	for _, leak := range leaks {
		if strings.Contains(err.Error(), leak) {
			t.Errorf("GET %s: error %q names the address %s", target, err, leak)
		}
	}
}

// Through a proxy the dial reaches the proxy, so the destination is checked
// before the proxy is picked: an internal destination never reaches the
// proxy, while the proxy itself, on loopback, may be dialled.
func TestProxiedChecksTheDestinationNotTheProxy(t *testing.T) {
	proxy := newTestProxy(t)
	stubLookup(t, map[string][]string{
		"public.example":  {"203.0.113.7"},
		"rebind.example":  {"203.0.113.7", "10.0.0.5"},
		"private.example": {"10.0.0.5"},
	})
	client := proxiedClient(t, false, http.ProxyURL(proxy.url(t)))

	resp, err := client.Get("http://public.example/ok")
	if err != nil {
		t.Fatalf("a public destination through the proxy: %v", err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if string(body) != "via proxy" {
		t.Fatalf("body = %q, want the proxy's answer", body)
	}

	for _, tt := range []struct {
		target string
		leaks  []string
	}{
		{"http://169.254.169.254/latest/meta-data/", nil},
		{"https://169.254.169.254/", nil},
		{"http://[fd00:ec2::254]/", nil},
		{"http://10.1.2.3:8080/", nil},
		{"http://127.0.0.1:8080/", nil},
		{"http://[::1]/", nil},
		{"http://0.0.0.0/", nil},
		{"http://metadata.google.internal/computeMetadata/v1/", nil},
		{"http://METADATA.goog./", nil},
		// Every address a host resolves to must pass, not only the first.
		{"http://rebind.example/", []string{"10.0.0.5"}},
		{"https://private.example/", []string{"10.0.0.5"}},
	} {
		t.Run(tt.target, func(t *testing.T) {
			expectRefused(t, client, tt.target, tt.leaks...)
		})
	}

	if got := proxy.requests(); len(got) != 1 || got[0] != "GET http://public.example/ok" {
		t.Fatalf("the proxy was asked for %q, want only the public destination", got)
	}
}

// With the opt-in a private destination may go through the proxy; loopback
// and metadata addresses never do.
func TestProxiedPrivateDestinationNeedsOptIn(t *testing.T) {
	proxy := newTestProxy(t)
	client := proxiedClient(t, true, http.ProxyURL(proxy.url(t)))

	resp, err := client.Get("http://10.1.2.3:8080/model")
	if err != nil {
		t.Fatalf("a private destination with the opt-in: %v", err)
	}
	resp.Body.Close()
	expectRefused(t, client, "http://127.0.0.1:8080/")
	expectRefused(t, client, "http://169.254.169.254/")

	if got := proxy.requests(); len(got) != 1 || got[0] != "GET http://10.1.2.3:8080/model" {
		t.Fatalf("the proxy was asked for %q", got)
	}
}

// A host the proxy function sends direct (NO_PROXY) is still checked, and the
// exemption for the proxy's address does not let a request reach the proxy
// as its destination.
func TestProxiedDirectRequestsAreStillChecked(t *testing.T) {
	proxy := newTestProxy(t)
	proxyURL := proxy.url(t)
	stubLookup(t, map[string][]string{
		"public.example":   {"203.0.113.7"},
		"internal.example": {"10.0.0.8"},
	})
	direct := map[string]bool{"internal.example": true, "127.0.0.1": true}
	client := proxiedClient(t, false, func(req *http.Request) (*url.URL, error) {
		if direct[req.URL.Hostname()] {
			return nil, nil
		}
		return proxyURL, nil
	})

	// The proxy's address is now exempt from the dial check.
	resp, err := client.Get("http://public.example/")
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()

	expectRefused(t, client, "http://internal.example/", "10.0.0.8")
	expectRefused(t, client, proxy.URL+"/")

	if got := proxy.requests(); len(got) != 1 {
		t.Fatalf("the proxy was asked for %q, want only the public destination", got)
	}
}

// Over HTTP/1.1 the host is checked on every request, one sent over a pooled
// connection to the proxy included, and a lookup that fails fails the
// request: the check fails closed.
func TestProxiedChecksEveryHTTP1RequestAndFailsClosed(t *testing.T) {
	proxy := newTestProxy(t)
	answers := map[string][]string{"public.example": {"203.0.113.7"}}
	stubLookup(t, answers)
	client := proxiedClient(t, false, http.ProxyURL(proxy.url(t)))

	resp, err := client.Get("http://public.example/first")
	if err != nil {
		t.Fatal(err)
	}
	io.Copy(io.Discard, resp.Body)
	resp.Body.Close()

	delete(answers, "public.example") // the next lookup fails
	if resp, err := client.Get("http://public.example/second"); err == nil {
		resp.Body.Close()
		t.Fatalf("status %d after a failed lookup, want the request refused", resp.StatusCode)
	}
	if got := proxy.requests(); len(got) != 1 {
		t.Fatalf("the proxy was asked for %q, want only the first request", got)
	}
}

// newTunnelProxy is a forward proxy on loopback that serves every CONNECT, as
// for an HTTPS destination, with a tunnel to target, and records the
// authorities it was asked for.
func newTunnelProxy(t *testing.T, target string) *testProxy {
	t.Helper()
	p := &testProxy{}
	p.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		p.mu.Lock()
		p.asked = append(p.asked, r.Method+" "+r.Host)
		p.mu.Unlock()
		if r.Method != http.MethodConnect {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		upstream, err := net.Dial("tcp", target)
		if err != nil {
			w.WriteHeader(http.StatusBadGateway)
			return
		}
		defer upstream.Close()
		conn, buffered, err := http.NewResponseController(w).Hijack()
		if err != nil {
			return
		}
		defer conn.Close()
		io.WriteString(conn, "HTTP/1.1 200 Connection established\r\n\r\n")
		go func() {
			io.Copy(upstream, buffered.Reader)
			upstream.Close()
		}()
		io.Copy(conn, upstream)
	}))
	t.Cleanup(p.Close)
	return p
}

// Through a CONNECT tunnel, as to an HTTPS vendor, a request that needs a new
// connection is checked. Over HTTP/1.1 every other request is too, and a
// failed lookup fails it; a request on an open HTTP/2 connection reuses the
// check made when that connection was opened.
func TestProxiedHTTPSChecksEachConnectionAndEachHTTP1Request(t *testing.T) {
	for _, http2 := range []bool{false, true} {
		t.Run(fmt.Sprintf("http2=%v", http2), func(t *testing.T) {
			upstream := httptest.NewUnstartedServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
			upstream.EnableHTTP2 = http2
			upstream.StartTLS()
			t.Cleanup(upstream.Close)
			proxy := newTunnelProxy(t, upstream.Listener.Addr().String())
			// The test server's certificate names example.com.
			answers := map[string][]string{"example.com": {"203.0.113.7"}}
			stubLookup(t, answers)
			roots := x509.NewCertPool()
			roots.AddCert(upstream.Certificate())
			// Set up as the decision providers' transports are.
			dial, pick := Proxied(net.Dialer{}, false, http.ProxyURL(proxy.url(t)))
			transport := &http.Transport{DialContext: dial, Proxy: pick, ForceAttemptHTTP2: true, TLSClientConfig: &tls.Config{RootCAs: roots}}
			t.Cleanup(transport.CloseIdleConnections)
			client := &http.Client{Transport: transport}
			get := func(path string) (int, error) {
				resp, err := client.Get("https://example.com" + path)
				if err != nil {
					return 0, err
				}
				io.Copy(io.Discard, resp.Body)
				resp.Body.Close()
				return resp.ProtoMajor, nil
			}

			wantProto := 1
			if http2 {
				wantProto = 2
			}
			if proto, err := get("/first"); err != nil || proto != wantProto {
				t.Fatalf("first request: HTTP/%d, %v; want HTTP/%d", proto, err, wantProto)
			}
			delete(answers, "example.com") // every lookup from now on fails
			_, err := get("/second")
			if http2 && err != nil {
				t.Fatalf("a request on the open HTTP/2 connection: %v", err)
			}
			if !http2 && err == nil {
				t.Fatal("over HTTP/1.1 a request was sent after its lookup failed")
			}
			transport.CloseIdleConnections()
			if _, err := get("/third"); err == nil {
				t.Fatal("a new connection was opened after the lookup failed")
			}
			if got := proxy.requests(); len(got) != 1 || got[0] != "CONNECT example.com:443" {
				t.Fatalf("the proxy was asked for %q, want one tunnel", got)
			}
		})
	}
}

// Only an address the proxy function returned skips the dial check, and only
// once it has been returned.
func TestProxiedDialExemptsOnlyThePickedProxy(t *testing.T) {
	proxy := newTestProxy(t)
	proxyURL := proxy.url(t)
	other := newTestProxy(t)
	dial, pick := Proxied(net.Dialer{}, false, http.ProxyURL(proxyURL))
	ctx := context.Background()

	var blocked *BlockedError
	if _, err := dial(ctx, "tcp", proxyURL.Host); !errors.As(err, &blocked) {
		t.Fatalf("dial before the proxy was picked = %v, want refused", err)
	}

	req, _ := http.NewRequest(http.MethodGet, "http://203.0.113.7/", nil)
	if got, err := pick(req); err != nil || got.String() != proxyURL.String() {
		t.Fatalf("pick = %v, %v", got, err)
	}
	conn, err := dial(ctx, "tcp", proxyURL.Host)
	if err != nil {
		t.Fatalf("dial the picked proxy: %v", err)
	}
	conn.Close()
	if _, err := dial(ctx, "tcp", strings.TrimPrefix(other.URL, "http://")); !errors.As(err, &blocked) {
		t.Fatalf("dial another loopback address = %v, want refused", err)
	}
}

func TestProxyAddr(t *testing.T) {
	for raw, want := range map[string]string{
		"http://proxy.corp:3128":    "proxy.corp:3128",
		"http://proxy.corp":         "proxy.corp:80",
		"https://proxy.corp":        "proxy.corp:443",
		"socks5://proxy.corp":       "proxy.corp:1080",
		"http://user:pw@[fd00::1]":  "[fd00::1]:80",
		"https://10.0.0.1:8443/pac": "10.0.0.1:8443",
	} {
		u, err := url.Parse(raw)
		if err != nil {
			t.Fatal(err)
		}
		if got := proxyAddr(u); got != want {
			t.Errorf("proxyAddr(%s) = %q, want %q", raw, got, want)
		}
	}
}
