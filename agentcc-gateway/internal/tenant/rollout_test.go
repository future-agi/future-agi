package tenant

import (
	"encoding/json"
	"hash/fnv"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"strconv"
	"sync"
	"testing"
	"time"
	"weak"
)

// TestRolloutDistribution: 10,000 distinct ids at 10% put 9-11% on the
// candidate, and the bucket of an id never changes.
func TestRolloutDistribution(t *testing.T) {
	r := &RolloutConfig{Percent: 10, StickyBy: "request"}
	candidate := 0
	for i := 0; i < 10000; i++ {
		id := "req-" + strconv.Itoa(i)
		if r.InCandidate("org-dist", id) {
			candidate++
		}
	}
	if candidate < 900 || candidate > 1100 {
		t.Fatalf("10%% rollout put %d of 10000 ids on the candidate, want 900-1100", candidate)
	}

	for i := 0; i < 100; i++ {
		id := "req-" + strconv.Itoa(i)
		b := RolloutBucket("org-dist", id)
		if b < 0 || b > 99 {
			t.Fatalf("bucket(%q) = %d, outside 0-99", id, b)
		}
		if again := RolloutBucket("org-dist", id); again != b {
			t.Fatalf("bucket(%q) changed from %d to %d", id, b, again)
		}
	}
	// The org is part of the hash: the same ids split differently per org.
	same := 0
	for i := 0; i < 1000; i++ {
		id := "req-" + strconv.Itoa(i)
		if RolloutBucket("org-a", id) == RolloutBucket("org-b", id) {
			same++
		}
	}
	if same > 50 {
		t.Errorf("%d of 1000 ids share a bucket across two orgs; the org is not hashed", same)
	}
}

func TestRolloutDistributionEdges(t *testing.T) {
	for _, tc := range []struct {
		percent int
		want    int
	}{{0, 0}, {-5, 0}, {100, 1000}, {150, 1000}} {
		r := &RolloutConfig{Percent: tc.percent}
		got := 0
		for i := 0; i < 1000; i++ {
			if r.InCandidate("org-edge", "id-"+strconv.Itoa(i)) {
				got++
			}
		}
		if got != tc.want {
			t.Errorf("percent %d put %d of 1000 on the candidate, want %d", tc.percent, got, tc.want)
		}
	}
	var none *RolloutConfig
	if none.InCandidate("org", "id") {
		t.Error("a nil rollout put a request on the candidate")
	}
}

// TestRolloutDistributionBucketIsFNV1a32 pins the hash other services and gate scripts
// precompute: FNV-1a 32-bit over org + sticky (no separator), mod 100.
func TestRolloutDistributionBucketIsFNV1a32(t *testing.T) {
	// The standard library's FNV-1a is the independent reference.
	fnv1a := func(s string) uint32 {
		h := fnv.New32a()
		_, _ = h.Write([]byte(s))
		return h.Sum32()
	}
	for _, tc := range [][2]string{{"org-acme", "sess-1"}, {"", ""}, {"org-rollout", "rollout-key-01"}, {"o", "ünïcode"}} {
		want := int(fnv1a(tc[0]+tc[1]) % 100)
		if got := RolloutBucket(tc[0], tc[1]); got != want {
			t.Errorf("RolloutBucket(%q, %q) = %d, want fnv1a32 %% 100 = %d", tc[0], tc[1], got, want)
		}
	}
}

// rolloutFixture is scripts/scenarios/fixtures/rollout/ids.json: ids whose
// buckets 17_rollout.sh and gates/rollout_session.sh rely on.
type rolloutFixture struct {
	Org     string `json:"org"`
	Percent int    `json:"percent"`
	Keys    []struct {
		ID     string `json:"id"`
		Bucket int    `json:"bucket"`
	} `json:"keys"`
	Sessions []struct {
		ID     string `json:"id"`
		Bucket int    `json:"bucket"`
	} `json:"sessions"`
}

// TestRolloutDistributionFixtureBuckets: every precomputed bucket in the
// scenario fixture is the gateway's own, and each list puts exactly half its
// ids below the fixture's percent.
func TestRolloutDistributionFixtureBuckets(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "scripts", "scenarios", "fixtures", "rollout", "ids.json"))
	if err != nil {
		t.Fatalf("reading the rollout fixture: %v", err)
	}
	var fx rolloutFixture
	if err := json.Unmarshal(raw, &fx); err != nil {
		t.Fatalf("parsing the rollout fixture: %v", err)
	}
	if fx.Org == "" || fx.Percent != 50 {
		t.Fatalf("fixture org %q percent %d, want an org and 50", fx.Org, fx.Percent)
	}
	check := func(kind string, ids []string, buckets []int) {
		if len(ids) != 40 {
			t.Fatalf("%s: %d ids, want 40", kind, len(ids))
		}
		below := 0
		seen := map[string]bool{}
		for i, id := range ids {
			if seen[id] {
				t.Fatalf("%s: duplicate id %q", kind, id)
			}
			seen[id] = true
			if got := RolloutBucket(fx.Org, id); got != buckets[i] {
				t.Errorf("%s %q: fixture bucket %d, gateway bucket %d", kind, id, buckets[i], got)
			}
			if buckets[i] < fx.Percent {
				below++
			}
		}
		if below != 20 {
			t.Errorf("%s: %d ids below %d, want exactly 20", kind, below, fx.Percent)
		}
	}
	var ids []string
	var buckets []int
	for _, k := range fx.Keys {
		ids, buckets = append(ids, k.ID), append(buckets, k.Bucket)
	}
	check("keys", ids, buckets)
	ids, buckets = nil, nil
	for _, s := range fx.Sessions {
		ids, buckets = append(ids, s.ID), append(buckets, s.Bucket)
	}
	check("sessions", ids, buckets)
}

// TestRolloutStickySession: sticky_by=session buckets on the session, then
// the run id, the key id and the request id; key on the key, then the
// request; request only on the request. The same session always lands in the
// same variant, whatever its run, key and request ids.
func TestRolloutStickySession(t *testing.T) {
	all := StickyIDs{SessionID: "s", RunID: "r", KeyID: "k", RequestID: "q"}
	cases := []struct {
		by   string
		ids  StickyIDs
		want string
	}{
		{"session", all, "s"},
		{"session", StickyIDs{RunID: "r", KeyID: "k", RequestID: "q"}, "r"},
		{"session", StickyIDs{KeyID: "k", RequestID: "q"}, "k"},
		{"session", StickyIDs{RequestID: "q"}, "q"},
		{"key", all, "k"},
		{"key", StickyIDs{SessionID: "s", RunID: "r", RequestID: "q"}, "q"},
		{"request", all, "q"},
		{"", all, "s"}, // the schema requires sticky_by; an unknown value is the stickiest chain
	}
	for _, tc := range cases {
		r := &RolloutConfig{StickyBy: tc.by}
		if got := r.StickyKey(tc.ids); got != tc.want {
			t.Errorf("sticky_by=%q with %+v: key %q, want %q", tc.by, tc.ids, got, tc.want)
		}
		// UsesKeyID agrees with the chain: dropping the key id changes the
		// sticky key exactly when the chain reaches it.
		noKey := tc.ids
		noKey.KeyID = ""
		if reaches := r.StickyKey(noKey) != r.StickyKey(withKey(tc.ids, "probe-key")); reaches != r.UsesKeyID(tc.ids) {
			t.Errorf("sticky_by=%q with %+v: UsesKeyID = %v, but the chain reaches the key: %v", tc.by, tc.ids, r.UsesKeyID(tc.ids), reaches)
		}
	}

	r := &RolloutConfig{Percent: 50, StickyBy: "session"}
	for s := 0; s < 50; s++ {
		session := "session-" + strconv.Itoa(s)
		first := r.InCandidate("org-sticky", r.StickyKey(StickyIDs{SessionID: session, RequestID: "req-0"}))
		for i := 1; i < 20; i++ {
			ids := StickyIDs{SessionID: session, RunID: "run-" + strconv.Itoa(i), KeyID: "key-" + strconv.Itoa(i), RequestID: "req-" + strconv.Itoa(i)}
			if got := r.InCandidate("org-sticky", r.StickyKey(ids)); got != first {
				t.Fatalf("session %q moved from candidate=%v to %v on request %d", session, first, got, i)
			}
		}
	}
}

// TestCandidateInherits: the candidate runs under the rollout's routing (and
// managed_models when the rollout has them), inherits agents, data_policy and
// presets from stable when it omits them, keeps the push-only upstream keys,
// shares every other section with stable, and changes nothing stored.
func TestCandidateInherits(t *testing.T) {
	stableAgents := &AgentsConfig{DefaultModel: "omega-default"}
	stableData := &DataPolicyConfig{ZDR: true, DataCollection: "deny"}
	stablePresets := map[string]*RoutingPreset{"fast": {Model: "qwen/qwen3.7-flash"}}
	upstream := map[string]*ManagedUpstreamKey{"openrouter": {APIKey: "k", KeyHash: "h"}}
	allowFree := false

	stable := &OrgConfig{
		Providers:  map[string]*ProviderConfig{"openai": {APIKey: "p", Enabled: true}},
		Guardrails: &GuardrailConfig{FailOpen: true},
		Privacy:    &PrivacyConfig{ContentMode: "metadata"},
		Meta:       &ConfigMeta{Version: 12},
		ManagedModels: &ManagedModelsConfig{
			Enabled: false, UpstreamKeys: upstream,
		},
		Routing: &RoutingConfig{
			Strategy:   "weighted",
			Auto:       &AutoRoutingConfig{Preference: "quality"},
			Agents:     stableAgents,
			DataPolicy: stableData,
			Presets:    stablePresets,
			Rollout: &RolloutConfig{
				Version: "v13", Percent: 10, StickyBy: "session",
				Routing:       &RoutingConfig{Strategy: "cost_optimized", Auto: &AutoRoutingConfig{Preference: "cost"}},
				ManagedModels: &ManagedModelsConfig{Enabled: true, AllowFree: &allowFree},
			},
		},
	}
	before := mustJSON(t, stable)

	cand := stable.Candidate()
	if cand == stable || cand.Routing == stable.Routing || cand.Routing == stable.Routing.Rollout.Routing {
		t.Fatal("Candidate must return a new config with its own routing struct")
	}
	if cand.Routing.Strategy != "cost_optimized" || cand.Routing.Auto.Preference != "cost" {
		t.Errorf("candidate routing = %q / %q, want the rollout's", cand.Routing.Strategy, cand.Routing.Auto.Preference)
	}
	if cand.Routing.Agents != stableAgents || cand.Routing.DataPolicy != stableData || !reflect.DeepEqual(cand.Routing.Presets, stablePresets) {
		t.Error("the candidate did not inherit agents, data_policy and presets it omits")
	}
	if cand.Routing.Rollout != nil {
		t.Error("the candidate config carries a rollout")
	}
	if !cand.ManagedModelsEnabled() || cand.ManagedPolicy().AllowFree {
		t.Errorf("candidate managed_models = %+v, want the rollout's overlay", cand.ManagedModels)
	}
	if !reflect.DeepEqual(cand.ManagedModels.UpstreamKeys, upstream) {
		t.Error("the candidate's managed_models overlay dropped the pushed upstream keys")
	}
	if cand.Providers["openai"] != stable.Providers["openai"] || cand.Guardrails != stable.Guardrails ||
		cand.Privacy != stable.Privacy || cand.Meta != stable.Meta {
		t.Error("the candidate must share every section the rollout does not replace")
	}
	if after := mustJSON(t, stable); after != before {
		t.Fatalf("Candidate changed the stored config:\nbefore %s\nafter  %s", before, after)
	}

	// A candidate that sets its own agents, data_policy and presets keeps them.
	own := &RoutingConfig{
		Agents:     &AgentsConfig{DefaultModel: "auto"},
		DataPolicy: &DataPolicyConfig{DataCollection: "allow"},
		Presets:    map[string]*RoutingPreset{"deep": {Model: "anthropic/claude-sonnet-5.5"}},
	}
	stable.Routing.Rollout = &RolloutConfig{Version: "v14", Percent: 10, Routing: own}
	cand = stable.Candidate()
	if cand.Routing.Agents != own.Agents || cand.Routing.DataPolicy != own.DataPolicy || cand.Routing.Presets["deep"] == nil {
		t.Error("the candidate's own agents, data_policy or presets were replaced by stable's")
	}
	// Without a managed_models overlay the candidate keeps stable's.
	if cand.ManagedModels != stable.ManagedModels {
		t.Error("a rollout without managed_models must keep stable's")
	}

	// An overlay that names its own upstream keys keeps them.
	ownKeys := map[string]*ManagedUpstreamKey{"openrouter": {APIKey: "k2", KeyHash: "h2"}}
	stable.Routing.Rollout = &RolloutConfig{Version: "v15", Routing: &RoutingConfig{},
		ManagedModels: &ManagedModelsConfig{Enabled: true, UpstreamKeys: ownKeys}}
	if got := stable.Candidate().ManagedModels.UpstreamKeys; !reflect.DeepEqual(got, ownKeys) {
		t.Error("an overlay's own upstream keys were replaced")
	}

	// A rollout without routing (the bulk sync can deliver one) is an empty
	// candidate routing that still inherits, never a panic.
	stable.Routing.Rollout = &RolloutConfig{Version: "v16"}
	cand = stable.Candidate()
	if cand.Routing == nil || cand.Routing.Strategy != "" || cand.Routing.Agents != stableAgents {
		t.Errorf("a rollout with no routing gave candidate routing %+v", cand.Routing)
	}

	// No rollout: Candidate is the config itself.
	plain := &OrgConfig{Routing: &RoutingConfig{Strategy: "weighted"}}
	if plain.Candidate() != plain {
		t.Error("a config without a rollout has no separate candidate")
	}
	var nilCfg *OrgConfig
	if nilCfg.Candidate() != nil {
		t.Error("a nil config has a candidate")
	}
}

// TestCandidateInheritsOneIdentityPerConfig: every call on one stored config
// returns the same candidate *OrgConfig and *RoutingConfig, so per-config
// state keyed on them (an org's circuit breakers, adaptive weights) builds
// up across candidate requests; a pushed config (a new pointer) or an
// in-place rollout swap gets a new candidate.
func TestCandidateInheritsOneIdentityPerConfig(t *testing.T) {
	stable := rolloutConfig("v8")
	first := stable.Candidate()
	for i := 0; i < 10; i++ {
		if c := stable.Candidate(); c != first || c.Routing != first.Routing {
			t.Fatalf("call %d returned candidate %p routing %p, want %p routing %p", i, c, c.Routing, first, first.Routing)
		}
	}

	// A push replaces the stored pointer: same content, its own candidate.
	pushed := rolloutConfig("v8")
	if c := pushed.Candidate(); c == first || c.Routing == first.Routing {
		t.Error("a newly pushed config reused the previous config's candidate")
	} else if c.Routing.Strategy != "cost_optimized" {
		t.Errorf("pushed candidate strategy = %q, want cost_optimized", c.Routing.Strategy)
	}

	// The rollout swapped in place rebuilds rather than serving the old one.
	stable.Routing.Rollout = &RolloutConfig{Version: "v9", Percent: 10,
		Routing: &RoutingConfig{Strategy: "least_latency"}}
	if c := stable.Candidate(); c == first || c.Routing.Strategy != "least_latency" {
		t.Errorf("after an in-place rollout swap the candidate strategy = %q, want least_latency", c.Routing.Strategy)
	}
	again := stable.Candidate()
	if again != stable.Candidate() {
		t.Error("the rebuilt candidate is not stable across calls")
	}
	// The managed_models section swapped in place rebuilds too.
	stable.ManagedModels = &ManagedModelsConfig{Enabled: true}
	if stable.Candidate() == again {
		t.Error("a managed_models swap kept the old candidate")
	}
}

// TestCandidateInheritsConcurrentCallsShareOne: concurrent first calls on a
// config all get the one candidate (run under -race).
func TestCandidateInheritsConcurrentCallsShareOne(t *testing.T) {
	stable := rolloutConfig("v8")
	const n = 64
	got := make([]*OrgConfig, n)
	var wg sync.WaitGroup
	start := make(chan struct{})
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			<-start
			got[i] = stable.Candidate()
		}(i)
	}
	close(start)
	wg.Wait()
	for i, c := range got {
		if c != got[0] {
			t.Fatalf("goroutine %d got candidate %p, goroutine 0 got %p", i, c, got[0])
		}
	}
}

// TestCandidateInheritsHitAllocatesNothing: after the first call a candidate
// lookup allocates nothing, so candidate traffic costs no more than stable.
// It relies on weak.Make returning the existing handle without allocating on
// a repeat call; if a toolchain changes that, adjust the bound, not the memo.
func TestCandidateInheritsHitAllocatesNothing(t *testing.T) {
	stable := rolloutConfig("v8")
	stable.Candidate()
	if allocs := testing.AllocsPerRun(1000, func() { _ = stable.Candidate() }); allocs != 0 {
		t.Errorf("a memoized Candidate() allocated %.1f times per call, want 0", allocs)
	}
}

// TestCandidateInheritsCacheHoldsOnlyLiveConfigs: the memo holds a config's
// candidate only while the config is alive, so it never grows with pushes.
// 1,000 configs that were each replaced (dropped) leave no entry once the
// collector has reclaimed them, while a live config keeps its candidate.
// Cleanups run on their own goroutine some time after a collection, so the
// test polls (GC, short sleep) for up to 10 s; that tolerance is deliberate.
func TestCandidateInheritsCacheHoldsOnlyLiveConfigs(t *testing.T) {
	live := rolloutConfig("v8")
	liveCand := live.Candidate()
	dropped := dropCandidates(1000)

	deadline := time.Now().Add(10 * time.Second)
	for remaining := memoized(dropped); remaining > 0; remaining = memoized(dropped) {
		if time.Now().After(deadline) {
			t.Fatalf("%d of %d dropped configs still have a memoized candidate", remaining, len(dropped))
		}
		runtime.GC()
		time.Sleep(10 * time.Millisecond)
	}
	if live.Candidate() != liveCand {
		t.Error("a live config lost its candidate while dropped ones were reclaimed")
	}
	runtime.KeepAlive(live)
}

// dropCandidates builds n configs, takes each one's candidate and drops the
// config, returning only weak keys, which keep nothing alive.
func dropCandidates(n int) []weak.Pointer[OrgConfig] {
	keys := make([]weak.Pointer[OrgConfig], n)
	for i := range keys {
		c := rolloutConfig("v" + strconv.Itoa(i))
		c.Candidate()
		keys[i] = weak.Make(c)
	}
	return keys
}

func memoized(keys []weak.Pointer[OrgConfig]) int {
	candidates.mu.RLock()
	defer candidates.mu.RUnlock()
	n := 0
	for _, k := range keys {
		if _, ok := candidates.m[k]; ok {
			n++
		}
	}
	return n
}

// rolloutConfig is a stored config with a 10% cost_optimized rollout.
func rolloutConfig(version string) *OrgConfig {
	return &OrgConfig{
		Meta: &ConfigMeta{Version: 7},
		Routing: &RoutingConfig{
			Strategy:       "round_robin",
			CircuitBreaker: &CircuitBreakerConfig{Enabled: true},
			Rollout: &RolloutConfig{Version: version, Percent: 10, StickyBy: "request",
				Routing: &RoutingConfig{Strategy: "cost_optimized", CircuitBreaker: &CircuitBreakerConfig{Enabled: true}}},
		},
	}
}

func withKey(ids StickyIDs, key string) StickyIDs {
	ids.KeyID = key
	return ids
}

func mustJSON(t *testing.T, v any) string {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	return string(b)
}
