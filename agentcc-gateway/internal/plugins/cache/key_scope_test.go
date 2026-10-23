package cache

import (
	"regexp"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/cache"
)

// Qdrant takes the cache key as a point id, and it only accepts an unsigned
// integer or a UUID there; 32 hex digits is the simple UUID form.
var cacheKeyShape = regexp.MustCompile(`^[0-9a-f]{32}$`)

func TestBuildCacheKey_ScopeIsPartOfTheKey(t *testing.T) {
	req := makeReq("gpt-4o", "what is the capital of France")
	scopes := []string{cache.NoOrgScope, cache.OrgScope(orgA), cache.OrgScope(orgB)}

	keys := map[string]string{}
	for _, s := range scopes {
		k := BuildCacheKey(s, "default", req)
		if !cacheKeyShape.MatchString(k) {
			t.Errorf("BuildCacheKey(%q) = %q, want 32 lowercase hex", s, k)
		}
		if other, dup := keys[k]; dup {
			t.Errorf("scopes %q and %q share key %s", s, other, k)
		}
		keys[k] = s
	}
}

// The namespace is caller input and is hashed after the scope, so no
// namespace can rebuild another scope's key.
func TestBuildCacheKey_NamespaceCannotMatchAnotherScope(t *testing.T) {
	req := makeReq("gpt-4o", "hello")
	victim := BuildCacheKey(cache.OrgScope(orgA), "default", req)

	for _, ns := range []string{
		"default",
		cache.OrgScope(orgA),
		cache.OrgScope(orgA) + "|default",
		"|" + cache.OrgScope(orgA) + "|default",
		"v2|" + cache.OrgScope(orgA) + "|default",
	} {
		for _, attacker := range []string{cache.NoOrgScope, cache.OrgScope(orgB)} {
			if BuildCacheKey(attacker, ns, req) == victim {
				t.Errorf("scope %q with namespace %q rebuilt org A's key", attacker, ns)
			}
		}
	}
}
