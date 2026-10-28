package guardrails

import "testing"

// A request that resolves an org's guardrail just before its config changes must
// not publish the old implementation after the cache was invalidated: later
// requests would keep running the replaced config.
func TestDynamicCacheDropsImplementationsBuiltBeforeInvalidation(t *testing.T) {
	building := make(chan struct{})
	release := make(chan struct{})
	stale := &responseWordCheck{name: "external", stage: StagePost}
	factory := func(name string, cfg map[string]interface{}) Guardrail {
		if cfg["version"] == "old" {
			close(building)
			<-release
			return stale
		}
		return &responseWordCheck{name: name, stage: StagePost}
	}
	plugin := NewPlugin(nil, nil, factory, nil, nil)

	done := make(chan Guardrail)
	go func() {
		done <- plugin.createDynamicGuardrail("org-a", "external", map[string]interface{}{"version": "old"})
	}()
	<-building
	plugin.InvalidateDynamicCache("org-a")
	close(release)
	if got := <-done; got != stale {
		t.Fatalf("in-flight request should still use what it built, got %v", got)
	}
	if cached := plugin.getCachedDynamicGuardrail("org-a", "external"); cached != nil {
		t.Fatal("implementation built before invalidation was published to the cache")
	}

	fresh := plugin.createDynamicGuardrail("org-a", "external", map[string]interface{}{"version": "new"})
	if cached := plugin.getCachedDynamicGuardrail("org-a", "external"); cached == nil || cached != fresh {
		t.Fatal("implementation built after invalidation was not cached")
	}
	// Other orgs are unaffected by this org's invalidation.
	other := plugin.createDynamicGuardrail("org-b", "external", map[string]interface{}{"version": "new"})
	if plugin.getCachedDynamicGuardrail("org-b", "external") != other {
		t.Fatal("unrelated org cache entry missing")
	}
}
