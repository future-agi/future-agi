package server

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// sellableModel is a catalogue entry the managed resolver may sell.
func sellableModel(id string) *catalog.Model {
	return &catalog.Model{ID: id, Created: 1790000000, ContextLength: 128000, MaxOutputTokens: 16000,
		InputModalities: []string{"text"}, OutputModalities: []string{"text"},
		InputPerToken: 1e-6, OutputPerToken: 2e-6}
}

// withCatalogModels replaces the test gateway's managed catalogue with
// testCatalogModels plus extra.
func withCatalogModels(t *testing.T, g *managedTestGateway, extra ...string) {
	t.Helper()
	all := make(map[string]*catalog.Model, len(testCatalogModels)+len(extra))
	for id, m := range testCatalogModels {
		all[id] = m
	}
	for _, id := range extra {
		all[id] = sellableModel(id)
	}
	g.srv.handlers.catalog.Put(&catalog.Snapshot{ProviderID: "openrouter", Source: "bundled",
		CatalogSource: catalog.SourceOpenRouter, FetchedAt: time.Now(), Models: all})
	for _, id := range extra {
		if !g.srv.handlers.catalogHas("openrouter", id) {
			t.Fatalf("setup: the managed catalogue does not sell %s", id)
		}
	}
}

// TestManagedPlainModelNamesResolve: on one Future AGI key, the names SDKs
// send reach the catalogue model they name, on chat and on /v1/messages:
// "gpt-4o", a dated Claude id (what Claude Code sends) and a bare Gemini id.
func TestManagedPlainModelNamesResolve(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	g.ledger.SetMicros("org-m", auth.USDToMicros(5))
	withCatalogModels(t, g, "openai/gpt-4o", "google/gemini-3.8-flash")

	for name, want := range map[string]string{
		"gpt-4o":                     "openai/gpt-4o",
		"claude-sonnet-5-5-20260928": "anthropic/claude-sonnet-5.5",
		"claude-sonnet-5-5":          "anthropic/claude-sonnet-5.5",
		"gemini-3.8-flash":           "google/gemini-3.8-flash",
	} {
		w := g.chat(t, chatBody(name), nil)
		if w.Code != http.StatusOK || g.managed.lastModel() != want {
			t.Errorf("chat %s: status %d, upstream model %q, want 200 on %s; body %s", name, w.Code, g.managed.lastModel(), want, w.Body)
		}
		if got := w.Header().Get("x-agentcc-billing-mode"); w.Code == http.StatusOK && got != "managed" {
			t.Errorf("chat %s: billing mode %q, want managed", name, got)
		}
	}

	w := postMessages(t, g.srv, "/v1/messages", managedOrgKey, managedMessagesBody("claude-sonnet-5-5-20260928", false, ""))
	if w.Code != http.StatusOK || g.managed.lastModel() != "anthropic/claude-sonnet-5.5" {
		t.Errorf("/v1/messages with a dated Claude id: status %d, upstream model %q; body %s", w.Code, g.managed.lastModel(), w.Body)
	}
}

// TestManagedExactCatalogueIDWins: a name that is itself a catalogue id is
// sent as it is, never re-read as another id.
func TestManagedExactCatalogueIDWins(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	g.ledger.SetMicros("org-m", auth.USDToMicros(5))
	withCatalogModels(t, g, "gpt-4o", "openai/gpt-4o")

	if w := g.chat(t, chatBody("gpt-4o"), nil); w.Code != http.StatusOK || g.managed.lastModel() != "gpt-4o" {
		t.Fatalf("status %d, upstream model %q, want the exact catalogue id gpt-4o", w.Code, g.managed.lastModel())
	}
	if w := g.chat(t, chatBody("openai/gpt-4o"), nil); w.Code != http.StatusOK || g.managed.lastModel() != "openai/gpt-4o" {
		t.Fatalf("status %d, upstream model %q, want openai/gpt-4o", w.Code, g.managed.lastModel())
	}
}

// TestManagedAmbiguousNameRefused: a bare name more than one catalogue id
// ends in is refused with a 400 naming every candidate, and no upstream is
// called.
func TestManagedAmbiguousNameRefused(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	g.ledger.SetMicros("org-m", auth.USDToMicros(5))
	withCatalogModels(t, g, "mistralai/foo-7b", "nousresearch/foo-7b")

	w := g.chat(t, chatBody("foo-7b"), nil)

	var body struct {
		Error struct {
			Code    string `json:"code"`
			Message string `json:"message"`
		} `json:"error"`
	}
	_ = json.Unmarshal(w.Body.Bytes(), &body)
	if w.Code != http.StatusBadRequest || body.Error.Code != "model_ambiguous" {
		t.Fatalf("status %d body %s, want 400 model_ambiguous", w.Code, w.Body)
	}
	for _, id := range []string{"mistralai/foo-7b", "nousresearch/foo-7b"} {
		if !strings.Contains(body.Error.Message, id) {
			t.Errorf("the error does not name the candidate %s: %s", id, body.Error.Message)
		}
	}
	if g.managed.calls() != 0 {
		t.Fatal("an ambiguous name reached the managed upstream")
	}

	m := postMessages(t, g.srv, "/v1/messages", managedOrgKey, managedMessagesBody("foo-7b", false, ""))
	if m.Code != http.StatusBadRequest || !strings.Contains(m.Body.String(), "nousresearch/foo-7b") {
		t.Errorf("/v1/messages: status %d body %s, want 400 naming the candidates", m.Code, m.Body)
	}
	if w := g.chat(t, chatBody("mistralai/foo-7b"), nil); w.Code != http.StatusOK || g.managed.lastModel() != "mistralai/foo-7b" {
		t.Errorf("a full id must still resolve: status %d, upstream %q", w.Code, g.managed.lastModel())
	}
}

// TestManagedPlainNamesNeedTheOrgToOptIn: a plain name resolves only for an
// org that has turned Future AGI models on.
func TestManagedPlainNamesNeedTheOrgToOptIn(t *testing.T) {
	g := newManagedTestGateway(t, &tenant.OrgConfig{})
	g.ledger.SetMicros("org-m", auth.USDToMicros(5))
	withCatalogModels(t, g, "openai/gpt-4o")

	if w := g.chat(t, chatBody("gpt-4o"), nil); w.Code != http.StatusForbidden || g.managed.calls() != 0 {
		t.Fatalf("status %d, managed calls %d; an org that has not opted in must not reach the managed provider", w.Code, g.managed.calls())
	}
}

// TestManagedVariantNameNeedsItsExactID: a name carrying an OpenRouter
// variant suffix (":online", ":free") is another price or behaviour of a
// model, so it is served only when it is itself a catalogue id. With the
// catalogue listing openai/gpt-4o and none of its variants, each variant name
// is refused as not available, on chat and on /v1/messages, and no upstream
// is called; a variant id the catalogue lists is served as it is.
func TestManagedVariantNameNeedsItsExactID(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	g.ledger.SetMicros("org-m", auth.USDToMicros(5))
	withCatalogModels(t, g, "openai/gpt-4o")

	for _, name := range []string{"openai/gpt-4o:online", "openai/gpt-4o:free", "gpt-4o:free"} {
		// An unlisted variant is an id nothing lists: 404 model_not_found
		// since DX-ERRORS-1.
		w := g.chat(t, chatBody(name), nil)
		if w.Code != http.StatusNotFound || !strings.Contains(w.Body.String(), name+`\" was not found`) {
			t.Errorf("chat %s: status %d body %s, want 404 naming the model as not found", name, w.Code, w.Body)
		}
		m := postMessages(t, g.srv, "/v1/messages", managedOrgKey, managedMessagesBody(name, false, ""))
		if m.Code != http.StatusNotFound || !strings.Contains(m.Body.String(), name+`\" was not found`) {
			t.Errorf("/v1/messages %s: status %d body %s, want 404 naming the model as not found", name, m.Code, m.Body)
		}
	}
	if n := g.managed.calls(); n != 0 {
		t.Fatalf("%d variant names reached the managed upstream as another model (last %q)", n, g.managed.lastModel())
	}

	if w := g.chat(t, chatBody("qwen/qwen3.7-flash:free"), nil); w.Code != http.StatusOK || g.managed.lastModel() != "qwen/qwen3.7-flash:free" {
		t.Fatalf("an exact variant id: status %d, upstream model %q, want 200 on qwen/qwen3.7-flash:free; body %s", w.Code, g.managed.lastModel(), w.Body)
	}
}
