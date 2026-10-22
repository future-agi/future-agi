package alerting

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// An organization's alert rule carries its severity to every channel, and a
// rule saved without a cooldown waits a stated default (15 minutes) before it
// fires again. The org config is built from the JSON the platform pushes,
// so the test reads the rule exactly as the gateway receives it.

// bodyRecorder is a loopback HTTP server that keeps every request body.
type bodyRecorder struct {
	srv    *httptest.Server
	mu     sync.Mutex
	bodies []string
}

func newBodyRecorder(t *testing.T) *bodyRecorder {
	t.Helper()
	r := &bodyRecorder{}
	r.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		b, _ := io.ReadAll(req.Body)
		r.mu.Lock()
		r.bodies = append(r.bodies, string(b))
		r.mu.Unlock()
		w.WriteHeader(http.StatusOK)
	}))
	t.Cleanup(r.srv.Close)
	return r
}

func (r *bodyRecorder) Bodies() []string {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]string(nil), r.bodies...)
}

func (r *bodyRecorder) hostPort() string { return strings.TrimPrefix(r.srv.URL, "http://") }

// orgAlerting parses an org config's alerting section from the JSON the
// platform pushes. rule is one alert rule object without its channels.
func orgAlerting(t *testing.T, rule string, webhookURL, slackURL string) *tenant.OrgConfig {
	t.Helper()
	var rm map[string]any
	if err := json.Unmarshal([]byte(rule), &rm); err != nil {
		t.Fatalf("rule JSON: %v", err)
	}
	rm["channels"] = []string{"hook", "chat"}
	doc := map[string]any{"alerting": map[string]any{
		"enabled": true,
		"rules":   []any{rm},
		"channels": []any{
			map[string]any{"name": "hook", "type": "webhook", "url": webhookURL},
			map[string]any{"name": "chat", "type": "slack", "url": slackURL},
		},
	}}
	body, _ := json.Marshal(doc)
	var cfg tenant.OrgConfig
	if err := json.Unmarshal(body, &cfg); err != nil {
		t.Fatalf("org config JSON: %v", err)
	}
	return &cfg
}

func sendRequests(p *Plugin, orgID string, n int) {
	for i := 0; i < n; i++ {
		rc := newTestRC()
		rc.Metadata[tenant.MetadataKeyOrgID] = orgID
		p.ProcessResponse(context.Background(), rc)
	}
}

func TestAlertRuleCooldownAndSeverity(t *testing.T) {
	t.Run("severity reaches the webhook payload and the Slack text; no cooldown set fires once", func(t *testing.T) {
		hook, chat := newBodyRecorder(t), newBodyRecorder(t)
		store := tenant.NewStore()
		store.Set("org-a", orgAlerting(t,
			`{"name":"any-request","metric":"request_count","condition":">=","threshold":1,"window":"5m","severity":"critical"}`,
			hook.srv.URL+"/hook", chat.srv.URL+"/slack"))
		p := New(nil, false, store, egressGuard(t, hook.hostPort(), chat.hostPort()))

		sendRequests(p, "org-a", 5)

		hooks, chats := hook.Bodies(), chat.Bodies()
		if len(hooks) != 1 || len(chats) != 1 {
			t.Fatalf("5 requests past the threshold sent %d webhook and %d Slack alerts; want 1 each (the default cooldown holds)", len(hooks), len(chats))
		}
		var payload map[string]any
		if err := json.Unmarshal([]byte(hooks[0]), &payload); err != nil {
			t.Fatalf("webhook payload is not JSON: %v (%s)", err, hooks[0])
		}
		if payload["severity"] != "critical" {
			t.Fatalf("webhook payload severity = %v, want critical; payload %s", payload["severity"], hooks[0])
		}
		var slack map[string]string
		if err := json.Unmarshal([]byte(chats[0]), &slack); err != nil {
			t.Fatalf("slack payload is not JSON: %v", err)
		}
		if !strings.Contains(slack["text"], "Severity: critical") {
			t.Fatalf("slack text does not name the severity: %q", slack["text"])
		}
	})

	t.Run("a config push rebuilds the rule but keeps its cooldown", func(t *testing.T) {
		hook, chat := newBodyRecorder(t), newBodyRecorder(t)
		store := tenant.NewStore()
		cfg := orgAlerting(t,
			`{"name":"any-request","metric":"request_count","condition":">=","threshold":1,"cooldown":"1h","severity":"warning"}`,
			hook.srv.URL+"/hook", chat.srv.URL+"/slack")
		store.Set("org-a", cfg)
		p := New(nil, false, store, egressGuard(t, hook.hostPort(), chat.hostPort()))

		sendRequests(p, "org-a", 2)
		// What main.go's onOrgConfigChange does on every org config push,
		// whichever section changed.
		store.Set("org-a", orgAlerting(t,
			`{"name":"any-request","metric":"request_count","condition":">=","threshold":1,"cooldown":"1h","severity":"warning"}`,
			hook.srv.URL+"/hook", chat.srv.URL+"/slack"))
		p.InvalidateOrg("org-a")
		sendRequests(p, "org-a", 3)

		if n := len(hook.Bodies()); n != 1 {
			t.Fatalf("the rule fired %d times across a config push inside its 1h cooldown; want 1", n)
		}
	})

	t.Run("an explicit 0s cooldown is honoured: fires on every request past the threshold", func(t *testing.T) {
		hook, chat := newBodyRecorder(t), newBodyRecorder(t)
		store := tenant.NewStore()
		store.Set("org-a", orgAlerting(t,
			`{"name":"every","metric":"request_count","condition":">=","threshold":1,"cooldown":"0s","severity":"info"}`,
			hook.srv.URL+"/hook", chat.srv.URL+"/slack"))
		p := New(nil, false, store, egressGuard(t, hook.hostPort(), chat.hostPort()))

		sendRequests(p, "org-a", 3)

		if n := len(hook.Bodies()); n != 3 {
			t.Fatalf("an explicit 0s cooldown fired %d times for 3 requests; want 3", n)
		}
	})

	t.Run("a rule without a severity, or with an unknown one, is sent as warning", func(t *testing.T) {
		for _, rule := range []string{
			`{"name":"r","metric":"request_count","condition":">=","threshold":1}`,
			`{"name":"r","metric":"request_count","condition":">=","threshold":1,"severity":"sev1"}`,
		} {
			hook, chat := newBodyRecorder(t), newBodyRecorder(t)
			store := tenant.NewStore()
			store.Set("org-a", orgAlerting(t, rule, hook.srv.URL+"/hook", chat.srv.URL+"/slack"))
			p := New(nil, false, store, egressGuard(t, hook.hostPort(), chat.hostPort()))

			sendRequests(p, "org-a", 1)

			hooks := hook.Bodies()
			if len(hooks) != 1 {
				t.Fatalf("rule %s: %d webhook alerts, want 1", rule, len(hooks))
			}
			var payload map[string]any
			_ = json.Unmarshal([]byte(hooks[0]), &payload)
			if payload["severity"] != "warning" {
				t.Fatalf("rule %s: severity = %v, want warning", rule, payload["severity"])
			}
		}
	})
}
