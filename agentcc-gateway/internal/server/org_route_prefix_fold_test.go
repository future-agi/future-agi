package server

import (
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// A "<provider>/<model>" id reaches the org's own provider only when the
// prefix equals the provider id under strings.EqualFold (Unicode simple case
// folding, rune by rune), and the model part matches exactly. A provider id
// that only full case folding would equate (a ligature, sharp s) or that has
// no simple fold to ASCII (dotless i, dotted capital I) does not take the
// id: no org route, so the call is served managed when credits are on and
// the catalog prices the id (the managed payer row), never on the org's
// key. These rows pin the rule the console's model list mirrors.
func TestOrgPrefixProviderFoldsLikeEqualFold(t *testing.T) {
	rows := []struct {
		name     string
		provider string
		model    string
		byok     bool
	}{
		{"ascii case", "FireWorks", "fireworks/m1", true},
		{"fi ligature", "ﬁreworks", "fireworks/m1", false},
		{"sharp s", "groß", "gross/m1", false},
		{"kelvin sign", "Kimi", "kimi/m1", true},
		{"long s", "ſambanova", "sambanova/m1", true},
		{"dotless i", "ınfra", "infra/m1", false},
		{"dotted capital I", "İnfra", "infra/m1", false},
	}
	base := map[string]config.ProviderConfig{}
	for _, r := range rows {
		base[r.provider] = config.ProviderConfig{BaseURL: "http://127.0.0.1:1", APIKey: "operator-key", APIFormat: "openai"}
	}
	h := &Handlers{orgProviderCache: providers.NewOrgProviderCache(base, false)}
	for _, r := range rows {
		t.Run(r.name, func(t *testing.T) {
			prefix := r.model[:strings.IndexByte(r.model, '/')]
			if got := strings.EqualFold(r.provider, prefix); got != r.byok {
				t.Fatalf("strings.EqualFold(%q, %q) = %t, want %t", r.provider, prefix, got, r.byok)
			}
			org := orgRouteConfig(map[string][]string{r.provider: {"m1"}})
			p, id, upstream, _, err := h.orgRouteFor("org-route", org, r.model)
			if err != nil {
				t.Fatalf("orgRouteFor(%q) error %v", r.model, err)
			}
			want := ""
			if r.byok {
				want = r.provider
				if p == nil || id != r.provider || upstream != "m1" {
					t.Fatalf("orgRouteFor(%q) = %v, %q sent as %q; want the org's %q sent as m1 (byok)", r.model, p, id, upstream, r.provider)
				}
			} else if p != nil || id != "" {
				t.Fatalf("orgRouteFor(%q) = %v, %q; want no org route (managed)", r.model, p, id)
			}
			if got := orgProviderServing(org, r.model); got != want {
				t.Fatalf("orgProviderServing(%q) = %q, want %q", r.model, got, want)
			}
			// The model part is exact: another case of m1 is not the org's.
			upper := prefix + "/M1"
			if p, id, _, _, _ := h.orgRouteFor("org-route", org, upper); p != nil || id != "" {
				t.Fatalf("orgRouteFor(%q) = %q; want no org route (the model part is exact)", upper, id)
			}
		})
	}
}
