package server

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
)

// TestResolveProvider_EmbeddingModelOverride_NoNilPanic is the regression test for #336.
//
// resolveProvider applied a routing ModelOverride by writing rc.Request.Model unconditionally.
// resolveProvider is called from BOTH the chat and the embedding handlers, but rc.Request is
// only populated on the chat path -- embeddings populate rc.EmbeddingRequest and leave
// rc.Request nil. So an embedding request that matched a ModelOverride dereferenced a nil
// rc.Request and panicked. The fix guards every override assignment:
//
//	if rc.Request != nil {
//	    rc.Request.Model = override
//	} else if rc.EmbeddingRequest != nil {
//	    rc.EmbeddingRequest.Model = override
//	}
//
// This drives the embedding path (rc.Request == nil, rc.EmbeddingRequest set) through a
// conditional-route ModelOverride and asserts the override lands on rc.EmbeddingRequest.Model.
// With any guard removed, resolveProvider dereferences the nil rc.Request and this test panics,
// which the Go test runner reports as a failure -- so the test fails without the fix.
func TestResolveProvider_EmbeddingModelOverride_NoNilPanic(t *testing.T) {
	mock := httptest.NewServer(http.NotFoundHandler())
	defer mock.Close()

	cfg := config.DefaultConfig()
	cfg.Providers["openai"] = config.ProviderConfig{
		BaseURL:   mock.URL,
		APIFormat: "openai",
		Models:    []string{"gpt-4o", "gpt-4o-mini"},
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("NewRegistry: %v", err)
	}
	defer registry.Close()

	// A conditional route that deterministically applies a ModelOverride for gpt-4o.
	cr, err := routing.NewConditionalRouter([]config.ConditionalRouteConfig{
		{
			Name:      "embedding-override",
			Condition: config.ConditionConfig{Field: "model", Op: "$eq", Value: "gpt-4o"},
			Action:    config.RouteActionConfig{Provider: "openai", ModelOverride: "gpt-4o-mini"},
		},
	})
	if err != nil {
		t.Fatalf("NewConditionalRouter: %v", err)
	}

	h := &Handlers{registry: registry, defaultTimeout: 60 * time.Second}
	h.conditionalRouter.Store(cr)

	// EMBEDDING request context: rc.Request is nil, rc.EmbeddingRequest is populated.
	rc := &models.RequestContext{
		Model:            "gpt-4o",
		Request:          nil,
		EmbeddingRequest: &models.EmbeddingRequest{Model: "gpt-4o"},
		Metadata:         map[string]string{},
	}

	// Pre-fix, this call dereferences nil rc.Request and panics.
	provider, err := h.resolveProvider(context.Background(), rc, "gpt-4o")
	if err != nil {
		t.Fatalf("resolveProvider returned error: %v", err)
	}
	if provider == nil {
		t.Fatal("resolveProvider returned nil provider")
	}
	if rc.Request != nil {
		t.Fatal("rc.Request must remain nil on the embedding path")
	}
	if rc.EmbeddingRequest.Model != "gpt-4o-mini" {
		t.Fatalf("ModelOverride not applied to the embedding request: got %q, want %q",
			rc.EmbeddingRequest.Model, "gpt-4o-mini")
	}
}
