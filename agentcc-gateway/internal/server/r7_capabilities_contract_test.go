package server

// GET /v1/capabilities is frozen. The release notes told
// callers not to build on it ("both shapes will change"); this contract test
// pins its response shape for a key (field names and JSON types, never
// values: org models change capability values per org), so the shape
// cannot change without failing here. Additive fields pass; removing,
// renaming or retyping a pinned field fails. Test only: capabilities_routes.go
// belongs to the org models change. Green at the release base by design (a freeze, not a
// fix).

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// r7CapabilityFeatures is capabilities-v1's feature set (features, flags).
var r7CapabilityFeatures = r7Object(map[string]r7Shape{
	"stream": r7Bool(), "tools": r7Bool(), "json_schema": r7Bool(), "vision": r7Bool(),
	"audio_input": r7Bool(), "reasoning": r7Bool(), "prompt_caching": r7Bool(),
})

// r7CapabilitiesMatrix is GET /v1/capabilities.
var r7CapabilitiesMatrix = r7Object(map[string]r7Shape{
	"generated_at": r7String(),
	"endpoints": r7Array(r7Object(map[string]r7Shape{
		"endpoint":    r7String(),
		"launch_tier": r7String(),
		"providers": r7Map(r7Object(map[string]r7Shape{
			"status":          r7String(),
			"credential_type": r7String(),
			"features":        r7CapabilityFeatures,
			"managed_allowed": r7Bool(),
			"price": r7Object(map[string]r7Shape{
				"sku": r7String(), "usd_per_unit_billed": r7Number(), "markup_percent": r7Number(),
			}),
			"source":       r7String(),
			"qualified":    r7Bool(),
			"qualified_at": r7Nullable(r7String()),
		}, "price")),
	})),
})

// r7CapabilitiesModel is GET /v1/capabilities?model=<id>.
var r7CapabilitiesModel = r7Object(map[string]r7Shape{
	"generated_at": r7String(),
	"model":        r7String(),
	"providers": r7Map(r7Object(map[string]r7Shape{
		"credential_type":   r7String(),
		"source":            r7String(),
		"flags":             r7CapabilityFeatures,
		"context_length":    r7Number(),
		"max_output_tokens": r7Number(),
		"modalities":        r7Object(map[string]r7Shape{"input": r7Nullable(r7Array(r7String())), "output": r7Nullable(r7Array(r7String()))}),
		"endpoints":         r7Map(r7Object(map[string]r7Shape{"status": r7String(), "managed_allowed": r7Bool()})),
	})),
})

func TestR7CapabilitiesContract(t *testing.T) {
	up := startHookUpstream(t, 0)
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "internal", Key: "sk-r7-cap", Owner: "t", KeyType: "internal"},
		{Name: "org", Key: "sk-r7-cap-org", Owner: "t", KeyType: "byok", Metadata: map[string]string{"org_id": "org-r7-cap"}},
	}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "dummy", APIFormat: "openai", Models: []string{"gpt-4o"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	tenants := tenant.NewStore()
	tenants.Set("org-r7-cap", &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"openai": {APIKey: "org-own-key", Enabled: true, Models: []string{"gpt-4o"}},
	}})
	keyStore := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keyStore, true)), keyStore, nil, nil, nil, testModelDBPtr(), tenants, nil)
	srv.ready.Store(true)
	get := func(path, key string) *httptest.ResponseRecorder {
		req := httptest.NewRequest(http.MethodGet, path, nil)
		req.Header.Set("Authorization", "Bearer "+key)
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)
		return w
	}

	for _, key := range []string{"sk-r7-cap", "sk-r7-cap-org"} {
		w := get("/v1/capabilities", key)
		if w.Code != http.StatusOK || !strings.HasPrefix(w.Header().Get("Content-Type"), "application/json") {
			t.Fatalf("%s: HTTP %d %q: %s", key, w.Code, w.Header().Get("Content-Type"), w.Body.String())
		}
		if extra := r7CheckShape(t, w.Body.Bytes(), r7CapabilitiesMatrix); len(extra) > 0 {
			t.Logf("%s: fields outside the frozen shape (additive, allowed): %v", key, extra)
		}
		// A matrix with no rows or no provider cells would pin nothing.
		if !r7HasNestedMapEntry(w.Body.Bytes(), "endpoints", "providers") {
			t.Fatalf("%s: the matrix has no provider cell to check: %s", key, w.Body.String())
		}

		w = get("/v1/capabilities?model=gpt-4o", key)
		if w.Code != http.StatusOK {
			t.Fatalf("%s ?model=gpt-4o: HTTP %d %s", key, w.Code, w.Body.String())
		}
		if extra := r7CheckShape(t, w.Body.Bytes(), r7CapabilitiesModel); len(extra) > 0 {
			t.Logf("%s ?model: fields outside the frozen shape (additive, allowed): %v", key, extra)
		}

		w = get("/v1/capabilities?model=no-such-model", key)
		if w.Code != http.StatusNotFound || r7AgentErrorCode(w.Body.Bytes()) != "model_not_found" {
			t.Fatalf("%s: an unknown model: HTTP %d %s, want 404 model_not_found", key, w.Code, w.Body.String())
		}
		r7CheckShape(t, w.Body.Bytes(), r7ErrorShape)
	}
	// Customers never see internal legal status.
	if w := get("/v1/capabilities", "sk-r7-cap"); r7BodyHas(w.Body.Bytes(), "managed_resale_terms") {
		t.Fatalf("/v1/capabilities carries managed_resale_terms: %s", w.Body.String())
	}
}
