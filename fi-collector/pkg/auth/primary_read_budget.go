package auth

import "time"

// credentialAuthority is a value snapshot, not an entry/result/project-map
// reference. These are the same immutable fields checked by RevalidateCached.
// In particular, a grant id alone is not enough to identify its signed claims.
type credentialAuthority struct {
	org, workspace, user, keyType, digest string
	principal                             Principal
	simulation                            SimulationAgent
	grant                                 GrantClaims
	hasGrant                              bool
}

func authorityOf(r *ResolveResult) credentialAuthority {
	a := credentialAuthority{
		org: r.OrgID, workspace: r.WorkspaceID, user: r.UserID,
		keyType: r.KeyType, digest: r.APIKeyDigest, principal: r.Principal,
		simulation: r.SimulationAgent,
	}
	if r.Grant != nil {
		a.grant, a.hasGrant = *r.Grant, true
	}
	return a
}

// A recent read is ONLY a credential-level revalidation budget, never authority
// to serve a hit or a miss. Ordinary TTL eviction must not reset this budget.
// It is reserved at query start so a cold lookup crossing an in-flight check can
// also avoid a duplicate check, but only after obtaining its OWN live PRIMARY
// result. Errors cannot validate a resident entry; its next request still retries.
// Keys are CacheKey digests/grant ids; no raw secrets or project maps are retained.
type primaryReadBudget struct {
	authority         credentialAuthority
	started           time.Time
	generation, epoch uint64
	publication       uint64 // Advances when newer positive authority uses this reservation.
	expiry            *time.Timer
}

// A negative may clear only the reservation it observed, not one subsequently
// reused by newer authority. Snapshots live only for a lookup; the budget map
// still retains no entry/result pointers. Publication identity must survive
// expiry/removal of a replacement that adopted the very same reservation.
type primaryReadSnapshot struct {
	budget      *primaryReadBudget
	publication uint64
	entry       *cacheEntry
}

// Caller holds c.mu; include expired resident identity rather than using peek.
func (c *cache) primaryReadSnapshotLocked(key string) primaryReadSnapshot {
	snapshot := primaryReadSnapshot{budget: c.primaryReads[key]}
	if snapshot.budget != nil {
		snapshot.publication = snapshot.budget.publication
		if raw, ok := c.m.Load(key); ok {
			snapshot.entry = raw.(*cacheEntry)
		}
	}
	return snapshot
}

// Caller holds c.mu. Ordinary TTL removal is not supersession, but a new entry,
// a new reservation, a positive publication or either changed fence is. A true
// return permits the caller to invalidate the still-matching resident as well.
func (c *cache) rejectPrimaryReadLocked(key string, observed primaryReadSnapshot, generation, epoch uint64) bool {
	if observed.budget == nil || c.primaryReads[key] != observed.budget ||
		observed.budget.publication != observed.publication ||
		c.generation.Load() != generation || c.generationFor(key).epoch != epoch {
		return false
	}
	if raw, ok := c.m.Load(key); ok && raw != observed.entry {
		return false
	}
	c.dropPrimaryReadLocked(key)
	return true
}

// Caller holds c.mu. Only actual RevalidateCached work creates a budget. Initial
// cold publication therefore preserves the first-fresh-hit revocation fence.
func (c *cache) startPrimaryReadLocked(key string, entry *cacheEntry, started time.Time, generation, epoch uint64) {
	c.dropPrimaryReadLocked(key)
	if c.primaryReads == nil {
		c.primaryReads = make(map[string]*primaryReadBudget)
	}
	budget := &primaryReadBudget{authority: authorityOf(entry.result), started: started, generation: generation, epoch: epoch}
	c.primaryReads[key] = budget
	// Bound retention even if a credential is never seen again. The callback
	// takes the same gate; an old timer cannot erase a replacement reservation.
	budget.expiry = time.AfterFunc(cachedEligibilityWindow, func() {
		c.mu.Lock()
		defer c.mu.Unlock()
		if c.primaryReads[key] == budget {
			c.dropPrimaryReadLocked(key)
		}
	})
}

func (c *cache) dropPrimaryReadLocked(key string) {
	if budget := c.primaryReads[key]; budget != nil {
		budget.expiry.Stop()
		delete(c.primaryReads, key)
		if len(c.primaryReads) == 0 {
			c.primaryReads = nil // Release idle map storage as well as its records.
		}
	}
}

// Called under the publication gate ONLY after successful cold PRIMARY
// resolution, with its existing cancellation, expiry, age and epoch fences.
// It never supplies a result or refreshes fetchedAt. A new entry uses only its
// freshly resolved authority/projects; the original read-start time is retained
// without sliding. A negative/error cold lookup cannot reach this handoff.
func (c *cache) applyPrimaryBudgetLocked(key string, entry *cacheEntry, now time.Time, generation, epoch uint64) {
	budget := c.primaryReads[key]
	if budget == nil {
		return
	}
	age := now.Sub(budget.started)
	if age < 0 || age >= cachedEligibilityWindow || budget.generation != generation || budget.epoch != epoch || budget.authority != authorityOf(entry.result) {
		c.dropPrimaryReadLocked(key)
		return
	}
	entry.revalidatedAt = budget.started
	entry.revalidatedGeneration = generation
	entry.revalidatedEpoch = epoch
}
