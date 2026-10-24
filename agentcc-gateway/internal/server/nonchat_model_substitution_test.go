package server

import (
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/routing"
)

// On the model-fallback and model-override paths a non-chat request gets
// its model's own resolution answer (rc.Request is nil on every handler that
// is not chat). A non-chat request never takes a model fallback: an
// embedding served by another model lands in another vector space.
// resolveProviderFor returns the model to send, so a route that names
// another model for a non-chat request is served with that model.

func TestNonChatRequestTakesNoModelFallback(t *testing.T) {
	g := newEmbedGateway(t, nil, nil)
	g.srv.handlers.modelFallbacks.Store(routing.NewModelFallbacks(map[string][]string{"unserved-embed": {"other-embed"}}))
	w := g.post(t, "/v1/embeddings", embedInternalKey, `{"model":"unserved-embed","input":"x"}`, nil)
	if w.Code < http.StatusBadRequest || w.Code >= http.StatusInternalServerError {
		t.Fatalf("status = %d, want the model's own 4xx resolution error: %s", w.Code, w.Body.String())
	}
	if strings.Contains(w.Body.String(), "internal_error") {
		t.Fatalf("the resolution panicked: %s", w.Body.String())
	}
	for id, u := range g.upstreams {
		if got := u.calls.Load(); got != 0 {
			t.Errorf("provider %s was called %d times", id, got)
		}
	}
}

func TestNonChatRouteSendsTheRoutedModel(t *testing.T) {
	g := newEmbedGateway(t, nil, nil)
	cr, err := routing.NewConditionalRouter([]config.ConditionalRouteConfig{{
		Name: "embed-elsewhere", Priority: 10,
		Condition: config.ConditionConfig{Field: "model", Op: "$eq", Value: "text-embedding-3-small"},
		Action:    config.RouteActionConfig{Provider: "other", ModelOverride: "other-embed"},
	}})
	if err != nil {
		t.Fatal(err)
	}
	g.srv.handlers.conditionalRouter.Store(cr)
	w := g.post(t, "/v1/embeddings", embedInternalKey, `{"model":"text-embedding-3-small","input":"x"}`, nil)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d %s, want 200 from the routed provider", w.Code, w.Body.String())
	}
	if got := g.upstreams["oai"].calls.Load(); got != 0 {
		t.Errorf("oai was called %d times; the route sends the request to other", got)
	}
	other := g.upstreams["other"]
	if got := other.calls.Load(); got != 1 {
		t.Fatalf("other was called %d times, want 1", got)
	}
	other.mu.Lock()
	sent := other.bodies[0]
	other.mu.Unlock()
	if !strings.Contains(sent, `"model":"other-embed"`) || strings.Contains(sent, "text-embedding-3-small") {
		t.Errorf("upstream body = %s, want the routed model other-embed and not the requested one", sent)
	}

	// A route that names the model the request already asked for changes
	// nothing, so it is served as asked.
	same, err := routing.NewConditionalRouter([]config.ConditionalRouteConfig{{
		Name: "embed-same", Priority: 10,
		Condition: config.ConditionConfig{Field: "model", Op: "$eq", Value: "text-embedding-3-small"},
		Action:    config.RouteActionConfig{Provider: "oai", ModelOverride: "text-embedding-3-small"},
	}})
	if err != nil {
		t.Fatal(err)
	}
	g.srv.handlers.conditionalRouter.Store(same)
	w = g.post(t, "/v1/embeddings", embedInternalKey, `{"model":"text-embedding-3-small","input":"x"}`, nil)
	if w.Code != http.StatusOK {
		t.Fatalf("same-model route: status = %d %s, want 200", w.Code, w.Body.String())
	}
	if got := g.upstreams["oai"].calls.Load(); got != 1 {
		t.Errorf("same-model route: oai called %d times, want 1", got)
	}
}
