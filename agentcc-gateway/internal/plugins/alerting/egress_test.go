package alerting

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"

	alertpkg "github.com/futureagi/agentcc-gateway/internal/alerting"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp/egress"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// config_AlertingWebhook is an operator alerting config with one webhook
// channel at url and a rule that fires on any request.
func config_AlertingWebhook(url string) config.AlertingConfig {
	return config.AlertingConfig{
		Enabled: true,
		Rules: []config.AlertRuleConfig{{
			Name: "any-request", Metric: "request_count", Condition: ">=", Threshold: 1,
			Channels: []string{"ops"},
		}},
		Channels: []config.AlertChannelConfig{{Name: "ops", Type: "webhook", URL: url}},
	}
}

// An organization's own alert
// channel is dialled through the guarded egress dialer, so an org-set webhook
// or slack URL at a loopback, private or metadata address is refused unless
// the operator listed it, and a listed one still delivers.

func egressGuard(t *testing.T, allow ...string) *egress.Dialer {
	t.Helper()
	d, err := egress.New(egress.Options{PrivateAllow: allow})
	if err != nil {
		t.Fatalf("egress.New(%v): %v", allow, err)
	}
	return d
}

func orgWithChannel(chType, url string) *tenant.OrgConfig {
	return &tenant.OrgConfig{Alerting: &tenant.AlertingConfig{
		Enabled: true,
		Rules: []*tenant.AlertRuleConfig{{
			Name: "any-request", Metric: "request_count", Condition: ">=", Threshold: 1,
			Channels: []string{"ch"},
		}},
		Channels: []*tenant.AlertChannelConfig{{
			Name: "ch", Type: chType, URL: url,
			Headers: map[string]string{"X-Org-Chosen": "org-a"},
		}},
	}}
}

func runOneRequest(store *tenant.Store, dialer *egress.Dialer) {
	p := New(nil, false, store, dialer)
	rc := newTestRC()
	rc.Metadata[tenant.MetadataKeyOrgID] = "org-a"
	p.ProcessResponse(context.Background(), rc)
}

type reqRecorder struct {
	srv  *httptest.Server
	mu   sync.Mutex
	hits atomic.Int32
	last string
}

func newReqRecorder(t *testing.T) *reqRecorder {
	t.Helper()
	r := &reqRecorder{}
	r.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		r.hits.Add(1)
		r.mu.Lock()
		r.last = req.Header.Get("X-Org-Chosen")
		r.mu.Unlock()
		w.WriteHeader(http.StatusOK)
	}))
	t.Cleanup(r.srv.Close)
	return r
}

// TestOrgAlertWebhookLoopbackRefused: with no operator exception, an org
// webhook channel at a loopback URL is never reached.
func TestOrgAlertWebhookLoopbackRefused(t *testing.T) {
	rec := newReqRecorder(t)
	store := tenant.NewStore()
	store.Set("org-a", orgWithChannel("webhook", rec.srv.URL+"/internal-only/admin"))
	runOneRequest(store, egressGuard(t))
	if n := rec.hits.Load(); n != 0 {
		t.Fatalf("the org webhook reached the loopback listener %d times; the guard did not refuse it", n)
	}
}

// TestOrgAlertSlackLoopbackRefused: the slack channel type is guarded too.
func TestOrgAlertSlackLoopbackRefused(t *testing.T) {
	rec := newReqRecorder(t)
	store := tenant.NewStore()
	store.Set("org-a", orgWithChannel("slack", rec.srv.URL+"/internal-only/slack"))
	runOneRequest(store, egressGuard(t))
	if n := rec.hits.Load(); n != 0 {
		t.Fatalf("the org slack channel reached the loopback listener %d times", n)
	}
}

// TestOrgAlertWebhookRedirectToLoopbackRefused: a public-looking first hop
// that redirects to loopback is not followed there.
func TestOrgAlertWebhookRedirectToLoopbackRefused(t *testing.T) {
	second := newReqRecorder(t)
	first := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, second.srv.URL+"/after-redirect", http.StatusFound)
	}))
	defer first.Close()
	store := tenant.NewStore()
	// The first hop is loopback too (httptest), so list it; the redirect
	// target is a different loopback port, which is not listed.
	store.Set("org-a", orgWithChannel("webhook", first.URL+"/hook"))
	runOneRequest(store, egressGuard(t, strings.TrimPrefix(first.URL, "http://")))
	if n := second.hits.Load(); n != 0 {
		t.Fatalf("the redirect to an unlisted loopback target was followed %d times", n)
	}
}

// TestOrgAlertWebhookListedTargetDelivers: the operator lists the server and
// the alert is delivered, with the org's own header.
func TestOrgAlertWebhookListedTargetDelivers(t *testing.T) {
	rec := newReqRecorder(t)
	store := tenant.NewStore()
	store.Set("org-a", orgWithChannel("webhook", rec.srv.URL+"/hook"))
	runOneRequest(store, egressGuard(t, strings.TrimPrefix(rec.srv.URL, "http://")))
	if n := rec.hits.Load(); n != 1 {
		t.Fatalf("the listed org webhook was reached %d times, want 1", n)
	}
	rec.mu.Lock()
	defer rec.mu.Unlock()
	if rec.last != "org-a" {
		t.Fatalf("the org's own header did not reach its own URL: %q", rec.last)
	}
}

// TestOrgAlertNilDialerRefusesLoopback: a nil dialer is the guard with no
// exceptions.
func TestOrgAlertNilDialerRefusesLoopback(t *testing.T) {
	rec := newReqRecorder(t)
	store := tenant.NewStore()
	store.Set("org-a", orgWithChannel("webhook", rec.srv.URL+"/hook"))
	runOneRequest(store, nil)
	if n := rec.hits.Load(); n != 0 {
		t.Fatalf("a nil dialer reached a loopback org webhook %d times", n)
	}
}

// TestOperatorGlobalChannelsStayPlain: the operator's config.yaml channels
// (NewManager) are not guarded — a global webhook at the operator's own host
// still delivers.
func TestOperatorGlobalChannelsStayPlain(t *testing.T) {
	rec := newReqRecorder(t)
	mgr := alertpkg.NewManager(config_AlertingWebhook(rec.srv.URL + "/ops"))
	p := New(mgr, true, nil, egressGuard(t)) // guard set, but global channels bypass it
	rc := newTestRC()
	p.ProcessResponse(context.Background(), rc)
	if n := rec.hits.Load(); n != 1 {
		t.Fatalf("the operator's global webhook was reached %d times, want 1", n)
	}
}
