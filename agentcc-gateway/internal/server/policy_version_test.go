package server

import (
	"net/http"
	"strconv"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// policyGateway is the managed test gateway with a generation recorder, so a
// test reads each request's routing evidence after the request finished.
type policyGateway struct {
	*managedTestGateway
	gens chan *models.TraceSnapshot
}

func newPolicyGateway(t *testing.T, orgCfg *tenant.OrgConfig) *policyGateway {
	t.Helper()
	g := &policyGateway{managedTestGateway: newManagedTestGateway(t, orgCfg), gens: make(chan *models.TraceSnapshot, 64)}
	g.ledger.SetMicros("org-m", auth.USDToMicros(100))
	g.srv.handlers.SetGenerationRecorder(func(snap *models.TraceSnapshot) { g.gens <- snap })
	return g
}

// call sends one auto request and returns its evidence and the model it chose.
func (g *policyGateway) call(t *testing.T, headers map[string]string) (models.RoutingEvidence, string) {
	t.Helper()
	w := g.chat(t, chatBody("auto"), headers)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d; body %s", w.Code, w.Body)
	}
	snap := <-g.gens
	return snap.Evidence, g.managed.lastModel()
}

// qualityStableCostCandidate is an opted-in org at config version 7 whose
// stable policy prefers quality and whose rollout candidate (v8) prefers cost.
func qualityStableCostCandidate(percent int, stickyBy string) *tenant.OrgConfig {
	cfg := managedOptIn()
	cfg.Meta = &tenant.ConfigMeta{Version: 7}
	cfg.Routing = &tenant.RoutingConfig{
		Auto: &tenant.AutoRoutingConfig{Preference: "quality"},
		Rollout: &tenant.RolloutConfig{
			Version: "v8", Percent: percent, StickyBy: stickyBy,
			Routing: &tenant.RoutingConfig{Auto: &tenant.AutoRoutingConfig{Preference: "cost"}},
		},
	}
	return cfg
}

// TestPolicyVersionStamped: every chat request carries the policy version
// and variant that decided it, and a candidate request is routed by the
// candidate's policy, not stable's.
func TestPolicyVersionStamped(t *testing.T) {
	t.Run("versioned config without a rollout is stable at v<meta.version>", func(t *testing.T) {
		cfg := managedOptIn()
		cfg.Meta = &tenant.ConfigMeta{Version: 7}
		ev, _ := newPolicyGateway(t, cfg).call(t, nil)
		if ev.PolicyVersion != "v7" || ev.PolicyVariant != tenant.PolicyVariantStable {
			t.Errorf("evidence = %q/%q, want v7/stable", ev.PolicyVersion, ev.PolicyVariant)
		}
	})
	t.Run("config without meta is unversioned", func(t *testing.T) {
		ev, _ := newPolicyGateway(t, managedOptIn()).call(t, nil)
		if ev.PolicyVersion != tenant.PolicyUnversioned || ev.PolicyVariant != tenant.PolicyVariantStable {
			t.Errorf("evidence = %q/%q, want unversioned/stable", ev.PolicyVersion, ev.PolicyVariant)
		}
	})
	t.Run("rollout at 0 percent keeps stable's version and policy", func(t *testing.T) {
		ev, model := newPolicyGateway(t, qualityStableCostCandidate(0, "request")).call(t, nil)
		if ev.PolicyVersion != "v7" || ev.PolicyVariant != tenant.PolicyVariantStable {
			t.Errorf("evidence = %q/%q, want v7/stable", ev.PolicyVersion, ev.PolicyVariant)
		}
		if model == "qwen/qwen3.7-flash" {
			t.Errorf("stable (quality) chose the economical %q", model)
		}
	})
	t.Run("rollout at 100 percent runs the candidate's version and policy", func(t *testing.T) {
		g := newPolicyGateway(t, qualityStableCostCandidate(100, "request"))
		ev, model := g.call(t, nil)
		if ev.PolicyVersion != "v8" || ev.PolicyVariant != tenant.PolicyVariantCandidate {
			t.Errorf("evidence = %q/%q, want v8/candidate", ev.PolicyVersion, ev.PolicyVariant)
		}
		if model != "qwen/qwen3.7-flash" {
			t.Errorf("candidate (cost) chose %q, want the economical qwen/qwen3.7-flash", model)
		}
		// The stored config is unchanged: the next lookup still holds the rollout.
		if got := g.tenants.Get("org-m"); got.Rollout() == nil || got.Routing.Auto.Preference != "quality" {
			t.Error("bucketing a request changed the stored org config")
		}
	})
	t.Run("a request with no org config is unversioned and stable", func(t *testing.T) {
		h := &Handlers{}
		rc := models.AcquireRequestContext()
		defer rc.Release()
		if got := h.effectiveOrgConfig(rc, nil); got != nil {
			t.Errorf("effectiveOrgConfig(nil) = %+v", got)
		}
		if rc.Evidence.PolicyVersion != tenant.PolicyUnversioned || rc.Evidence.PolicyVariant != tenant.PolicyVariantStable {
			t.Errorf("evidence = %+v", rc.Evidence)
		}
	})
}

// TestPolicyVersionStampedCandidateIsOneConfig: every candidate request on
// one stored config runs under the same candidate config and routing struct,
// so per-org routing state cached on them (circuit breakers, adaptive
// weights, round-robin position) is shared across candidate requests the way
// it is for stable; a push gets its own candidate.
func TestPolicyVersionStampedCandidateIsOneConfig(t *testing.T) {
	cfg := qualityStableCostCandidate(50, "request")
	h := newPolicyGateway(t, cfg).srv.handlers
	effective := func(requestID string) *tenant.OrgConfig {
		rc := models.AcquireRequestContext()
		defer rc.Release()
		rc.RequestID = requestID
		rc.Metadata["org_id"] = "org-m"
		return h.effectiveOrgConfig(rc, cfg)
	}
	var cand *tenant.OrgConfig
	candidates, stables := 0, 0
	for i := 0; i < 40; i++ {
		id := "req-" + strconv.Itoa(i)
		got := effective(id)
		if tenant.RolloutBucket("org-m", id) >= 50 {
			stables++
			if got != cfg {
				t.Fatalf("stable request %s ran under %p, want the stored config %p", id, got, cfg)
			}
			continue
		}
		candidates++
		if cand == nil {
			cand = got
		}
		if got != cand || got.Routing != cand.Routing {
			t.Fatalf("candidate request %s ran under config %p routing %p, want %p routing %p", id, got, got.Routing, cand, cand.Routing)
		}
	}
	if candidates == 0 || stables == 0 {
		t.Fatalf("40 request ids gave %d candidate and %d stable requests, want both", candidates, stables)
	}
	if cand == cfg || cand.Routing.Auto.Preference != "cost" {
		t.Fatalf("the candidate config = %+v, want the rollout's routing", cand.Routing)
	}

	pushed := qualityStableCostCandidate(100, "request")
	rc := models.AcquireRequestContext()
	defer rc.Release()
	if got := h.effectiveOrgConfig(rc, pushed); got == cand || got.Routing == cand.Routing {
		t.Error("a pushed config ran under the previous config's candidate")
	}

	// Concurrent candidate requests share that one config: run them
	// through the whole chat path so -race sees any request-time write into
	// it.
	shared := qualityStableCostCandidate(100, "request")
	shared.Routing.Rollout.Routing.ModelTimeouts = map[string]string{"qwen/qwen3.7-flash": "20s"}
	shared.Routing.Rollout.Routing.Presets = map[string]*tenant.RoutingPreset{"fast": {Model: "qwen/qwen3.7-flash"}}
	g := newPolicyGateway(t, shared)
	const n = 32
	var wg sync.WaitGroup
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if w := g.chat(t, chatBody("auto"), nil); w.Code != http.StatusOK {
				t.Errorf("concurrent candidate request: status %d; body %s", w.Code, w.Body)
			}
		}()
	}
	wg.Wait()
	for i := 0; i < n; i++ {
		if ev := (<-g.gens).Evidence; ev.PolicyVariant != tenant.PolicyVariantCandidate || ev.PolicyVersion != "v8" {
			t.Errorf("concurrent request evidence = %q/%q, want v8/candidate", ev.PolicyVersion, ev.PolicyVariant)
		}
	}
}

// TestRolloutStickySessionThroughGateway: with sticky_by=session, the session
// the parse step resolves from x-agentcc-session-id decides the variant, the
// same for every request of the session; an unversioned rollout is stamped
// unversioned.
func TestRolloutStickySessionThroughGateway(t *testing.T) {
	g := newPolicyGateway(t, qualityStableCostCandidate(50, "session"))
	seen := map[string]bool{}
	for s := 0; s < 12; s++ {
		session := "sess-" + strconv.Itoa(s)
		want := tenant.PolicyVariantStable
		if tenant.RolloutBucket("org-m", session) < 50 {
			want = tenant.PolicyVariantCandidate
		}
		seen[want] = true
		for i := 0; i < 3; i++ {
			ev, model := g.call(t, map[string]string{"x-agentcc-session-id": session})
			if ev.PolicyVariant != want {
				t.Fatalf("session %s request %d: variant %q, want %q (bucket %d)", session, i, ev.PolicyVariant, want, tenant.RolloutBucket("org-m", session))
			}
			if wantVersion := map[string]string{tenant.PolicyVariantStable: "v7", tenant.PolicyVariantCandidate: "v8"}[want]; ev.PolicyVersion != wantVersion {
				t.Errorf("session %s: version %q, want %q", session, ev.PolicyVersion, wantVersion)
			}
			if economical := model == "qwen/qwen3.7-flash"; economical != (want == tenant.PolicyVariantCandidate) {
				t.Errorf("session %s (%s) chose %q", session, want, model)
			}
		}
	}
	if !seen[tenant.PolicyVariantStable] || !seen[tenant.PolicyVariantCandidate] {
		t.Fatalf("12 sessions did not cover both variants: %v", seen)
	}

	unversioned := qualityStableCostCandidate(100, "session")
	unversioned.Routing.Rollout.Version = ""
	g.tenants.Set("org-m", unversioned)
	if ev, _ := g.call(t, map[string]string{"x-agentcc-session-id": "sess-0"}); ev.PolicyVersion != tenant.PolicyUnversioned {
		t.Errorf("a rollout without a version stamped %q", ev.PolicyVersion)
	}
}

// TestRolloutStickySessionFallsBackToKey: with no session or run id the
// session chain reaches the key id, which the hook finds by a read-only
// lookup because the auth plugin has not run at step 4; sticky_by=key uses
// the key even when a session is present; sticky_by=request ignores both.
func TestRolloutStickySessionFallsBackToKey(t *testing.T) {
	g := newPolicyGateway(t, managedOptIn())
	key := g.srv.handlers.keyStore.Lookup(managedOrgKey)
	if key == nil {
		t.Fatal("test key not loaded")
	}
	rollout := &tenant.RolloutConfig{Version: "v2", Percent: 100}
	h := g.srv.handlers
	ids := func(by string, session string) tenant.StickyIDs {
		rc := models.AcquireRequestContext()
		defer rc.Release()
		rc.RequestID = "req-1"
		rc.Agent.SessionID = session
		rc.Metadata["authorization"] = "Bearer " + managedOrgKey
		rollout.StickyBy = by
		return h.stickyIDs(rc, rollout)
	}
	if got := ids("session", ""); got.KeyID != key.ID || rollout.StickyKey(got) != key.ID {
		t.Errorf("session chain without a session = %+v, want the key id %q", got, key.ID)
	}
	if got := ids("session", "s1"); rollout.StickyKey(got) != "s1" || got.KeyID != "" {
		t.Errorf("session chain with a session = %+v; the key lookup must be skipped", got)
	}
	if got := ids("key", "s1"); rollout.StickyKey(got) != key.ID {
		t.Errorf("sticky_by=key = %+v, want the key id %q", got, key.ID)
	}
	if got := ids("request", "s1"); rollout.StickyKey(got) != "req-1" || got.KeyID != "" {
		t.Errorf("sticky_by=request = %+v, want the request id only", got)
	}
}
