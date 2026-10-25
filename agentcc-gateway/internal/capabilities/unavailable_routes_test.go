package capabilities

import "testing"

// No provider adapter that ships with this release serves
// search, video or realtime, so /v1/search, /v1/videos and /v1/realtime answer
// 501 whatever provider a request reaches. One provider per built-in
// api_format (builtinConfig), each built as the gateway builds it.
func TestNoShippedAdapterServesSearchVideoOrRealtime(t *testing.T) {
	ps := builtinProviders(t)
	if len(ps) == 0 {
		t.Fatal("no built-in provider was built")
	}
	formats := map[string]bool{}
	for _, p := range ps {
		formats[p.APIFormat] = true
		for _, iface := range []Interface{IfaceSearch, IfaceVideo, IfaceRealtime} {
			if p.Interfaces[iface] {
				t.Errorf("provider %s (api_format %s) implements %s: the README says no shipped adapter does", p.ID, p.APIFormat, iface)
			}
		}
	}
	for _, f := range []string{"openai", "anthropic", "gemini", "bedrock", "cohere", "azure"} {
		if !formats[f] {
			t.Errorf("api_format %s was not checked (providers.NewRegistry builds it)", f)
		}
	}
}
