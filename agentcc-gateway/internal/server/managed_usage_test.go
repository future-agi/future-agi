package server

import (
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/routing/reqopts"
)

// TestManagedUsageIncludeAccepted: a managed
// request may send usage: {include: true}, the gateway's own outbound value,
// and the upstream receives the gateway-built usage.include=true. Any other
// usage value stays 400 unsupported_field_for_managed, so no caller setting
// silently does nothing.
func TestManagedUsageIncludeAccepted(t *testing.T) {
	g := newRoutingGateway(t, managedOrg())
	w := g.chat(t, body(managedModel, `"usage":{"include":true}`))
	if w.Code != http.StatusOK {
		t.Fatalf("usage {include:true}: status %d body %s", w.Code, w.Body)
	}
	usage, _ := lastBody(t, g.openrouter)["usage"].(map[string]any)
	if include, _ := usage["include"].(bool); !include || len(usage) != 1 {
		t.Fatalf("upstream usage = %v, want {include: true}", lastBody(t, g.openrouter)["usage"])
	}
	for _, fragment := range []string{`"usage":{"include":false}`, `"usage":{"include":true,"extra":1}`, `"usage":"yes"`} {
		before := len(g.openrouter.received())
		expect400(t, g.chat(t, body(managedModel, fragment)), reqopts.CodeUnsupportedFieldForManaged, "usage")
		if len(g.openrouter.received()) != before {
			t.Fatalf("%s: a refused managed request reached the upstream", fragment)
		}
	}
}
