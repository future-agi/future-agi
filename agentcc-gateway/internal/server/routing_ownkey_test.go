package server

import (
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// An org's model_map, routing, failover and breakers for customer keys
// (orgRouteFor, the org failover) compose with sending an org's own
// provider the id it registered, not "<provider>/<id>" (orgModelFor). These
// pin that each holds with the other in place, on the gateway's own HTTP
// handler with the org's own key.

// A model_map entry that names the org's own provider for a provider-qualified
// id: the map picks the provider, and its host is sent the id it registered.
func TestModelMapRouteSendsTheRegisteredID(t *testing.T) {
	g := newOKGateway(t, func(_ *config.Config, org *tenant.OrgConfig) {
		org.ModelMap = map[string]string{"openai/gpt-4o-mini": "openai"}
	})
	ct, body := okJSON(okChat)(t, "openai/gpt-4o-mini")
	w := g.send(t, "/v1/chat/completions", ct, body)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d %s", w.Code, w.Body.String())
	}
	if seen, _ := g.hosts["openai"].calls(); len(seen) != 1 || seen[0] != "/v1/chat/completions gpt-4o-mini" {
		t.Fatalf("openai received %q, want one chat call naming gpt-4o-mini", seen)
	}
}

// A customer key whose org turned retry on runs over the org failover
// (customerFailover); a provider-qualified id reaches the host as the id the
// org registered, once.
func TestOrgFailoverSendsTheRegisteredID(t *testing.T) {
	g := newOKGateway(t, func(_ *config.Config, org *tenant.OrgConfig) {
		org.Routing = &tenant.RoutingConfig{Retry: &tenant.RetryConfig{Enabled: true, MaxRetries: 1}}
	})
	for _, c := range []struct{ model, host, want string }{
		{"openai/gpt-4o-mini", "openai", "gpt-4o-mini"},
		{"together/" + okTogether, "together", okTogether},
	} {
		ct, body := okJSON(okChat)(t, c.model)
		w := g.send(t, "/v1/chat/completions", ct, body)
		if w.Code != http.StatusOK {
			t.Fatalf("%s: status = %d %s", c.model, w.Code, w.Body.String())
		}
		if got := w.Header().Get("x-agentcc-provider"); got != "" && got != c.host {
			t.Errorf("%s: x-agentcc-provider = %q, want %s", c.model, got, c.host)
		}
		if seen, _ := g.hosts[c.host].calls(); len(seen) != 1 || seen[0] != "/v1/chat/completions "+c.want {
			t.Fatalf("%s: %s received %q, want one chat call naming %s", c.model, c.host, seen, c.want)
		}
	}
}

// A model_map alias of a one-model provider is sent that model on chat; a
// route that does not apply the map (embeddings) never takes the alias to
// that provider (the resolveOrgProvider rule).
func TestModelMapAliasRoutesChatOnly(t *testing.T) {
	g := newOKGateway(t, func(_ *config.Config, org *tenant.OrgConfig) {
		org.ModelMap = map[string]string{"fast": "together", "ocr-alias": "mistral"}
	})
	ct, body := okJSON(okChat)(t, "fast")
	if w := g.send(t, "/v1/chat/completions", ct, body); w.Code != http.StatusOK {
		t.Fatalf("chat alias: status = %d %s", w.Code, w.Body.String())
	}
	if seen, _ := g.hosts["together"].calls(); len(seen) != 1 || seen[0] != "/v1/chat/completions "+okTogether {
		t.Fatalf("together received %q, want one chat call naming %s", seen, okTogether)
	}
	ct, body = okJSON(`{"model":"@MODEL@","input":"hi"}`)(t, "ocr-alias")
	if w := g.send(t, "/v1/embeddings", ct, body); w.Code == http.StatusOK {
		t.Fatalf("embeddings with a model_map alias answered 200 %s; the route does not apply the map", w.Body.String())
	}
	ct, body = okJSON(`{"model":"@MODEL@","input":"hi","store":false}`)(t, "ocr-alias")
	if w := g.send(t, "/v1/responses", ct, body); w.Code == http.StatusOK {
		t.Fatalf("responses with a model_map alias answered 200 %s; the route does not apply the map", w.Body.String())
	}
	if seen, _ := g.hosts["mistral"].calls(); len(seen) != 0 {
		t.Fatalf("mistral received %q; an alias is not taken on a route that does not apply the map", seen)
	}
}
