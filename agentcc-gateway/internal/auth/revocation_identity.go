package auth

import (
	"strings"
	"time"
)

// RevokeWithIdentity captures the credential hash and revokes under the same
// lock. Returning the current slot's hash in a later Get would race a sync or
// replacement. Retries retain the identity even when already locally revoked.
func (ks *KeyStore) RevokeWithIdentity(id string) (bool, string) {
	ks.mu.Lock()
	defer ks.mu.Unlock()
	key := ks.byID[id]
	if !ks.revokeKeyLocked(key) {
		return false, ""
	}
	return true, key.KeyHash
}

// ApplyRevocation consumes the Redis wire format. Credential events invalidate
// the exact hash, even when replicas have different configuration slot orders.
// Legacy ID-only events are transient best-effort hints for synced keys; they
// must never revoke a configuration slot. New known-key publishers use hashes.
func (ks *KeyStore) ApplyRevocation(message string) bool {
	ks.mu.Lock()
	defer ks.mu.Unlock()
	if hash, ok := strings.CutPrefix(message, "sha256:"); ok {
		return ks.revokeKeyLocked(ks.byHash[hash])
	}
	key := ks.byID[message]
	if key != nil && key.Source == "config" {
		return false
	}
	return ks.revokeKeyLocked(key)
}

func (ks *KeyStore) revokeKeyLocked(key *APIKey) bool {
	if key == nil || (ks.controlPlane && key.Source == "config") {
		return false
	}
	ks.blockedHashes[key.KeyHash] = true
	key.Status = "revoked"
	key.UpdatedAt = ks.now()
	key.mintedAt = time.Time{}
	return true
}
