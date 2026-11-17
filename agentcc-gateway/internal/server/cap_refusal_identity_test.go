package server

import (
	"context"
	"encoding/json"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
	"time"

	contract "github.com/futureagi/agentcc-gateway/internal/contracts/generated"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func assertCapIdentity(t *testing.T, w *httptest.ResponseRecorder, scope, ref, kind string, revision int) {
	t.Helper()
	var body struct {
		Type  string         `json:"type"`
		Error map[string]any `json:"error"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	want := map[string]any{"cap_scope": scope, "cap_ref": ref, "cap_kind": kind, "policy_revision": float64(revision)}
	if !reflect.DeepEqual(body.Error["metadata"], want) {
		t.Fatalf("identity=%#v want=%#v; body=%s", body.Error["metadata"], want, w.Body)
	}
	for name := range w.Header() {
		lower := strings.ToLower(name)
		for _, sensitive := range []string{"cost", "spend", "balance", "remaining", "budget", "limit"} {
			if strings.Contains(lower, sensitive) {
				t.Fatalf("monetary header %s=%v", name, w.Header()[name])
			}
		}
	}
	for _, r := range body.Error["message"].(string) {
		if r >= '0' && r <= '9' {
			t.Fatalf("numeric message: %s", w.Body)
		}
	}
}

func TestCapRefusalWireDialects(t *testing.T) {
	for _, status := range []int{429, 503} {
		for _, messages := range []bool{false, true} {
			err := &models.APIError{Status: status, Type: models.ErrTypeRateLimit, Code: "org_budget_exceeded", Message: "daily model-spend cap cannot admit this request", Metadata: contract.AdmissionCapRefusalMetadata{CapScope: "agent", CapRef: "worker", CapKind: "daily", PolicyRevision: 7}}
			if status == 503 {
				err.Type, err.Code = models.ErrTypeInvalidRequest, "budget_state_unavailable"
			}
			w := httptest.NewRecorder()
			if messages {
				writeAnthropicErrorFromError(w, err)
			} else {
				models.WriteError(w, err)
			}
			assertCapIdentity(t, w, "agent", "worker", "daily", 7)
			if errorCode(t, w) != err.Code || w.Code != status {
				t.Fatalf("code/status lost: %d %s", w.Code, w.Body)
			}
			if messages && !strings.Contains(w.Body.String(), `"type":"error"`) {
				t.Fatalf("not Messages-shaped: %s", w.Body)
			}
		}
	}
}

func TestCapRefusalGuardedRoutes(t *testing.T) {
	srv := capInventoryServer(t)
	key := srv.keyStore.Authenticate(videoKeyA)
	classes := capRouteClasses(t)
	for _, rt := range srv.router.Routes() {
		if classes[rt.Method+" "+rt.Pattern] != "unsupported" {
			continue
		}
		t.Run(rt.Method+" "+rt.Pattern, func(t *testing.T) {
			target := concretePath(rt.Pattern)
			headers := map[string]string{"Authorization": "Bearer " + videoKeyA}
			if strings.HasPrefix(target, "/v1beta/") {
				headers = map[string]string{"x-goog-api-key": videoKeyA}
			}
			if strings.HasPrefix(target, "/cartesia/") {
				headers = map[string]string{"X-API-Key": videoKeyA}
			}
			if strings.HasPrefix(target, "/deepgram/") {
				headers = map[string]string{"Authorization": "Token " + videoKeyA}
			}
			w := serveWithHeaders(t, srv, rt.Method, target, `{}`, headers, 5*time.Second)
			if w.Code != 402 {
				t.Fatalf("status=%d %s", w.Code, w.Body)
			}
			assertCapIdentity(t, w, "key", key.ID, "daily", 1)
			if strings.HasPrefix(target, "/v1/messages") && !strings.Contains(w.Body.String(), `"type":"error"`) {
				t.Fatalf("not Messages-shaped: %s", w.Body)
			}
		})
	}
	w := serveWithHeaders(t, srv, "POST", "/v1/chat/completions", `{"model":"synthetic","messages":[],"stream":true}`, map[string]string{"Authorization": "Bearer " + videoKeyA}, 5*time.Second)
	if w.Code != 402 {
		t.Fatalf("stream start=%d %s", w.Code, w.Body)
	}
	assertCapIdentity(t, w, "key", key.ID, "daily", 1)
}

func TestCapRefusalRunIdentity(t *testing.T) {
	for _, bound := range []bool{false, true} {
		for _, uncertain := range []bool{false, true} {
			g, client, prefix := capComposedGateway(t)
			cfg := g.tenants.Get("org-m")
			cfg.Budgets.Agents, cfg.Budgets.KeyIDs = nil, nil
			limit := 0.000001
			cfg.Routing.Agents.MaxRunUSD = &limit
			scope, ref := "agent", "worker"
			if !bound {
				cfg.Routing.Agents.KeyBindings = nil
				scope, ref = "key", g.srv.keyStore.Authenticate(managedOrgKey).ID
			}
			g.tenants.Set("org-m", cfg)
			if uncertain {
				// A malformed authoritative counter is unavailable, never zero.
				client.Redis().Set(context.Background(), prefix+"abud:{org-m}:synthetic-run:spent", "unknown", 0)
			}
			w := g.chat(t, chatBody("openai/gpt-6-sol"), map[string]string{"x-agentcc-run-id": "synthetic-run"})
			want := 402
			if uncertain {
				want = 503
			}
			if w.Code != want || g.managed.calls() != 0 {
				t.Fatalf("run status=%d calls=%d %s", w.Code, g.managed.calls(), w.Body)
			}
			assertCapIdentity(t, w, scope, ref, "run", 1)
		}
	}
}

func TestCapRefusalMissingRunPlugin(t *testing.T) {
	g, _, _ := capComposedGateway(t)
	cfg := g.tenants.Get("org-m")
	limit := 0.05
	cfg.Routing.Agents.MaxRunUSD = &limit
	g.tenants.Set("org-m", cfg)
	g.srv.handlers.SetRunBudget(nil)
	w := g.chat(t, chatBody("openai/gpt-6-sol"), map[string]string{"x-agentcc-run-id": "synthetic-run"})
	if w.Code != 402 || errorCode(t, w) != "call_cap_unenforceable" || g.managed.calls() != 0 {
		t.Fatalf("missing run plugin: %d %s", w.Code, w.Body)
	}
	assertCapIdentity(t, w, "agent", "worker", "run", 1)
}

func TestCapRefusalMissingRevisionDialects(t *testing.T) {
	srv := capInventoryServer(t)
	cfg := capRouteConfig(t, srv, true)
	cfg.Budgets.PolicyRevision = 0
	srv.TenantStore.Set(videoOrgA, cfg)
	for _, path := range []string{"/v1/chat/completions", "/v1/messages", "/v1/responses"} {
		w := serveWithHeaders(t, srv, "POST", path, `{ "stream": true }`, map[string]string{"Authorization": "Bearer " + videoKeyA}, 5*time.Second)
		if w.Code != 503 || errorCode(t, w) != "budget_state_unavailable" {
			t.Fatalf("%s: %d %s", path, w.Code, w.Body)
		}
		assertCapIdentity(t, w, "key", srv.keyStore.Authenticate(videoKeyA).ID, "daily", 0)
		if path == "/v1/messages" && !strings.Contains(w.Body.String(), `"type":"error"`) {
			t.Fatalf("not Messages-shaped: %s", w.Body)
		}
	}
}

func TestCapRefusalComposedChat(t *testing.T) {
	for _, scope := range []string{"agent", "key"} {
		t.Run(scope, func(t *testing.T) {
			g, _, _ := capComposedGateway(t)
			cfg := g.tenants.Get("org-m")
			ref := "worker"
			limit := 0.000001
			if scope == "agent" {
				cfg.Budgets.Agents[ref].Limit = &limit
			} else {
				ref = g.srv.keyStore.Authenticate(managedOrgKey).ID
				cfg.Budgets.KeyIDs[ref].Limit = &limit
			}
			g.tenants.Set("org-m", cfg)
			w := g.chat(t, chatBody("openai/gpt-6-sol"), nil)
			if w.Code != 429 || g.managed.calls() != 0 {
				t.Fatalf("status=%d calls=%d %s", w.Code, g.managed.calls(), w.Body)
			}
			assertCapIdentity(t, w, scope, ref, "daily", 1)
		})
	}
}
