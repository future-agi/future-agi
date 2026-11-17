package auth

import (
	"context"
	"time"
)

// Immutable binding. Its identity also fences an authentication racing a
// rebind, so an answer from the previous authority cannot be used afterward.
type keyRevocationCheck struct {
	check func(context.Context, string, string) (bool, error)
}

// SetRevocationCheck installs authoritative revocation checks before serving
// multi-replica traffic. Nil never removes a binding. A store that was never
// bound retains the existing single-instance behavior; Redis-enabled startup
// must bind even if its initial Redis connection failed.
// SetRevocationCheck retains compatibility for hash-only callers.
// Production uses SetRevocationIdentityCheck to retain historical ID denial.
func (ks *KeyStore) SetRevocationCheck(check func(context.Context, string) (bool, error)) {
	if check == nil {
		return
	}
	ks.SetRevocationIdentityCheck(func(ctx context.Context, _ string, hash string) (bool, error) {
		return check(ctx, hash)
	})
}

// SetRevocationIdentityCheck atomically binds both credential identities.
// A nil check never removes an existing fail-closed authority binding.
func (ks *KeyStore) SetRevocationIdentityCheck(check func(context.Context, string, string) (bool, error)) {
	if check == nil {
		return
	}
	ks.mu.Lock()
	defer ks.mu.Unlock()
	ks.revocation = &keyRevocationCheck{check: check}
}

// activeWithRevocation checks even cached-active keys against current authority.
// Never hold the store lock over network IO: local revoke/sync must proceed.
// An expired/error result refuses without permanently revoking unrelated keys.
func (ks *KeyStore) activeWithRevocation(hash string, markUsed bool) (*APIKey, map[string]string) {
	ks.mu.RLock()
	key := ks.byHash[hash]
	if key == nil || !key.IsActive() || ks.configPending(key) {
		ks.mu.RUnlock()
		return nil, nil
	}
	binding, id, identity := ks.revocation, key.ID, key.KeyHash
	ks.mu.RUnlock()

	var freshUntil time.Time
	if binding != nil {
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		freshUntil, _ = ctx.Deadline()
		revoked, err := binding.check(ctx, id, identity)
		expired := ctx.Err() != nil
		cancel()
		if revoked || err != nil || expired {
			return nil, nil
		}
	}

	ks.mu.RLock()
	defer ks.mu.RUnlock()
	// Waiting for a local writer must not extend the authority's validity.
	if !freshUntil.IsZero() && !time.Now().Before(freshUntil) {
		return nil, nil
	}
	// The list, status or binding may have changed while authority was read.
	if ks.byHash[hash] != key || ks.revocation != binding || key.ID != id || key.KeyHash != identity || !key.IsActive() || ks.configPending(key) {
		return nil, nil
	}
	if markUsed {
		now := time.Now()
		key.lastUsedAt.Store(&now)
	}
	return key, key.Metadata
}
