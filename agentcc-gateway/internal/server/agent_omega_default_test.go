package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// omegaGateway is the managed test gateway with the generation recorder on,
// so a test can read what the request logged.
func omegaGateway(t *testing.T, cfg *tenant.OrgConfig) (*managedTestGateway, chan *models.TraceSnapshot) {
	t.Helper()
	g := newManagedTestGateway(t, cfg)
	g.ledger.SetMicros("org-m", auth.USDToMicros(1))
	snaps := make(chan *models.TraceSnapshot, 4)
	g.srv.handlers.SetGenerationRecorder(func(s *models.TraceSnapshot) { snaps <- s })
	return g, snaps
}

func waitSnapshot(t *testing.T, snaps chan *models.TraceSnapshot) *models.TraceSnapshot {
	t.Helper()
	select {
	case s := <-snaps:
		return s
	case <-time.After(5 * time.Second):
		t.Fatal("no generation record")
		return nil
	}
}

func responseModel(t *testing.T, body []byte) string {
	t.Helper()
	var resp struct {
		Model string `json:"model"`
	}
	if err := json.Unmarshal(body, &resp); err != nil {
		t.Fatalf("response body: %v (%s)", err, body)
	}
	return resp.Model
}

func TestOmegaDefaultAlias(t *testing.T) {
	for _, alias := range []string{"omega-default", "omega/auto"} {
		if !routing.IsAutoModel(alias) {
			t.Errorf("%s is not an auto model name", alias)
		}
		t.Run(alias, func(t *testing.T) {
			g, snaps := omegaGateway(t, managedOptIn())
			w := g.chat(t, chatBody(alias), nil)
			if w.Code != http.StatusOK {
				t.Fatalf("status = %d; body %s", w.Code, w.Body)
			}
			if _, ok := testCatalogModels[g.managed.lastModel()]; !ok {
				t.Errorf("upstream model = %q, want a managed catalog model", g.managed.lastModel())
			}
			if got := responseModel(t, w.Body.Bytes()); got != g.managed.lastModel() {
				t.Errorf("response model = %q, want the real model %q", got, g.managed.lastModel())
			}
			snap := waitSnapshot(t, snaps)
			if snap.Metadata["model_alias_requested"] != alias || snap.Metadata["auto_requested"] != alias {
				t.Errorf("metadata model_alias_requested %q auto_requested %q", snap.Metadata["model_alias_requested"], snap.Metadata["auto_requested"])
			}
		})
	}
	if routing.IsAutoModel("omega-default-2") || routing.IsAutoModel("omega") {
		t.Error("only the exact alias names are auto")
	}
}

func TestOmegaDefaultPinned(t *testing.T) {
	pinned := func(cfg *tenant.OrgConfig, agents *tenant.AgentsConfig, modelMap map[string]string) *tenant.OrgConfig {
		cfg.Routing = &tenant.RoutingConfig{Agents: agents}
		cfg.ModelMap = modelMap
		return cfg
	}
	cases := []struct {
		name string
		cfg  *tenant.OrgConfig
		want string // "" means auto chose
	}{
		{"routing.agents.default_model", pinned(managedOptIn(), &tenant.AgentsConfig{DefaultModel: "anthropic/claude-sonnet-5.5"}, nil), "anthropic/claude-sonnet-5.5"},
		{"default_model wins over model_map", pinned(managedOptIn(), &tenant.AgentsConfig{DefaultModel: "openai/gpt-6-sol"},
			map[string]string{"omega-default": "anthropic/claude-sonnet-5.5"}), "openai/gpt-6-sol"},
		{"model_map names the model", pinned(managedOptIn(), nil, map[string]string{"omega-default": "openai/gpt-6-sol"}), "openai/gpt-6-sol"},
		{"default_model auto means auto", pinned(managedOptIn(), &tenant.AgentsConfig{DefaultModel: "auto"}, nil), ""},
		// The org-config golden's default_model is the alias itself: no loop.
		{"default_model naming the alias is auto", pinned(managedOptIn(), &tenant.AgentsConfig{DefaultModel: "omega-default"}, nil), ""},
		// model_map's values are provider ids; one can't make an auto alias an
		// explicit route, so the alias stays auto.
		{"model_map naming a provider is auto", pinned(managedOptIn(), nil, map[string]string{"omega-default": "openrouter"}), ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			g, snaps := omegaGateway(t, tc.cfg)
			w := g.chat(t, chatBody("omega-default"), nil)
			if w.Code != http.StatusOK {
				t.Fatalf("status = %d; body %s", w.Code, w.Body)
			}
			snap := waitSnapshot(t, snaps)
			if snap.Metadata["model_alias_requested"] != "omega-default" {
				t.Errorf("model_alias_requested = %q", snap.Metadata["model_alias_requested"])
			}
			if tc.want == "" {
				if snap.Metadata["auto_requested"] != "omega-default" {
					t.Errorf("auto did not run: %v", snap.Metadata)
				}
				return
			}
			if g.managed.lastModel() != tc.want || responseModel(t, w.Body.Bytes()) != tc.want {
				t.Errorf("upstream %q response %q, want %q", g.managed.lastModel(), responseModel(t, w.Body.Bytes()), tc.want)
			}
			if _, ok := snap.Metadata["auto_requested"]; ok {
				t.Errorf("a pinned alias must not run auto: %v", snap.Metadata)
			}
		})
	}
}

func TestOmegaDefaultUnroutable(t *testing.T) {
	// No managed opt-in and no providers: nothing to route to.
	for _, alias := range []string{"omega-default", "omega/auto"} {
		g, _ := omegaGateway(t, &tenant.OrgConfig{})
		w := g.chat(t, chatBody(alias), nil)
		var resp struct {
			Error struct {
				Code     string            `json:"code"`
				Metadata map[string]string `json:"metadata"`
			} `json:"error"`
		}
		_ = json.Unmarshal(w.Body.Bytes(), &resp)
		if w.Code != http.StatusBadRequest || resp.Error.Code != "omega_default_unroutable" {
			t.Fatalf("%s: status %d body %s", alias, w.Code, w.Body)
		}
		if resp.Error.Metadata["remedy_hint"] != "enable managed models or add a provider" {
			t.Errorf("remedy_hint = %q", resp.Error.Metadata["remedy_hint"])
		}
		if g.managed.calls() != 0 || g.operator.calls() != 0 {
			t.Error("an unroutable alias reached an upstream")
		}
	}
	// A pinned default_model is not judged by the auto pool.
	off := &tenant.OrgConfig{Routing: &tenant.RoutingConfig{Agents: &tenant.AgentsConfig{DefaultModel: "anthropic/claude-sonnet-5.5"}}}
	g, _ := omegaGateway(t, off)
	if w := g.chat(t, chatBody("omega-default"), nil); w.Code == http.StatusBadRequest {
		t.Errorf("a pinned alias got %d %s; its own route decides", w.Code, w.Body)
	}
}

// The unroutable check at step 7 builds the auto candidates without running
// checkEligibility, which belongs to step 11 alone: an omega alias must not
// pay for (or record) eligibility twice.
func TestOmegaDefaultUnroutableJudgedWithoutEligibility(t *testing.T) {
	checks := map[string]int{}
	for _, model := range []string{"auto", "omega-default"} {
		g, snaps := omegaGateway(t, managedOptIn())
		var mu sync.Mutex
		var events []string
		g.srv.handlers.hookObserver = func(hook, _ string, _ *models.RequestContext) {
			mu.Lock()
			events = append(events, hook)
			mu.Unlock()
		}
		if w := g.chat(t, chatBody(model), nil); w.Code != http.StatusOK {
			t.Fatalf("%s: status = %d; body %s", model, w.Code, w.Body)
		}
		waitSnapshot(t, snaps)
		mu.Lock()
		budgetAt, before, after := -1, 0, 0
		for i, e := range events {
			switch {
			case e == hookRunBudgetCap:
				budgetAt = i
			case e == hookCheckEligibility && budgetAt < 0:
				before++
			case e == hookCheckEligibility:
				after++
			}
		}
		mu.Unlock()
		if budgetAt < 0 || before != 0 || after == 0 {
			t.Errorf("%s: checkEligibility ran %d times before step 10 and %d after, want none before: %v",
				model, before, after, events)
		}
		checks[model] = after
	}
	if checks["omega-default"] != checks["auto"] {
		t.Errorf("checkEligibility ran %d times for omega-default and %d for auto; the alias is auto",
			checks["omega-default"], checks["auto"])
	}
}

// An org-less request is judged too when nothing can limit its pool (no key
// store, so no license bit to widen it): a self-hosted gateway with nothing
// auto can use gets the contracted error and remedy, not no_auto_candidate.
func TestOmegaDefaultUnroutableWithoutOrg(t *testing.T) {
	chat := func(t *testing.T, cfg *config.Config) *httptest.ResponseRecorder {
		t.Helper()
		registry, err := providers.NewRegistry(cfg)
		if err != nil {
			t.Fatalf("registry: %v", err)
		}
		srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
		srv.ready.Store(true)
		req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(chatBody("omega-default")))
		req.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)
		return w
	}
	var resp struct {
		Error struct {
			Code     string            `json:"code"`
			Metadata map[string]string `json:"metadata"`
		} `json:"error"`
	}

	empty := config.DefaultConfig()
	empty.Auth.Enabled = false
	w := chat(t, empty)
	_ = json.Unmarshal(w.Body.Bytes(), &resp)
	if w.Code != http.StatusBadRequest || resp.Error.Code != "omega_default_unroutable" ||
		resp.Error.Metadata["remedy_hint"] != "enable managed models or add a provider" {
		t.Fatalf("no providers: status %d body %s", w.Code, w.Body)
	}

	up := startHookUpstream(t, 0)
	served := config.DefaultConfig()
	served.Auth.Enabled = false
	served.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "dummy", APIFormat: "openai", Models: []string{"gpt-4o"}}
	if w := chat(t, served); w.Code != http.StatusOK {
		t.Errorf("a provider auto can use: status %d body %s", w.Code, w.Body)
	}
}
