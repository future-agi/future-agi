package auth

import (
	"context"
	"fmt"
	"time"
)

// cachedEligibilityWindow bounds missed-notification revocation latency to at
// most 30s from the start of a successful PRIMARY check. Hits never slide it;
// cache expiry, grant expiry and delivered invalidations can only shorten it.
const cachedEligibilityWindow = 30 * time.Second

// The production resolver rechecks PRIMARY once per credential window and
// generation/epoch. Successful cold resolution can carry the remaining read
// budget across TTL eviction, never stale authority. Injected resolvers may own
// their own liveness policy (NewWithResolver).
type cachedEligibilityResolver interface {
	RevalidateCached(context.Context, string, string, *ResolveResult) (bool, error)
}

func (r *PGResolver) RevalidateCached(ctx context.Context, api, secret string, prior *ResolveResult) (bool, error) {
	if prior == nil {
		return false, nil
	}
	var live *ResolveResult
	var err error
	if prior.Grant != nil {
		live, err = r.ValidateGrant(ctx, prior.Grant)
	} else {
		live, err = r.ValidateKey(ctx, api, secret)
	}
	if err != nil || live == nil {
		return false, err
	}
	// Reusing the project's map requires identical immutable authority, not
	// just a still-valid credential whose owner/workspace was changed.
	return authorityOf(live) == authorityOf(prior), nil
}

func (a *Authenticator) cachedCredentialCurrent(ctx context.Context, api, secret, key string, prior *ResolveResult) (bool, error) {
	// In-package envelope fixtures seed a cache without any resolver. The
	// production constructors cannot create that shape (NewWithResolver refuses
	// nil). Do not mistake keyResolver's typed nil *PGResolver for persistence.
	if a.keys == nil && a.pg == nil {
		return a.cache.currentResult(ctx, key, prior)
	}
	resolver, ok := a.keyResolver().(cachedEligibilityResolver)
	if !ok {
		return a.cache.currentResult(ctx, key, prior)
	}
	checkCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	if err := credentialDeadline(checkCtx, prior.Grant); err != nil {
		return false, err
	}
	if err := a.cache.mu.LockContext(checkCtx); err != nil {
		return false, err
	}
	entry, status := a.cache.peek(key)
	if status != "fresh" || entry.result != prior {
		a.cache.mu.Unlock()
		return false, nil
	}
	generation, epoch := a.cache.generation.Load(), a.cache.generationFor(key).epoch
	validated := entry.eligibilityCurrent(a.eligibilityNow(), generation, epoch)
	a.cache.mu.Unlock()
	if validated {
		return a.admitCachedEligibility(checkCtx, key, entry, generation, epoch)
	}

	// A distinct namespace avoids cold-flight collisions. Entry identity and
	// both epochs prevent a new authority from joining an old in-flight check.
	flight := fmt.Sprintf("cached:%s:%p:%d:%d", key, entry, generation, epoch)
	lookup := a.sfKey.DoChan(flight, func() (any, error) {
		// Like cold validation, one canceled caller cannot cancel live waiters.
		workerCtx, stop := context.WithTimeout(context.WithoutCancel(checkCtx), 10*time.Second)
		defer stop()
		return a.revalidateCachedEntry(workerCtx, resolver, api, secret, key, entry, generation, epoch)
	})
	select {
	case <-checkCtx.Done():
		return false, checkCtx.Err()
	case checked := <-lookup:
		if err := credentialDeadline(checkCtx, prior.Grant); err != nil {
			return false, err
		}
		if checked.Err != nil {
			return false, checked.Err
		}
		if current, _ := checked.Val.(bool); !current {
			return false, nil
		}
	}
	return a.admitCachedEligibility(checkCtx, key, entry, generation, epoch)
}

// Admission metadata stays entry-bound under the cache gate. A separate,
// expiring read-budget reservation can seed a newly PRIMARY-validated entry;
// ordinary cache hits never consult that reservation as authority.
func (e *cacheEntry) eligibilityCurrent(now time.Time, generation, epoch uint64) bool {
	age := now.Sub(e.revalidatedAt)
	return !e.revalidatedAt.IsZero() && age >= 0 && age < cachedEligibilityWindow &&
		e.revalidatedGeneration == generation && e.revalidatedEpoch == epoch
}

func (a *Authenticator) eligibilityNow() time.Time {
	if a.clock != nil {
		return a.clock()
	}
	return time.Now()
}

// Caller holds c.mu. Check entry identity as well as the immutable result:
// replacing an entry with the same result must not inherit its old window.
func (c *cache) sameEligibilityEntry(key string, expected *cacheEntry, generation, epoch uint64) bool {
	entry, status := c.peek(key)
	return status == "fresh" && entry == expected &&
		c.generation.Load() == generation && c.generationFor(key).epoch == epoch
}

func (a *Authenticator) revalidateCachedEntry(ctx context.Context, resolver cachedEligibilityResolver, api, secret, key string, entry *cacheEntry, generation, epoch uint64) (bool, error) {
	if err := credentialDeadline(ctx, entry.result.Grant); err != nil {
		return false, err
	}
	if err := a.cache.mu.LockContext(ctx); err != nil {
		return false, err
	}
	if !a.cache.sameEligibilityEntry(key, entry, generation, epoch) {
		a.cache.mu.Unlock()
		return false, nil
	}
	started := a.eligibilityNow()
	if entry.eligibilityCurrent(started, generation, epoch) {
		// A caller may reach DoChan after the previous flight has completed.
		a.cache.mu.Unlock()
		return true, nil
	}
	// Reserve before leaving the gate: a real cold lookup crossing this check
	// may use its OWN PRIMARY result to avoid a second cached check after TTL.
	a.cache.startPrimaryReadLocked(key, entry, started, generation, epoch)
	read := a.cache.primaryReadSnapshotLocked(key)
	a.cache.mu.Unlock()

	live, err := resolver.RevalidateCached(ctx, api, secret, entry.result)
	if deadlineErr := credentialDeadline(ctx, entry.result.Grant); deadlineErr != nil {
		return false, deadlineErr
	}
	if err != nil {
		return false, err // PRIMARY failure is unavailable, never stale success.
	}
	if err := a.cache.mu.LockContext(ctx); err != nil {
		return false, err
	}
	current := a.cache.sameEligibilityEntry(key, entry, generation, epoch)
	if !live {
		// TTL expiry alone cannot preserve a rejected budget. Snapshot identity
		// also fences a newer handoff/budget when its entry has already expired.
		a.cache.rejectPrimaryReadLocked(key, read, generation, epoch)
		if current {
			// A late negative cannot evict a newer result or generation.
			a.cache.generation.Add(1)
			a.cache.invalidateKeyLocked(key)
		}
	} else if current {
		age := a.eligibilityNow().Sub(started)
		current = age >= 0 && age < cachedEligibilityWindow
		if current {
			// Query time consumes the window. Never refresh fetchedAt, replace
			// the result/project map, or publish an evicted entry here.
			entry.revalidatedAt = started
			entry.revalidatedGeneration = generation
			entry.revalidatedEpoch = epoch
		}
	}
	a.cache.mu.Unlock()
	if err := credentialDeadline(ctx, entry.result.Grant); err != nil {
		return false, err
	}
	if !live {
		return false, ErrUnauthenticated
	}
	return current, nil
}

// Both fast hits and flight waiters re-observe all fences under the invalidation
// gate. A successful worker is not permission to ignore a later notification.
func (a *Authenticator) admitCachedEligibility(ctx context.Context, key string, entry *cacheEntry, generation, epoch uint64) (bool, error) {
	if err := a.cache.mu.LockContext(ctx); err != nil {
		return false, err
	}
	current := a.cache.sameEligibilityEntry(key, entry, generation, epoch) &&
		entry.eligibilityCurrent(a.eligibilityNow(), generation, epoch)
	a.cache.mu.Unlock()
	if err := credentialDeadline(ctx, entry.result.Grant); err != nil {
		return false, err
	}
	current = current && entry.fresh(a.cache.ttl)
	if err := ctx.Err(); err != nil {
		return false, err
	}
	return current, nil
}

// The composed entrypoint retains its existing inline caller/expiry fences.
// Cached PRIMARY work uses the same order and wall clock at every boundary.
func credentialDeadline(ctx context.Context, claims *GrantClaims) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if claims != nil && time.Now().Unix() >= claims.Exp {
		return ErrUnauthenticated
	}
	return nil
}
