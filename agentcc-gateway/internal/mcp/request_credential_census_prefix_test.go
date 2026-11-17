package mcp

import (
	"maps"
	"strings"
	"testing"
)

// TestK2R7DeclaredPrefixAddsNoSender: a catalog key sent after the prefix
// its record declares goes out through the credential template's round
// tripper (the pool) and the probe's own requests (the listing at add), so
// the census entries for both stay exactly what they were, and the module
// still counts to the census. Spelling the Authorization header out at
// either site would be a new sender, and the census would refuse it.
func TestK2R7DeclaredPrefixAddsNoSender(t *testing.T) {
	allowed := r7AllowedDispatch(t)
	want := map[string]int{
		"internal/mcp/probe.go|*prober.endSession|Do":                              1,
		"internal/mcp/probe.go|*prober.httpSSE|Do":                                 1,
		"internal/mcp/probe.go|*prober.send|Do":                                    1,
		"internal/mcp/credtemplate/roundtrip.go|*roundTripper.RoundTrip|RoundTrip": 2,
	}
	got := map[string]int{}
	for key, site := range allowed {
		if strings.HasPrefix(key, "internal/mcp/probe.go|") || strings.HasPrefix(key, "internal/mcp/credtemplate/") {
			got[key] = site.Count
		}
	}
	if !maps.Equal(got, want) {
		t.Fatalf("census entries for the probe and the credential template = %v, want %v", got, want)
	}

	base := r7ModuleSources(t)
	if problems := r7CustodyProblems(base, allowed); len(problems) != 0 {
		t.Fatalf("the module does not count to the census: %v", problems)
	}
	for _, tc := range []struct{ path, old, replacement, want string }{
		{
			path: "internal/mcp/credtemplate/roundtrip.go", old: "out.Header.Set(h.Name, h.Prefix+h.Value)",
			replacement: `out.Header.Set(h.Name, h.Prefix+h.Value)
		if h.Name == "Authorization" {
			out.Header.Set("Authorization", h.Prefix+h.Value)
		}`,
			want: "dispatch census internal/mcp/credtemplate/roundtrip.go|*roundTripper.RoundTrip|AuthorizationSet",
		},
		{
			path: "internal/mcp/probe.go", old: "h.Set(a.headerName(), v)",
			replacement: `h.Set(a.headerName(), v)
	if a.headerName() == "Authorization" {
		h.Set("Authorization", v)
	}`,
			want: "dispatch census internal/mcp/probe.go|ProbeAuth.header|AuthorizationSet",
		},
	} {
		sources := maps.Clone(base)
		if strings.Count(sources[tc.path], tc.old) != 1 {
			t.Fatalf("%s: the anchor is not there exactly once", tc.path)
		}
		sources[tc.path] = strings.Replace(sources[tc.path], tc.old, tc.replacement, 1)
		problems := r7CustodyProblems(sources, allowed)
		found := false
		for _, problem := range problems {
			if strings.HasPrefix(problem, "parse ") {
				t.Fatalf("%s: the mutation does not parse: %s", tc.path, problem)
			}
			found = found || strings.Contains(problem, tc.want)
		}
		if !found {
			t.Errorf("%s: an Authorization header spelled out was not counted as a new sender: %v", tc.path, problems)
		}
	}
}
