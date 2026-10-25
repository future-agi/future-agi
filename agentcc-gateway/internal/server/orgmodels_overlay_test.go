package server

// Through the gateway: an org's Model Database overrides
// (Settings > Org Config > Model Database) price its BYOK call, hold its
// token limits and capability switches on the request (the validation
// plugin with the tenant store), and are what
// /v1/count_tokens, /v1/capabilities and /v1/models report to its key.

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	validationplugin "github.com/futureagi/agentcc-gateway/internal/plugins/validation"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// orgModelsOverlayOrg is the org config the console's Model Database tab saves
// for gpt-4o: prices of $1/M in and $4/M out, max output 5, max input 1,000
// and function calling off.
func orgModelsOverlayOrg(t *testing.T) *tenant.OrgConfig {
	t.Helper()
	var cfg tenant.OrgConfig
	raw := `{"model_database":{"overrides":{"gpt-4o":{"max_input_tokens":1000,"max_output_tokens":5,` +
		`"pricing":{"input_per_token":0.000001,"output_per_token":0.000004},` +
		`"capabilities":{"function_calling":false}}}}}`
	if err := json.Unmarshal([]byte(raw), &cfg); err != nil {
		t.Fatal(err)
	}
	return &cfg
}

// orgModelsOverlayGateway is orgModelsGateway serving gpt-4o on "openai" with the
// validation plugin as main.go builds it (with SetTenantStore), and the
// cost plugin as main.go builds it.
func orgModelsOverlayGateway(t *testing.T, up *orgModelsUpstream, org *tenant.OrgConfig) *Server {
	t.Helper()
	db := testModelDBPtr()
	store := tenant.NewStore()
	vp := validationplugin.New(true, func() *modeldb.ModelDB { return db.Load() })
	vp.SetTenantStore(store)
	cost := costplugin.New(true, func() *modeldb.ModelDB { return db.Load() }, store, nil)
	srv := orgModelsGateway(t, up, "openai", []string{"gpt-4o"}, org, vp, cost)
	// The plugins read the same org configs the server was given.
	if got := srv.handlers.tenantStore.Get(orgModelsOrg); got != nil {
		store.Set(orgModelsOrg, got)
	}
	return srv
}

func orgModelsChatBody(extra string) string {
	return `{"model":"gpt-4o","messages":[{"role":"user","content":"hello"}]` + extra + `}`
}

const orgModelsTools = `,"tools":[{"type":"function","function":{"name":"lookup","parameters":{"type":"object"}}}]`

// TestModelDBOverlayThroughGateway: a request over the org's
// max_output_tokens and one with tools are refused before the upstream;
// one without max_tokens is sent with the org's 5; the BYOK call is priced
// at the org's override (1,000 x $1/M + 500 x $4/M = $0.003), not the
// model database's $0.0075.
func TestModelDBOverlayThroughGateway(t *testing.T) {
	up := startOrgModelsUpstream(t)
	srv := orgModelsOverlayGateway(t, up, orgModelsOverlayOrg(t))

	w := orgModelsChat(t, srv, orgModelsChatBody(`,"max_tokens":500`), nil)
	if code, msg := orgModelsErr(w); w.Code != http.StatusBadRequest || code != "max_tokens_exceeds_limit" || !strings.Contains(msg, "max_output_tokens 5") {
		t.Fatalf("max_tokens 500 over the org's max_output_tokens 5 -> %d %s %q; want 400 max_tokens_exceeds_limit naming the limit", w.Code, code, msg)
	}
	w = orgModelsChat(t, srv, orgModelsChatBody(orgModelsTools), nil)
	if code, msg := orgModelsErr(w); w.Code != http.StatusBadRequest || code != "model_capability_error" || !strings.Contains(msg, "function_calling") {
		t.Fatalf("tools with function_calling off -> %d %s %q; want 400 model_capability_error naming function_calling", w.Code, code, msg)
	}
	if s := up.seen(); len(s) != 0 {
		t.Fatalf("refused requests reached the upstream: %v", s)
	}

	w = orgModelsChat(t, srv, orgModelsChatBody(""), nil)
	if w.Code != http.StatusOK {
		t.Fatalf("plain request -> %d %s", w.Code, w.Body.String())
	}
	body := up.lastBody()
	var sent int
	if raw, ok := body["max_tokens"]; ok {
		_ = json.Unmarshal(raw, &sent)
	} else if raw, ok := body["max_completion_tokens"]; ok {
		_ = json.Unmarshal(raw, &sent)
	}
	if sent != 5 {
		t.Fatalf("upstream body max_tokens/max_completion_tokens = %d (body %v); want the org's 5", sent, body)
	}
	cost, err := strconv.ParseFloat(w.Header().Get("x-agentcc-cost"), 64)
	if err != nil || cost < 0.0029999 || cost > 0.0030001 {
		t.Fatalf("x-agentcc-cost = %q; want 0.003 (the org's override price), not 0.0075 (the model database's)", w.Header().Get("x-agentcc-cost"))
	}
}

// TestModelDBOverlayCountTokens: /v1/count_tokens reports the org's
// max_input_tokens (1,000) as the window, not the model database's 128,000,
// also when the override is keyed "<provider>/<model>" (as chat matches it).
func TestModelDBOverlayCountTokens(t *testing.T) {
	prefixed := orgModelsOverlayOrg(t)
	prefixed.ModelDatabase.Overrides["openai/gpt-4o"] = prefixed.ModelDatabase.Overrides["gpt-4o"]
	delete(prefixed.ModelDatabase.Overrides, "gpt-4o")
	for name, org := range map[string]*tenant.OrgConfig{"gpt-4o": orgModelsOverlayOrg(t), "openai/gpt-4o": prefixed} {
		srv := orgModelsGateway(t, startOrgModelsUpstream(t), "openai", []string{"gpt-4o"}, org)
		req := httptest.NewRequest(http.MethodPost, "/v1/count_tokens", strings.NewReader(orgModelsChatBody("")))
		req.Header.Set("Authorization", "Bearer "+orgModelsKey)
		req.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)
		var resp struct {
			TokenCount       int `json:"token_count"`
			MaxContextTokens int `json:"max_context_tokens"`
			RemainingTokens  int `json:"remaining_tokens"`
		}
		_ = json.Unmarshal(w.Body.Bytes(), &resp)
		if w.Code != http.StatusOK || resp.MaxContextTokens != 1000 || resp.RemainingTokens != 1000-resp.TokenCount {
			t.Fatalf("override keyed %q: count_tokens -> %d %s; want max_context_tokens 1000 and remaining 1000 - token_count",
				name, w.Code, w.Body.String())
		}
	}
}

// TestModelDBOverlayCapabilities: /v1/capabilities?model=gpt-4o for the
// org's key shows the org's limits and its function_calling switch.
func TestModelDBOverlayCapabilities(t *testing.T) {
	srv := orgModelsGateway(t, startOrgModelsUpstream(t), "openai", []string{"gpt-4o"}, orgModelsOverlayOrg(t))
	req := httptest.NewRequest(http.MethodGet, "/v1/capabilities?model=gpt-4o", nil)
	req.Header.Set("Authorization", "Bearer "+orgModelsKey)
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	var v struct {
		Providers map[string]struct {
			Flags struct {
				Tools bool `json:"tools"`
			} `json:"flags"`
			ContextLength   int `json:"context_length"`
			MaxOutputTokens int `json:"max_output_tokens"`
		} `json:"providers"`
	}
	_ = json.Unmarshal(w.Body.Bytes(), &v)
	cell, ok := v.Providers["openai"]
	if w.Code != http.StatusOK || !ok {
		t.Fatalf("/v1/capabilities?model=gpt-4o -> %d %s", w.Code, w.Body.String())
	}
	if cell.ContextLength != 1000 || cell.MaxOutputTokens != 5 || cell.Flags.Tools {
		t.Fatalf("openai/gpt-4o cell = context %d, max output %d, tools %v; want the org's 1000, 5, false (body %s)",
			cell.ContextLength, cell.MaxOutputTokens, cell.Flags.Tools, w.Body.String())
	}
}

// TestModelDBOverlayModelsList: /v1/models describes the org's model as
// the org sees it: its limits, prices and switches.
func TestModelDBOverlayModelsList(t *testing.T) {
	srv := orgModelsGateway(t, startOrgModelsUpstream(t), "openai", []string{"gpt-4o"}, orgModelsOverlayOrg(t))
	req := httptest.NewRequest(http.MethodGet, "/v1/models", nil)
	req.Header.Set("Authorization", "Bearer "+orgModelsKey)
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	var l struct {
		Data []struct {
			ID              string `json:"id"`
			MaxInputTokens  int    `json:"max_input_tokens"`
			MaxOutputTokens int    `json:"max_output_tokens"`
			Pricing         *struct {
				In  float64 `json:"input_per_m_tokens"`
				Out float64 `json:"output_per_m_tokens"`
			} `json:"pricing"`
			Capabilities *struct {
				FunctionCalling bool `json:"function_calling"`
			} `json:"capabilities"`
		} `json:"data"`
	}
	_ = json.Unmarshal(w.Body.Bytes(), &l)
	for _, m := range l.Data {
		if m.ID != "gpt-4o" {
			continue
		}
		if m.MaxInputTokens != 1000 || m.MaxOutputTokens != 5 || m.Pricing == nil || m.Pricing.In != 1 || m.Pricing.Out != 4 ||
			m.Capabilities == nil || m.Capabilities.FunctionCalling {
			t.Fatalf("gpt-4o listed as %s; want the org's limits 1000/5, $1/$4 per 1M and function_calling false", w.Body.String())
		}
		return
	}
	t.Fatalf("gpt-4o not listed: %d %s", w.Code, w.Body.String())
}

// TestModelDBOverlayAfterAutoPick: model "auto" picks gpt-4o (the org's
// only model), and the validation plugin then holds the call to the org's
// Model Database entry for the model auto picked: a request without
// max_tokens is sent with the org's 5, and the answer names gpt-4o.
func TestModelDBOverlayAfterAutoPick(t *testing.T) {
	up := startOrgModelsUpstream(t)
	srv := orgModelsOverlayGateway(t, up, orgModelsOverlayOrg(t))

	w := orgModelsChat(t, srv, `{"model":"auto","messages":[{"role":"user","content":"hello"}]}`, nil)
	if w.Code != http.StatusOK {
		t.Fatalf("auto -> %d %s", w.Code, w.Body.String())
	}
	body := up.lastBody()
	var model string
	_ = json.Unmarshal(body["model"], &model)
	var sent int
	if raw, ok := body["max_tokens"]; ok {
		_ = json.Unmarshal(raw, &sent)
	} else if raw, ok := body["max_completion_tokens"]; ok {
		_ = json.Unmarshal(raw, &sent)
	}
	if model != "gpt-4o" || sent != 5 {
		t.Fatalf("auto sent model %q with max_tokens %d (body %v); want gpt-4o with the org's 5", model, sent, body)
	}
}
