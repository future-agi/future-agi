package cache

import (
	"context"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func keyPolicyStore(mode string) (*tenant.Store, *tenant.KeyGuardrailPolicy) {
	pol := &tenant.KeyGuardrailPolicy{ID: "pol", Name: "pol", Mode: mode, KeyIDs: []string{"key_a"},
		Checks: map[string]*tenant.GuardrailCheck{"secret-detection": {Enabled: true, Action: "block"}}}
	store := tenant.NewStore()
	store.Set("org", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{pol}}})
	return store, pol
}

func keyRequest(key, text string) *models.RequestContext {
	rc := models.AcquireRequestContext()
	rc.Metadata["org_id"] = "org"
	rc.Metadata["auth_key_id"] = key
	rc.Model = "gpt-4o"
	rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o", Messages: []models.Message{textMsg("user", text)}}
	return rc
}

// A semantic hit answers a similar, different prompt: the request's own text
// never reaches the pre-stage checks. Keys with key policies get exact hits only.
func TestKeyPolicyRequestsSkipTheSemanticCache(t *testing.T) {
	p, _, _ := newTestSemanticPlugin()
	store, _ := keyPolicyStore("enforce")
	p.SetTenantStore(store)

	rc1 := keyRequest("key_a", "deploy with akiaiosfodnn7example please")
	p.ProcessRequest(context.Background(), rc1)
	rc1.Response = makeResp("first")
	p.ProcessResponse(context.Background(), rc1)
	rc1.Release()

	rc2 := keyRequest("key_a", "deploy with AKIAIOSFODNN7EXAMPLE please")
	defer rc2.Release()
	p.ProcessRequest(context.Background(), rc2)
	if rc2.Flags.CacheHit {
		t.Fatalf("key with a key policy got a %s cache hit for a different prompt", rc2.Metadata["cache_status"])
	}

	// Control: a key without key policies still gets semantic hits.
	rc3 := keyRequest("key_b", "deploy with akiaiosfodnn7example please")
	p.ProcessRequest(context.Background(), rc3)
	rc3.Response = makeResp("third")
	p.ProcessResponse(context.Background(), rc3)
	rc3.Release()
	rc4 := keyRequest("key_b", "deploy with AKIAIOSFODNN7EXAMPLE please")
	defer rc4.Release()
	p.ProcessRequest(context.Background(), rc4)
	if rc4.Metadata["cache_status"] != "hit_semantic" {
		t.Fatalf("control: cache_status %q; want hit_semantic", rc4.Metadata["cache_status"])
	}
}

// When the key's policies change while a request runs, the guardrails may
// have judged it under other policies than its cache key names: it is not
// stored there.
func TestResponseNotStoredWhenKeyPoliciesChangedMidRequest(t *testing.T) {
	p, mem, _ := newTestSemanticPlugin()
	store, pol := keyPolicyStore("enforce")
	p.SetTenantStore(store)

	rc := keyRequest("key_a", "hello")
	defer rc.Release()
	p.ProcessRequest(context.Background(), rc)
	key := rc.Metadata["cache_key"]
	pol.Mode = "monitor" // the control plane switched it before guardrails ran
	rc.Response = makeResp("judged-in-monitor")
	p.ProcessResponse(context.Background(), rc)
	if _, ok := mem.Get(key); ok {
		t.Fatal("a response judged under Monitor was stored under the Enforce cache key")
	}

	// Control: unchanged policies store as usual.
	rc2 := keyRequest("key_a", "hello again")
	defer rc2.Release()
	p.ProcessRequest(context.Background(), rc2)
	rc2.Response = makeResp("stored")
	p.ProcessResponse(context.Background(), rc2)
	if _, ok := mem.Get(rc2.Metadata["cache_key"]); !ok {
		t.Fatal("control: response not stored")
	}
}
