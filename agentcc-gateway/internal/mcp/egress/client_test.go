package egress

import (
	"crypto/tls"
	"crypto/x509"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/netguard"
)

// hostPort is the host:port a test server listens on.
func hostPort(t *testing.T, s *httptest.Server) string {
	t.Helper()
	u, err := url.Parse(s.URL)
	if err != nil {
		t.Fatal(err)
	}
	return u.Host
}

// counted is a test server that answers "reached" and counts its requests.
func counted(t *testing.T, hits *atomic.Int32) *httptest.Server {
	t.Helper()
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		w.Header().Set("X-Seen-Api-Key", r.Header.Get("X-Api-Key"))
		io.WriteString(w, "reached")
	}))
	t.Cleanup(s.Close)
	return s
}

// redirector redirects every request to target.
func redirector(t *testing.T, target string) *httptest.Server {
	t.Helper()
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, target, http.StatusFound)
	}))
	t.Cleanup(s.Close)
	return s
}

// TestRedirectRechecked: every redirect hop is checked again, its address at
// dial time and its scheme before it is followed; a hop the operator list
// does not open is refused and never reached.
func TestRedirectRechecked(t *testing.T) {
	var hits atomic.Int32
	target := counted(t, &hits)
	hop := redirector(t, target.URL+"/next")

	t.Run("a hop to an address not listed", func(t *testing.T) {
		_, err := mustNew(t, hostPort(t, hop)).HTTPClient(5 * time.Second).Get(hop.URL)
		var refused *RefusedError
		if !errors.As(err, &refused) || refused.Class != netguard.Loopback || refused.Reason != ReasonNotAllowed {
			t.Fatalf("err = %v, want the second hop refused as loopback", err)
		}
		if hits.Load() != 0 {
			t.Fatalf("the refused hop was reached %d times", hits.Load())
		}
	})

	t.Run("a hop to the metadata service", func(t *testing.T) {
		meta := redirector(t, "http://169.254.169.254/latest/meta-data/iam/security-credentials/")
		_, err := mustNew(t, hostPort(t, meta), "169.254.169.254").HTTPClient(5 * time.Second).Get(meta.URL)
		var refused *RefusedError
		if !errors.As(err, &refused) || refused.Class != netguard.Forbidden {
			t.Fatalf("err = %v, want the metadata hop refused as forbidden", err)
		}
	})

	t.Run("a hop to a scheme that is not http(s)", func(t *testing.T) {
		file := redirector(t, "file:///etc/passwd")
		_, err := mustNew(t, hostPort(t, file)).HTTPClient(5 * time.Second).Get(file.URL)
		if !errors.Is(err, ErrRedirectScheme) {
			t.Fatalf("err = %v, want ErrRedirectScheme", err)
		}
	})

	t.Run("a listed hop is followed; credentials do not cross origins", func(t *testing.T) {
		client := mustNew(t, hostPort(t, hop), hostPort(t, target)).HTTPClient(5*time.Second, "X-Api-Key")
		req, _ := http.NewRequest(http.MethodGet, hop.URL, nil)
		req.Header.Set("X-Api-Key", "probe-test-key")
		resp, err := client.Do(req)
		if err != nil {
			t.Fatalf("listed hops: %v", err)
		}
		body, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		if string(body) != "reached" || hits.Load() != 1 {
			t.Fatalf("body %q, hits %d; want the target reached once", body, hits.Load())
		}
		if got := resp.Header.Get("X-Seen-Api-Key"); got != "" {
			t.Fatalf("the credential header reached another origin: %q", got)
		}
	})

	t.Run("redirects are bounded", func(t *testing.T) {
		var loop *httptest.Server
		loop = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			http.Redirect(w, r, loop.URL+r.URL.Path+"x", http.StatusFound)
		}))
		defer loop.Close()
		_, err := mustNew(t, hostPort(t, loop)).HTTPClient(5 * time.Second).Get(loop.URL + "/")
		if !errors.Is(err, ErrTooManyRedirects) {
			t.Fatalf("err = %v, want the redirect limit", err)
		}
	})
}

// TestHTTPSRequired: plaintext HTTP goes only to an address in private_allow
// (the local mocks); anywhere else needs TLS. The refusal comes before the
// connection is made.
func TestHTTPSRequired(t *testing.T) {
	d := mustNew(t)

	t.Run("the decision, for public addresses", func(t *testing.T) {
		for _, addr := range []string{"192.0.2.10:80", "[2001:db8::1]:8080"} {
			var refused *RefusedError
			if err := d.check("api.example.com", addr, true); !errors.As(err, &refused) ||
				refused.Reason != ReasonHTTPSRequired || refused.Class != netguard.Public {
				t.Errorf("plaintext to %s: %v, want https_required", addr, err)
			}
			if err := d.check("api.example.com", addr, false); err != nil {
				t.Errorf("TLS to %s: %v, want it allowed", addr, err)
			}
		}
	})

	t.Run("the client refuses plaintext to a public address", func(t *testing.T) {
		// 192.0.2.0/24 is TEST-NET-1: public to netguard, and refused before
		// any connection is attempted.
		_, err := d.HTTPClient(2 * time.Second).Get("http://192.0.2.10:9/mcp")
		var refused *RefusedError
		if !errors.As(err, &refused) || refused.Reason != ReasonHTTPSRequired {
			t.Fatalf("err = %v, want https_required", err)
		}
	})

	plain := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { io.WriteString(w, "plain") }))
	defer plain.Close()
	tlsSrv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { io.WriteString(w, "tls") }))
	defer tlsSrv.Close()
	pool := x509.NewCertPool()
	pool.AddCert(tlsSrv.Certificate())

	t.Run("plaintext to a listed address", func(t *testing.T) {
		resp, err := mustNew(t, hostPort(t, plain)).HTTPClient(5 * time.Second).Get(plain.URL)
		if err != nil {
			t.Fatalf("listed plaintext: %v", err)
		}
		resp.Body.Close()
	})

	t.Run("TLS dials through the guard", func(t *testing.T) {
		listed, err := New(Options{PrivateAllow: []string{hostPort(t, tlsSrv)}, TLSConfig: &tls.Config{RootCAs: pool}})
		if err != nil {
			t.Fatal(err)
		}
		resp, err := listed.HTTPClient(5 * time.Second).Get(tlsSrv.URL)
		if err != nil {
			t.Fatalf("TLS to a listed address: %v", err)
		}
		body, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		if string(body) != "tls" {
			t.Fatalf("body %q", body)
		}
		// The same TLS server with nothing listed: still refused, as loopback.
		strict, _ := New(Options{TLSConfig: &tls.Config{RootCAs: pool}})
		_, err = strict.HTTPClient(5 * time.Second).Get(tlsSrv.URL)
		var refused *RefusedError
		if !errors.As(err, &refused) || refused.Class != netguard.Loopback {
			t.Fatalf("TLS to an unlisted loopback server: err = %v, want a loopback refusal", err)
		}
		// And the certificate is verified: no system root signs the test cert.
		noRoots, _ := New(Options{PrivateAllow: []string{hostPort(t, tlsSrv)}})
		if _, err := noRoots.HTTPClient(5 * time.Second).Get(tlsSrv.URL); err == nil {
			t.Fatal("an unverifiable certificate was accepted")
		}
	})

	t.Run("no proxy from the environment", func(t *testing.T) {
		client := d.HTTPClient(2 * time.Second)
		if tr, ok := client.Transport.(*http.Transport); !ok || tr.Proxy != nil {
			t.Fatalf("transport %T has a proxy func; connections must dial the destination through the guard", client.Transport)
		}
		t.Setenv("HTTP_PROXY", plain.URL)
		t.Setenv("HTTPS_PROXY", plain.URL)
		_, err := client.Get("http://192.0.2.10:9/mcp")
		var refused *RefusedError
		if !errors.As(err, &refused) || refused.Reason != ReasonHTTPSRequired {
			t.Fatalf("err = %v: the request went through the environment's proxy instead of the guard", err)
		}
	})
}
