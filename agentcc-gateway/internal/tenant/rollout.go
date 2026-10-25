package tenant

// A staged rollout (routing.rollout) runs a candidate routing policy on a
// deterministic, sticky share of an org's traffic. This file holds the bucket,
// the sticky key and the candidate config, which is built once per stored
// config and memoized for as long as that config is alive. The request path
// (server.effectiveOrgConfig) picks the variant and stamps the request.

import (
	"runtime"
	"sync"
	"weak"
)

// Policy variants, and the policy version of a config that carries none.
const (
	PolicyVariantStable    = "stable"
	PolicyVariantCandidate = "candidate"
	PolicyUnversioned      = "unversioned"
)

// StickyIDs are the request identifiers a rollout can bucket on. An empty
// field is absent.
type StickyIDs struct {
	SessionID string
	RunID     string
	KeyID     string
	RequestID string
}

// RolloutBucket is the request's bucket, 0-99: FNV-1a 32-bit over the org id
// followed by the sticky id (no separator), mod 100. Scenario fixtures and
// gate scripts precompute it, so it never changes.
func RolloutBucket(orgID, sticky string) int {
	h := uint32(fnvOffset32)
	for i := 0; i < len(orgID); i++ {
		h = (h ^ uint32(orgID[i])) * fnvPrime32
	}
	for i := 0; i < len(sticky); i++ {
		h = (h ^ uint32(sticky[i])) * fnvPrime32
	}
	return int(h % 100)
}

// FNV-1a 32-bit parameters, inlined so a bucket allocates nothing.
const (
	fnvOffset32 = 2166136261
	fnvPrime32  = 16777619
)

// StickyKey is the identifier the rollout buckets on. sticky_by=session uses
// the session, then the run id, the key id and the request id; key uses the
// key id, then the request id; request uses the request id. The schema
// requires sticky_by; an empty or unknown value takes the session chain, the
// stickiest.
func (r *RolloutConfig) StickyKey(ids StickyIDs) string {
	switch r.StickyBy {
	case "request":
		return ids.RequestID
	case "key":
		return firstNonEmpty(ids.KeyID, ids.RequestID)
	default:
		return firstNonEmpty(ids.SessionID, ids.RunID, ids.KeyID, ids.RequestID)
	}
}

// UsesKeyID reports whether StickyKey can reach the key id for these ids, so
// a caller that must look the key up does so only when it counts.
func (r *RolloutConfig) UsesKeyID(ids StickyIDs) bool {
	switch r.StickyBy {
	case "request":
		return false
	case "key":
		return true
	default:
		return ids.SessionID == "" && ids.RunID == ""
	}
}

// InCandidate reports whether the sticky id lands in the candidate share:
// its bucket is below the rollout's percent. A nil rollout has no candidate.
func (r *RolloutConfig) InCandidate(orgID, sticky string) bool {
	return r != nil && RolloutBucket(orgID, sticky) < r.Percent
}

// Candidate returns the config candidate traffic runs under, or c itself when
// c has no rollout. It is a shallow copy of c that shares every section the
// rollout does not replace, so the stored config is never changed:
//   - Routing is the rollout's routing, with agents, data_policy and presets
//     taken from stable when the candidate omits them, and no rollout of its
//     own.
//   - ManagedModels is the rollout's managed_models when present. The
//     control plane pushes per-org upstream keys only at the top level,
//     so the overlay keeps stable's unless it names its own.
//
// The candidate is built once per stored config: every call on the same c
// returns the same *OrgConfig and *RoutingConfig, so per-config state keyed
// on those pointers (an org's circuit breakers, adaptive weights and
// round-robin position) accumulates across candidate requests as it does for
// stable. The store replaces the *OrgConfig on every push and never edits
// one in place; a call that finds c's routing, rollout or managed_models
// pointer changed since the build rebuilds.
//
// Meta stays stable's, so the candidate's PolicyVersion() is stable's too:
// the version that decided a request is rc.Evidence.PolicyVersion, and a
// cache of per-config state keyed on a version must key on it (or the
// variant), never on meta.version alone.
func (c *OrgConfig) Candidate() *OrgConfig {
	ro := c.Rollout()
	if ro == nil {
		return c
	}
	key := weak.Make(c)
	candidates.mu.RLock()
	e := candidates.m[key]
	candidates.mu.RUnlock()
	if e.builtFrom(c) {
		return e.cand
	}

	candidates.mu.Lock()
	defer candidates.mu.Unlock()
	e, known := candidates.m[key]
	if e.builtFrom(c) {
		return e.cand // a concurrent call built it first
	}
	e = &candidateEntry{routing: c.Routing, rollout: ro, managed: c.ManagedModels, cand: c.buildCandidate(ro)}
	candidates.m[key] = e
	if !known {
		// The entry lives as long as c: once the store drops c and the
		// collector reclaims it, the entry goes too. The cleanup holds only
		// the weak key, so it never keeps c alive.
		runtime.AddCleanup(c, forgetCandidate, key)
	}
	return e.cand
}

// candidates memoizes each stored config's candidate, keyed by a weak
// pointer to the stored config so the cache holds exactly the live configs.
var candidates = struct {
	mu sync.RWMutex
	m  map[weak.Pointer[OrgConfig]]*candidateEntry
}{m: map[weak.Pointer[OrgConfig]]*candidateEntry{}}

// candidateEntry is a built candidate and the stored pointers it was built
// from.
type candidateEntry struct {
	routing *RoutingConfig
	rollout *RolloutConfig
	managed *ManagedModelsConfig
	cand    *OrgConfig
}

// builtFrom reports whether e is c's candidate as c stands now.
func (e *candidateEntry) builtFrom(c *OrgConfig) bool {
	return e != nil && e.routing == c.Routing && e.rollout == c.Routing.Rollout && e.managed == c.ManagedModels
}

func forgetCandidate(key weak.Pointer[OrgConfig]) {
	candidates.mu.Lock()
	delete(candidates.m, key)
	candidates.mu.Unlock()
}

// buildCandidate makes c's candidate config for its rollout ro.
func (c *OrgConfig) buildCandidate(ro *RolloutConfig) *OrgConfig {
	cand := *c
	routing := RoutingConfig{}
	if ro.Routing != nil {
		routing = *ro.Routing
	}
	stable := c.Routing
	if routing.Agents == nil {
		routing.Agents = stable.Agents
	}
	if routing.DataPolicy == nil {
		routing.DataPolicy = stable.DataPolicy
	}
	if routing.Presets == nil {
		routing.Presets = stable.Presets
	}
	routing.Rollout = nil
	cand.Routing = &routing

	if ro.ManagedModels != nil {
		managed := *ro.ManagedModels
		if managed.UpstreamKeys == nil && c.ManagedModels != nil {
			managed.UpstreamKeys = c.ManagedModels.UpstreamKeys
		}
		cand.ManagedModels = &managed
	}
	return &cand
}

func firstNonEmpty(values ...string) string {
	for _, v := range values {
		if v != "" {
			return v
		}
	}
	return ""
}
