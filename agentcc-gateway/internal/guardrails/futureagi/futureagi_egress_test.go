package futureagi

import (
	"bytes"
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/mcp/egress"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/netguard"
)

// An org's FutureAGI
// eval check is built with the guarded egress dialer, like an org webhook or
// external check. A loopback or private base_url is refused after DNS, on
// every attempt and every redirect hop, unless the operator listed the address
// in mcp.egress.private_allow, and a link-local or cloud-metadata one is refused
// even when listed; the operator's own backend (FI_BASE_URL) is reachable
// unlisted, and a config.yaml rule uses a plain client. Every test here sets
// FI_BASE_URL (to a loopback listener, or empty), so no call can fall through
// to the public default.

const planted = "planted-org-key"

// evalRecorder is an evaluation API on loopback that counts its calls and keeps
// the last request's path and headers.
type evalRecorder struct {
	srv     *httptest.Server
	mu      sync.Mutex
	hits    int
	path    string
	headers http.Header
	body    []byte
}

func newEvalRecorder(t *testing.T) *evalRecorder {
	t.Helper()
	r := &evalRecorder{}
	r.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		buf := new(bytes.Buffer)
		_, _ = buf.ReadFrom(req.Body)
		r.mu.Lock()
		r.hits++
		r.path = req.URL.Path
		r.headers = req.Header.Clone()
		r.body = buf.Bytes()
		r.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"result":[{"evaluations":[{"name":"tox","output":"Failed","reason":"flagged by the listed API","evalId":"15"}]}]}`))
	}))
	t.Cleanup(r.srv.Close)
	return r
}

func (r *evalRecorder) count() int {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.hits
}

func (r *evalRecorder) last() (string, http.Header) {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.path, r.headers
}

func hostPort(srv *httptest.Server) string { return strings.TrimPrefix(srv.URL, "http://") }

func mustGuard(t *testing.T, allow ...string) *egress.Dialer {
	t.Helper()
	d, err := egress.New(egress.Options{PrivateAllow: allow})
	if err != nil {
		t.Fatalf("egress.New(%v): %v", allow, err)
	}
	return d
}

// orgRule is a complete org FutureAGI rule; an empty baseURL leaves base_url
// unset.
func orgRule(baseURL string) map[string]interface{} {
	cfg := map[string]interface{}{
		"provider": "futureagi", "eval_id": "15",
		"api_key": planted + "-api", "secret_key": planted + "-secret",
	}
	if baseURL != "" {
		cfg["base_url"] = baseURL
	}
	return cfg
}

func egressInput() *guardrails.CheckInput {
	return makeInput([]models.Message{makeMsg("user", "secret internal prompt from org A")})
}

// refusal posts to url with g's client and returns the guard's refusal, or
// fails the test: it proves the client is the guarded one (the check's own
// message is not used, so this holds however a call failure is worded).
func refusal(t *testing.T, g *FutureAGIGuardrail, url string) *egress.RefusedError {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, strings.NewReader("{}"))
	if err != nil {
		t.Fatalf("build request: %v", err)
	}
	resp, err := g.client.Do(req)
	if err == nil {
		resp.Body.Close()
		t.Fatalf("POST %s: reached (status %d), want the egress guard to refuse it", url, resp.StatusCode)
	}
	var refused *egress.RefusedError
	if !errors.As(err, &refused) {
		t.Fatalf("POST %s: err = %v, want an *egress.RefusedError", url, err)
	}
	return refused
}

// TestFutureAGI_OrgLoopbackRefused: with no operator exception, an org check
// whose base_url is a loopback address fails closed and the listener is never
// reached, on every attempt.
func TestFutureAGI_OrgLoopbackRefused(t *testing.T) {
	t.Setenv("FI_BASE_URL", "")
	rec := newEvalRecorder(t)
	cfg := orgRule(rec.srv.URL + "/internal")
	cfg["retry"] = 2

	g := New("fi-org", cfg, mustGuard(t))
	if res := g.Check(context.Background(), egressInput()); res.Pass {
		t.Fatal("an org futureagi check at a loopback base_url passed; the guard did not refuse it")
	}
	if n := rec.count(); n != 0 {
		t.Fatalf("the loopback evaluation API was reached %d times", n)
	}
	if r := refusal(t, g, rec.srv.URL+"/sdk/api/v1/eval/"); r.Class != netguard.Loopback {
		t.Fatalf("refused as %v, want loopback", r.Class)
	}
}

// TestFutureAGI_OrgNameResolvingToLoopbackRefused: the address is classified
// after DNS, in the dialer, on every connection — a host name (no IP literal)
// that resolves to loopback is refused on each of three attempts.
func TestFutureAGI_OrgNameResolvingToLoopbackRefused(t *testing.T) {
	t.Setenv("FI_BASE_URL", "")
	rec := newEvalRecorder(t)
	byName := strings.Replace(rec.srv.URL, "127.0.0.1", "localhost", 1)
	cfg := orgRule(byName)
	cfg["retry"] = 2

	g := New("fi-org", cfg, mustGuard(t))
	if res := g.Check(context.Background(), egressInput()); res.Pass {
		t.Fatal("an org futureagi check at a name resolving to loopback passed")
	}
	if n := rec.count(); n != 0 {
		t.Fatalf("the loopback evaluation API was reached by name %d times", n)
	}
	if r := refusal(t, g, byName+"/sdk/api/v1/eval/"); r.Class != netguard.Loopback {
		t.Fatalf("refused as %v, want loopback", r.Class)
	}
}

// TestFutureAGI_OrgPrivateAndMetadataRefused: a private (RFC 1918) base_url is
// refused before any connect, and a cloud-metadata one is refused even when
// the operator's list covers it.
func TestFutureAGI_OrgPrivateAndMetadataRefused(t *testing.T) {
	t.Setenv("FI_BASE_URL", "")
	private := New("fi-org", orgRule("http://10.255.255.1:8080"), mustGuard(t))
	if res := private.Check(context.Background(), egressInput()); res.Pass {
		t.Fatal("an org futureagi check at a private base_url passed")
	}
	if r := refusal(t, private, "http://10.255.255.1:8080/sdk/api/v1/eval/"); r.Class != netguard.Private {
		t.Fatalf("private base_url refused as %v, want private", r.Class)
	}

	metadata := New("fi-org", orgRule("http://169.254.169.254/latest/meta-data"), mustGuard(t, "169.254.0.0/16"))
	if res := metadata.Check(context.Background(), egressInput()); res.Pass {
		t.Fatal("an org futureagi check at the metadata address passed")
	}
	if r := refusal(t, metadata, "http://169.254.169.254/latest/meta-data/sdk/api/v1/eval/"); r.Class != netguard.Forbidden {
		t.Fatalf("metadata base_url refused as %v, want forbidden", r.Class)
	}
}

// TestFutureAGI_OrgRedirects: a redirect hop is re-checked — a listed
// evaluation API that redirects to an unlisted loopback server or to the
// metadata address does not reach it — and a redirect to another listed origin
// does not carry the org's X-Api-Key or X-Secret-Key there.
func TestFutureAGI_OrgRedirects(t *testing.T) {
	t.Setenv("FI_BASE_URL", "")

	t.Run("to an unlisted loopback server", func(t *testing.T) {
		target := newEvalRecorder(t)
		hop := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			http.Redirect(w, r, target.srv.URL+r.URL.Path, http.StatusTemporaryRedirect)
		}))
		defer hop.Close()
		g := New("fi-org", orgRule(hop.URL), mustGuard(t, hostPort(hop)))
		if res := g.Check(context.Background(), egressInput()); res.Pass {
			t.Fatal("the check passed although its redirect target is refused")
		}
		if n := target.count(); n != 0 {
			t.Fatalf("the unlisted redirect target was reached %d times", n)
		}
		if r := refusal(t, g, hop.URL+"/sdk/api/v1/eval/"); r.Class != netguard.Loopback {
			t.Fatalf("redirect hop refused as %v, want loopback", r.Class)
		}
	})

	t.Run("to the metadata address", func(t *testing.T) {
		hop := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			http.Redirect(w, r, "http://169.254.169.254/latest/meta-data/", http.StatusFound)
		}))
		defer hop.Close()
		g := New("fi-org", orgRule(hop.URL), mustGuard(t, hostPort(hop)))
		if res := g.Check(context.Background(), egressInput()); res.Pass {
			t.Fatal("the check passed although its redirect target is the metadata address")
		}
		if r := refusal(t, g, hop.URL+"/sdk/api/v1/eval/"); r.Class != netguard.Forbidden {
			t.Fatalf("metadata redirect refused as %v, want forbidden", r.Class)
		}
	})

	t.Run("the keys do not cross to another origin", func(t *testing.T) {
		target := newEvalRecorder(t)
		hop := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			http.Redirect(w, r, target.srv.URL+r.URL.Path, http.StatusTemporaryRedirect)
		}))
		defer hop.Close()
		g := New("fi-org", orgRule(hop.URL), mustGuard(t, hostPort(hop), hostPort(target.srv)))
		_ = g.Check(context.Background(), egressInput())
		if n := target.count(); n != 1 {
			t.Fatalf("the listed redirect target was reached %d times, want 1", n)
		}
		_, headers := target.last()
		for name, values := range headers {
			for _, v := range values {
				if strings.Contains(v, planted) {
					t.Errorf("%s: %q crossed to another origin on a redirect", name, v)
				}
			}
		}
	})
}

// TestFutureAGI_OrgListedEvalAPIWorks: an evaluation API the operator listed
// (a self-hosted backend on a private address) is reached with the org's keys
// and its verdict is read.
func TestFutureAGI_OrgListedEvalAPIWorks(t *testing.T) {
	t.Setenv("FI_BASE_URL", "")
	rec := newEvalRecorder(t)
	g := New("fi-org", orgRule(rec.srv.URL), mustGuard(t, hostPort(rec.srv)))
	res := g.Check(context.Background(), egressInput())
	if res.Pass || res.Message != "flagged by the listed API" {
		t.Fatalf("listed eval API: pass=%v msg=%q, want its verdict", res.Pass, res.Message)
	}
	path, headers := rec.last()
	if rec.count() != 1 || path != "/sdk/api/v1/eval/" || headers.Get("X-Api-Key") != planted+"-api" {
		t.Fatalf("listed eval API: hits=%d path=%q X-Api-Key=%q", rec.count(), path, headers.Get("X-Api-Key"))
	}
}

// TestFutureAGI_NilDialerRefusesLoopback: a nil dialer is the guard with no
// exceptions.
func TestFutureAGI_NilDialerRefusesLoopback(t *testing.T) {
	t.Setenv("FI_BASE_URL", "")
	rec := newEvalRecorder(t)
	g := New("fi-org", orgRule(rec.srv.URL), nil)
	if res := g.Check(context.Background(), egressInput()); res.Pass {
		t.Fatal("a nil dialer reached a loopback evaluation API")
	}
	if n := rec.count(); n != 0 {
		t.Fatalf("reached %d times", n)
	}
}

// TestFutureAGI_OperatorBackendIsReachable: the operator's own backend,
// FI_BASE_URL, is operator configuration, so the org check reaches it on a
// private or loopback address without the operator listing it — when the
// rule names no base_url, when it names the built-in default (what the
// platform fills in),
// and when it names exactly FI_BASE_URL (a trailing slash aside). In each
// case the eval URL is FI_BASE_URL's own.
func TestFutureAGI_OperatorBackendIsReachable(t *testing.T) {
	for name, baseURL := range map[string]func(fi string) string{
		"no base_url":                       func(string) string { return "" },
		"the built-in default":              func(string) string { return defaultBaseURL },
		"exactly FI_BASE_URL":               func(fi string) string { return fi },
		"FI_BASE_URL with a trailing slash": func(fi string) string { return fi + "/" },
	} {
		t.Run(name, func(t *testing.T) {
			rec := newEvalRecorder(t)
			t.Setenv("FI_BASE_URL", rec.srv.URL)
			g := New("fi-org", orgRule(baseURL(rec.srv.URL)), mustGuard(t))
			res := g.Check(context.Background(), egressInput())
			if res.Pass || res.Message != "flagged by the listed API" {
				t.Fatalf("operator backend: pass=%v msg=%q, want its verdict", res.Pass, res.Message)
			}
			if path, _ := rec.last(); rec.count() != 1 || path != "/sdk/api/v1/eval/" {
				t.Fatalf("operator backend: hits=%d path=%q", rec.count(), path)
			}
		})
	}
}

// TestFutureAGI_OrgURLOnTheOperatorHostIsGuarded: the operator backend the
// guard trusts is exactly FI_BASE_URL. Every other base_url on that host,
// with a different path, a query or userinfo, goes through the egress guard.
func TestFutureAGI_OrgURLOnTheOperatorHostIsGuarded(t *testing.T) {
	rec := newEvalRecorder(t)
	t.Setenv("FI_BASE_URL", rec.srv.URL)
	for _, base := range []string{
		rec.srv.URL + "/admin",
		rec.srv.URL + "/x?",
		rec.srv.URL + "?next=",
		strings.Replace(rec.srv.URL, "http://", "http://user:pw@", 1),
		strings.Replace(rec.srv.URL, "127.0.0.1", "localhost", 1),
	} {
		g := New("fi-org", orgRule(base), mustGuard(t))
		if res := g.Check(context.Background(), egressInput()); res.Pass {
			t.Errorf("base_url %q: the check passed", base)
		}
	}
	if n := rec.count(); n != 0 {
		t.Fatalf("an org base_url other than FI_BASE_URL reached the operator backend %d times", n)
	}
}

// TestFutureAGI_ConfigFileRuleUsesAPlainClient: a config.yaml rule is
// operator-trusted — it may name a loopback or private evaluation API without
// listing it.
func TestFutureAGI_ConfigFileRuleUsesAPlainClient(t *testing.T) {
	t.Setenv("FI_BASE_URL", "")
	rec := newEvalRecorder(t)
	g := NewConfigFileRule("fi-op", orgRule(rec.srv.URL))
	res := g.Check(context.Background(), egressInput())
	if res.Pass || res.Message != "flagged by the listed API" {
		t.Fatalf("operator config-file rule: pass=%v msg=%q, want its eval API's verdict", res.Pass, res.Message)
	}
	if n := rec.count(); n != 1 {
		t.Fatalf("the operator's eval API was reached %d times, want 1", n)
	}
}
