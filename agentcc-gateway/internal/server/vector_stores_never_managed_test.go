package server

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// /v1/vector_stores is BYOK-only: a vector store lives in the upstream
// account that created it, and the gateway neither bills its storage nor
// scopes FutureAGI's account per org, so a managed upstream must never serve
// one. Two things keep it true: managedEndpoint admits only chat, and the
// vector-store proxy resolves no model at all: it goes to the org's own
// "openai" provider, or to the global one, which a key limited to its org's
// providers may not use (501 not_supported). The org has opted in to managed
// models, has credit, and has no OpenAI key of its own, and the managed
// catalog lists gpt-4o, so a managed route would be the only way to serve
// these requests.
func TestVectorStoresNeverManaged(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	g.ledger.SetMicros("org-m", auth.USDToMicros(5))
	withGPT4o := map[string]*catalog.Model{"gpt-4o": {ID: "gpt-4o", Created: 1790000000, ContextLength: 128000,
		OutputModalities: []string{"text"}, InputPerToken: 2.5e-6, OutputPerToken: 10e-6}}
	for id, m := range testCatalogModels {
		withGPT4o[id] = m
	}
	g.srv.handlers.catalog.Put(&catalog.Snapshot{ProviderID: "openrouter", Source: "bundled",
		CatalogSource: catalog.SourceOpenRouter, FetchedAt: time.Now(), Models: withGPT4o})
	if !g.srv.handlers.catalogHas("openrouter", "gpt-4o") {
		t.Fatal("setup: the managed catalog does not list gpt-4o")
	}

	for _, et := range []string{"vector_stores", "assistants"} {
		if managedEndpoint(&models.RequestContext{EndpointType: et}) {
			t.Errorf("managedEndpoint(%q) = true; vector stores must stay BYOK-only", et)
		}
	}

	routes := []struct{ method, path, body string }{
		{"POST", "/v1/vector_stores", `{"name":"kb"}`},
		{"GET", "/v1/vector_stores", ""},
		{"GET", "/v1/vector_stores/vs_1", ""},
		{"POST", "/v1/vector_stores/vs_1", `{"name":"kb2"}`},
		{"POST", "/v1/vector_stores/vs_1/search", `{"query":"refund policy"}`},
		{"POST", "/v1/vector_stores/vs_1/files", `{"file_id":"file-1"}`},
		{"GET", "/v1/vector_stores/vs_1/files", ""},
		{"DELETE", "/v1/vector_stores/vs_1/files/file-1", ""},
		{"POST", "/v1/vector_stores/vs_1/file_batches", `{"file_ids":["file-1"]}`},
	}
	for _, rt := range routes {
		t.Run(rt.method+" "+rt.path, func(t *testing.T) {
			req := httptest.NewRequest(rt.method, rt.path, bytes.NewBufferString(rt.body))
			req.Header.Set("Authorization", "Bearer "+managedOrgKey)
			req.Header.Set("Content-Type", "application/json")
			w := httptest.NewRecorder()
			g.srv.httpServer.Handler.ServeHTTP(w, req)

			// The managed upstream answers any non-chat path with 200, so a
			// request it served would come back 200.
			if w.Code != http.StatusNotImplemented || !strings.Contains(w.Body.String(), `"not_supported"`) {
				t.Fatalf("status = %d, want 501 not_supported (no managed upstream for vector stores): %s", w.Code, w.Body.String())
			}
			if p := w.Header().Get("x-agentcc-provider"); p != "" {
				t.Errorf("x-agentcc-provider = %q; nothing should have served the request", p)
			}
		})
	}
	if n := len(g.outbox("org-m")); n != 0 {
		t.Errorf("settlement records = %d, want 0: no vector-store call may bill the managed ledger", n)
	}
}
