package server

// The Model Database tab says a Prompt Caching override
// "only changes what /v1/models and /v1/capabilities report" (it has no
// request-side signal, so the validation plugin never checks it). This holds
// that claim: the org's prompt_caching switch, set against the model
// database's own value for gpt-4o, flips the flag both endpoints report to
// the org's key, and Default (no override) reports the database's value.

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// orgModelsPromptCachingReported is the prompt_caching flag /v1/models and
// /v1/capabilities?model=gpt-4o report for gpt-4o to the org's key.
func orgModelsPromptCachingReported(t *testing.T, org *tenant.OrgConfig) (listed, capabilities bool) {
	t.Helper()
	srv := orgModelsGateway(t, startOrgModelsUpstream(t), "openai", []string{"gpt-4o"}, org)
	get := func(path string) []byte {
		req := httptest.NewRequest(http.MethodGet, path, nil)
		req.Header.Set("Authorization", "Bearer "+orgModelsKey)
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)
		if w.Code != http.StatusOK {
			t.Fatalf("GET %s -> %d %s", path, w.Code, w.Body.String())
		}
		return w.Body.Bytes()
	}

	var l struct {
		Data []struct {
			ID           string `json:"id"`
			Capabilities *struct {
				PromptCaching bool `json:"prompt_caching"`
			} `json:"capabilities"`
		} `json:"data"`
	}
	body := get("/v1/models")
	if err := json.Unmarshal(body, &l); err != nil {
		t.Fatalf("/v1/models: %v: %s", err, body)
	}
	found := false
	for _, m := range l.Data {
		if m.ID == "gpt-4o" {
			if m.Capabilities == nil {
				t.Fatalf("/v1/models lists gpt-4o without capabilities: %s", body)
			}
			listed, found = m.Capabilities.PromptCaching, true
		}
	}
	if !found {
		t.Fatalf("/v1/models does not list gpt-4o: %s", body)
	}

	var v struct {
		Providers map[string]struct {
			Flags struct {
				PromptCaching bool `json:"prompt_caching"`
			} `json:"flags"`
		} `json:"providers"`
	}
	body = get("/v1/capabilities?model=gpt-4o")
	if err := json.Unmarshal(body, &v); err != nil {
		t.Fatalf("/v1/capabilities: %v: %s", err, body)
	}
	cell, ok := v.Providers["openai"]
	if !ok {
		t.Fatalf("/v1/capabilities?model=gpt-4o has no openai entry: %s", body)
	}
	return listed, cell.Flags.PromptCaching
}

func TestModelDBCapabilitiesPromptCachingReported(t *testing.T) {
	info, ok := modeldb.New(modeldb.BundledModels, nil).Get("gpt-4o")
	if !ok {
		t.Fatal("the bundled model database has no gpt-4o")
	}
	dbValue := info.Capabilities.PromptCaching

	listed, caps := orgModelsPromptCachingReported(t, &tenant.OrgConfig{})
	if listed != dbValue || caps != dbValue {
		t.Fatalf("Default: /v1/models prompt_caching %v, /v1/capabilities %v; want the model database's %v", listed, caps, dbValue)
	}

	flipped := !dbValue
	var org tenant.OrgConfig
	raw := `{"model_database":{"overrides":{"gpt-4o":{"capabilities":{"prompt_caching":` + boolJSON(flipped) + `}}}}}`
	if err := json.Unmarshal([]byte(raw), &org); err != nil {
		t.Fatal(err)
	}
	listed, caps = orgModelsPromptCachingReported(t, &org)
	if listed != flipped || caps != flipped {
		t.Fatalf("override prompt_caching %v: /v1/models reports %v, /v1/capabilities %v; want the org's %v", flipped, listed, caps, flipped)
	}
}

func boolJSON(b bool) string {
	if b {
		return "true"
	}
	return "false"
}
