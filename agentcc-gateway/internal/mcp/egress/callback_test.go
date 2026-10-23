package egress

import (
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/netguard"
)

// A callback an
// organization's config names (a guardrail webhook or external guardrail
// service, an org alert channel) goes through the guard's address rules, as
// MCP upstreams do, with the operator list private_allow the only way to
// reach a private or loopback address.

// headerRecorder is a test server that keeps the headers of each request it
// gets and answers 200.
type headerRecorder struct {
	srv  *httptest.Server
	mu   sync.Mutex
	got  []http.Header
	hits atomic.Int32
}

func newHeaderRecorder(t *testing.T) *headerRecorder {
	t.Helper()
	h := &headerRecorder{}
	h.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		h.hits.Add(1)
		h.mu.Lock()
		h.got = append(h.got, r.Header.Clone())
		h.mu.Unlock()
		io.WriteString(w, "reached")
	}))
	t.Cleanup(h.srv.Close)
	return h
}

func (h *headerRecorder) last() http.Header {
	h.mu.Lock()
	defer h.mu.Unlock()
	if len(h.got) == 0 {
		return nil
	}
	return h.got[len(h.got)-1]
}

func postWithHeaders(client *http.Client, url string, headers map[string]string) (*http.Response, error) {
	req, err := http.NewRequest(http.MethodPost, url, strings.NewReader(`{"input":"x"}`))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	return client.Do(req)
}

// TestCallbackClientRefusesWhatTheGuardRefuses: with nothing listed, a
// loopback server is refused and never reached, a host name is refused for
// the address it resolves to, and metadata and private addresses are refused
// before any connection is made.
func TestCallbackClientRefusesWhatTheGuardRefuses(t *testing.T) {
	rec := newHeaderRecorder(t)
	client := mustNew(t).CallbackClient(5 * time.Second)

	for _, c := range []struct {
		name, url string
		class     netguard.Class
		reason    Reason
	}{
		{"a loopback server", rec.srv.URL + "/hook", netguard.Loopback, ReasonNotAllowed},
		{"a name that resolves to loopback", strings.Replace(rec.srv.URL, "127.0.0.1", "localhost", 1) + "/hook", netguard.Loopback, ReasonNotAllowed},
		{"the metadata service", "http://169.254.169.254/latest/meta-data/", netguard.Forbidden, ReasonForbidden},
		{"a private address", "http://10.0.0.5:8080/v2/guard", netguard.Private, ReasonNotAllowed},
	} {
		t.Run(c.name, func(t *testing.T) {
			_, err := postWithHeaders(client, c.url, nil)
			var refused *RefusedError
			if !errors.As(err, &refused) || refused.Class != c.class || refused.Reason != c.reason {
				t.Fatalf("err = %v, want a %v refusal (%s)", err, c.class, c.reason)
			}
		})
	}
	if n := rec.hits.Load(); n != 0 {
		t.Fatalf("a refused destination was reached %d times", n)
	}
}

// TestCallbackClientMetadataRefusedEvenWhenListed: no private_allow entry
// opens the metadata service.
func TestCallbackClientMetadataRefusedEvenWhenListed(t *testing.T) {
	_, err := postWithHeaders(mustNew(t, "169.254.169.254").CallbackClient(5*time.Second),
		"http://169.254.169.254/computeMetadata/v1/", nil)
	var refused *RefusedError
	if !errors.As(err, &refused) || refused.Class != netguard.Forbidden {
		t.Fatalf("err = %v, want the metadata service refused", err)
	}
}

// TestCallbackClientReachesAListedServer: the operator lists the server (a
// self-hosted LAN service in a deployment; a local test server here) and the
// callback reaches it, over plaintext http.
func TestCallbackClientReachesAListedServer(t *testing.T) {
	rec := newHeaderRecorder(t)
	resp, err := postWithHeaders(mustNew(t, hostPort(t, rec.srv)).CallbackClient(5*time.Second), rec.srv.URL+"/hook",
		map[string]string{"X-Org-Token": "planted-org-token"})
	if err != nil {
		t.Fatalf("listed server: %v", err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if string(body) != "reached" || rec.hits.Load() != 1 {
		t.Fatalf("body %q, hits %d; want the listed server reached once", body, rec.hits.Load())
	}
	if got := rec.last().Get("X-Org-Token"); got != "planted-org-token" {
		t.Fatalf("the org's own header did not reach its own URL: %q", got)
	}
}

// TestCallbackClientPlaintextIsNotRefusedByScheme: the guard decides by the
// address, not the scheme. HTTPClient (MCP) refuses plaintext to any address
// private_allow does not list; a callback may use plaintext http,
// so the callback client dials http:// with the address rules alone. The
// decision for a public address, without connecting to one:
func TestCallbackClientPlaintextIsNotRefusedByScheme(t *testing.T) {
	d := mustNew(t)
	for _, addr := range []string{"192.0.2.10:80", "[2001:db8::1]:8080"} {
		if err := d.check("hooks.example.com", addr, callbackPlaintext); err != nil {
			t.Errorf("callback plaintext to public %s: %v, want it allowed", addr, err)
		}
		var refused *RefusedError
		if err := d.check("hooks.example.com", addr, true); !errors.As(err, &refused) || refused.Reason != ReasonHTTPSRequired {
			t.Errorf("MCP plaintext to public %s: %v, want https_required", addr, err)
		}
	}
	// And the address rules still apply to a callback's plaintext dial.
	for _, c := range []struct {
		addr  string
		class netguard.Class
	}{{"127.0.0.1:80", netguard.Loopback}, {"10.1.2.3:80", netguard.Private}, {"169.254.169.254:80", netguard.Forbidden}} {
		var refused *RefusedError
		if err := d.check("hooks.example.com", c.addr, callbackPlaintext); !errors.As(err, &refused) || refused.Class != c.class {
			t.Errorf("callback plaintext to %s: %v, want a %v refusal", c.addr, err, c.class)
		}
	}
}

// TestCallbackClientRedirects: every hop is dialled, so checked, again; a hop
// to a scheme other than http(s) is refused; at most MaxRedirects; and a hop
// that leaves the first request's origin carries none of its headers but
// Accept, Content-Type and User-Agent.
func TestCallbackClientRedirects(t *testing.T) {
	t.Run("a hop to an unlisted loopback server", func(t *testing.T) {
		target := newHeaderRecorder(t)
		hop := redirector(t, target.srv.URL+"/next")
		_, err := postWithHeaders(mustNew(t, hostPort(t, hop)).CallbackClient(5*time.Second), hop.URL, nil)
		var refused *RefusedError
		if !errors.As(err, &refused) || refused.Class != netguard.Loopback {
			t.Fatalf("err = %v, want the second hop refused as loopback", err)
		}
		if n := target.hits.Load(); n != 0 {
			t.Fatalf("the refused hop was reached %d times", n)
		}
	})

	t.Run("a hop to the metadata service", func(t *testing.T) {
		meta := redirector(t, "http://169.254.169.254/latest/meta-data/iam/security-credentials/")
		_, err := postWithHeaders(mustNew(t, hostPort(t, meta)).CallbackClient(5*time.Second), meta.URL, nil)
		var refused *RefusedError
		if !errors.As(err, &refused) || refused.Class != netguard.Forbidden {
			t.Fatalf("err = %v, want the metadata hop refused", err)
		}
	})

	t.Run("a hop to a scheme that is not http(s)", func(t *testing.T) {
		file := redirector(t, "file:///etc/passwd")
		_, err := postWithHeaders(mustNew(t, hostPort(t, file)).CallbackClient(5*time.Second), file.URL, nil)
		if !errors.Is(err, ErrRedirectScheme) {
			t.Fatalf("err = %v, want ErrRedirectScheme", err)
		}
	})

	t.Run("redirects are bounded", func(t *testing.T) {
		var loop *httptest.Server
		loop = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			http.Redirect(w, r, loop.URL+r.URL.Path+"x", http.StatusFound)
		}))
		defer loop.Close()
		_, err := mustNew(t, hostPort(t, loop)).CallbackClient(5 * time.Second).Get(loop.URL + "/")
		if !errors.Is(err, ErrTooManyRedirects) {
			t.Fatalf("err = %v, want the redirect limit", err)
		}
	})

	t.Run("a listed hop to another origin keeps no header the org or an adapter set", func(t *testing.T) {
		target := newHeaderRecorder(t)
		hop := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			http.Redirect(w, r, target.srv.URL+"/next", http.StatusTemporaryRedirect)
		}))
		defer hop.Close()
		client := mustNew(t, hostPort(t, hop), hostPort(t, target.srv)).CallbackClient(5 * time.Second)
		resp, err := postWithHeaders(client, hop.URL+"/hook?token=planted-url-token", map[string]string{
			"Authorization":     "Bearer planted-bearer",
			"X-Api-Key":         "planted-api-key",
			"Lakera-Api-Key":    "planted-adapter-key",
			"X-Org-Custom":      "planted-org-header",
			"Accept":            "application/json",
			"X-Webhook-Purpose": "planted-org-purpose",
		})
		if err != nil {
			t.Fatalf("listed hops: %v", err)
		}
		resp.Body.Close()
		got := target.last()
		if got == nil {
			t.Fatal("the listed target was not reached")
		}
		for name, values := range got {
			for _, v := range values {
				if strings.Contains(v, "planted") {
					t.Errorf("%s: %q crossed to another origin", name, v)
				}
			}
		}
		if got.Get("Content-Type") != "application/json" || got.Get("Accept") != "application/json" {
			t.Errorf("Content-Type %q, Accept %q: the body headers should survive a 307", got.Get("Content-Type"), got.Get("Accept"))
		}
	})

	t.Run("a same-origin redirect keeps the headers", func(t *testing.T) {
		var srv *httptest.Server
		var seen atomic.Value
		srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Path == "/hook" {
				http.Redirect(w, r, "/hook/v2", http.StatusTemporaryRedirect)
				return
			}
			seen.Store(r.Header.Get("X-Api-Key"))
			io.WriteString(w, "reached")
		}))
		defer srv.Close()
		resp, err := postWithHeaders(mustNew(t, hostPort(t, srv)).CallbackClient(5*time.Second), srv.URL+"/hook",
			map[string]string{"X-Api-Key": "org-key-for-its-own-url"})
		if err != nil {
			t.Fatalf("same-origin redirect: %v", err)
		}
		resp.Body.Close()
		if got, _ := seen.Load().(string); got != "org-key-for-its-own-url" {
			t.Fatalf("the org's key did not follow a redirect within its own origin: %q", got)
		}
	})
}

// TestCallbackClientUsesNoProxy: the environment's proxy is never used, so
// the address checked is always the destination's.
func TestCallbackClientUsesNoProxy(t *testing.T) {
	proxy := newHeaderRecorder(t)
	client := mustNew(t).CallbackClient(5 * time.Second)
	if tr, ok := client.Transport.(*http.Transport); !ok || tr.Proxy != nil {
		t.Fatalf("transport %T has a proxy func", client.Transport)
	}
	t.Setenv("HTTP_PROXY", proxy.srv.URL)
	t.Setenv("HTTPS_PROXY", proxy.srv.URL)
	_, err := postWithHeaders(client, "http://10.0.0.5:8080/hook", nil)
	var refused *RefusedError
	if !errors.As(err, &refused) || refused.Class != netguard.Private {
		t.Fatalf("err = %v: want the private destination refused by the guard, not sent to a proxy", err)
	}
	if n := proxy.hits.Load(); n != 0 {
		t.Fatalf("the environment's proxy got %d requests", n)
	}
}

// TestCallbackClientsShareOneTransport: every check and channel built from
// one guard pools its connections in one transport; each gets its own
// timeout.
func TestCallbackClientsShareOneTransport(t *testing.T) {
	d := mustNew(t)
	a, b := d.CallbackClient(time.Second), d.CallbackClient(2*time.Second)
	if a.Transport != b.Transport {
		t.Fatal("two callback clients of one guard have two transports")
	}
	if a.Timeout != time.Second || b.Timeout != 2*time.Second {
		t.Fatalf("timeouts %v and %v", a.Timeout, b.Timeout)
	}
	if a == b {
		t.Fatal("the clients are one value: setting one's timeout would change the other's")
	}
}

// TestNilDialerHasNoExceptions: a caller given no guard (nil) gets the guard
// with no operator exceptions, never an unguarded client.
func TestNilDialerHasNoExceptions(t *testing.T) {
	rec := newHeaderRecorder(t)
	var d *Dialer
	_, err := postWithHeaders(d.CallbackClient(5*time.Second), rec.srv.URL+"/hook", nil)
	var refused *RefusedError
	if !errors.As(err, &refused) || refused.Class != netguard.Loopback {
		t.Fatalf("err = %v, want a nil guard to refuse loopback", err)
	}
	if rec.hits.Load() != 0 {
		t.Fatal("a nil guard reached a loopback server")
	}
}
