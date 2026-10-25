package server

// GET /v1/capabilities has a fixed shape (release notes C51): this contract
// test pins its response shape for a key (field names and JSON types, never
// values, which change per org), so the shape cannot change without failing
// here. Additive fields pass; removing, renaming or retyping a pinned field
// fails.

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

// capabilityFeaturesShape is capabilities-v1's feature set (features, flags).
var capabilityFeaturesShape = shapeObject(map[string]jsonShape{
	"stream": shapeBool(), "tools": shapeBool(), "json_schema": shapeBool(), "vision": shapeBool(),
	"audio_input": shapeBool(), "reasoning": shapeBool(), "prompt_caching": shapeBool(),
})

// capabilitiesMatrixShape is GET /v1/capabilities.
var capabilitiesMatrixShape = shapeObject(map[string]jsonShape{
	"generated_at": shapeString(),
	"endpoints": shapeArray(shapeObject(map[string]jsonShape{
		"endpoint":    shapeString(),
		"launch_tier": shapeString(),
		"providers": shapeMap(shapeObject(map[string]jsonShape{
			"status":          shapeString(),
			"credential_type": shapeString(),
			"features":        capabilityFeaturesShape,
			"managed_allowed": shapeBool(),
			"price": shapeObject(map[string]jsonShape{
				"sku": shapeString(), "usd_per_unit_billed": shapeNumber(), "markup_percent": shapeNumber(),
			}),
			"source":       shapeString(),
			"qualified":    shapeBool(),
			"qualified_at": shapeNullable(shapeString()),
		}, "price")),
	})),
})

// capabilitiesModelShape is GET /v1/capabilities?model=<id>.
var capabilitiesModelShape = shapeObject(map[string]jsonShape{
	"generated_at": shapeString(),
	"model":        shapeString(),
	"providers": shapeMap(shapeObject(map[string]jsonShape{
		"credential_type":   shapeString(),
		"source":            shapeString(),
		"flags":             capabilityFeaturesShape,
		"context_length":    shapeNumber(),
		"max_output_tokens": shapeNumber(),
		"modalities":        shapeObject(map[string]jsonShape{"input": shapeNullable(shapeArray(shapeString())), "output": shapeNullable(shapeArray(shapeString()))}),
		"endpoints":         shapeMap(shapeObject(map[string]jsonShape{"status": shapeString(), "managed_allowed": shapeBool()})),
	})),
})

func TestCapabilitiesContract(t *testing.T) {
	up := startHookUpstream(t, 0)
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "internal", Key: "sk-cap", Owner: "t", KeyType: "internal"},
		{Name: "org", Key: "sk-cap-org", Owner: "t", KeyType: "byok", Metadata: map[string]string{"org_id": "org-cap"}},
	}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "dummy", APIFormat: "openai", Models: []string{"gpt-4o"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	tenants := tenant.NewStore()
	tenants.Set("org-cap", &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
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

	for _, key := range []string{"sk-cap", "sk-cap-org"} {
		w := get("/v1/capabilities", key)
		if w.Code != http.StatusOK || !strings.HasPrefix(w.Header().Get("Content-Type"), "application/json") {
			t.Fatalf("%s: HTTP %d %q: %s", key, w.Code, w.Header().Get("Content-Type"), w.Body.String())
		}
		if extra := checkShape(t, w.Body.Bytes(), capabilitiesMatrixShape); len(extra) > 0 {
			t.Logf("%s: fields outside the frozen shape (additive, allowed): %v", key, extra)
		}
		// A matrix with no rows or no provider cells would pin nothing.
		if !hasNestedMapEntry(w.Body.Bytes(), "endpoints", "providers") {
			t.Fatalf("%s: the matrix has no provider cell to check: %s", key, w.Body.String())
		}

		w = get("/v1/capabilities?model=gpt-4o", key)
		if w.Code != http.StatusOK {
			t.Fatalf("%s ?model=gpt-4o: HTTP %d %s", key, w.Code, w.Body.String())
		}
		if extra := checkShape(t, w.Body.Bytes(), capabilitiesModelShape); len(extra) > 0 {
			t.Logf("%s ?model: fields outside the frozen shape (additive, allowed): %v", key, extra)
		}

		w = get("/v1/capabilities?model=no-such-model", key)
		if w.Code != http.StatusNotFound || deadlineErrorCode(w.Body.Bytes()) != "model_not_found" {
			t.Fatalf("%s: an unknown model: HTTP %d %s, want 404 model_not_found", key, w.Code, w.Body.String())
		}
		checkShape(t, w.Body.Bytes(), errorShape)
	}
	// Customers never see internal legal status.
	if w := get("/v1/capabilities", "sk-cap"); bodyHasField(w.Body.Bytes(), "managed_resale_terms") {
		t.Fatalf("/v1/capabilities carries managed_resale_terms: %s", w.Body.String())
	}
}
