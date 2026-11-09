package auth

import (
	"log/slog"
	"sort"
	"strings"
	"time"
)

// KeySyncState carries negative facts independently of the positive key list.
// A nil ConfigConflicts means the peer did not answer the seed-hash challenge.
type KeySyncState struct {
	Snapshot        *KeySnapshot `json:"snapshot"`
	Retired         []RetiredKey `json:"retired"`
	ConfigConflicts *[]string    `json:"config_conflicts"`
}

type KeySnapshot struct {
	Complete   bool  `json:"complete"`
	IssuedEver int64 `json:"issued_ever"`
}

type RetiredKey struct {
	ID      string `json:"id"`
	KeyHash string `json:"key_hash"`
}

func (s KeySyncState) authoritative() bool {
	return s.Snapshot != nil && s.Snapshot.Complete && s.Snapshot.IssuedEver > 0
}

// personKeysOnly reports whether the answer came from a 733 control plane,
// which lists only person-owned keys: a 733 answer. Every 733 answer carries a
// snapshot, even a fresh install's (issued_ever 0, so not authoritative); a
// pre-733 answer carries none, and its keys have org_id but no user_id. The
// first 733 answer applied sets the 733 latch (KeyStore.latched733): until
// then a control-plane gateway loads an unowned key, so a new gateway behind
// an older Django keeps its keys (L4: either deploy may go first); from then
// on it refuses one, whatever later answers carry.
func (s KeySyncState) personKeysOnly() bool {
	return s.Snapshot != nil
}

// pre733LogLine is logged once, when a control-plane gateway first applies a
// pre-733 answer before any 733 one; /readyz carries the same state as
// "control_plane": "pre-733" (ControlPlanePre733). Both end at the latch.
const pre733LogLine = "control plane pre-733: its key list carries no 733 snapshot, so until its first 733 answer synced keys without user_id load, an additive push needs no owner, and a config seed whose hash only a deleted key holds still serves"

// noteAnswerGenerationLocked records which control-plane generation answered
// a list this sync applies: a 733 answer sets the latch for good, and a
// pre-733 one before the latch marks the control plane pre-733. Called only
// under the store lock.
func (ks *KeyStore) noteAnswerGenerationLocked(state KeySyncState) {
	switch {
	case state.personKeysOnly():
		ks.latched733 = true
	case !ks.controlPlane:
	case ks.latched733:
		if !ks.latchHeldLogged {
			ks.latchHeldLogged = true
			slog.Warn("key sync: an answer without the 733 snapshot after a 733 one; 733 key semantics stay latched")
		}
	case !ks.pre733:
		ks.pre733 = true
		slog.Warn(pre733LogLine, "control_plane", "pre-733")
	}
}

// ControlPlanePre733 reports whether a control-plane gateway's control plane
// is pre-733: a pre-733 answer was applied and no 733 answer yet (the latch).
// /readyz reports it, so ops can see it.
func (ks *KeyStore) ControlPlanePre733() bool {
	ks.mu.RLock()
	defer ks.mu.RUnlock()
	return ks.pre733 && !ks.latched733
}

// conflictsAnsweredLocked reports whether the answer settles which config
// seeds collide with a stored key, so that seeds may serve (KeyListLoaded). A
// 733 control plane answers config_conflicts on every list; a 733 answer (it
// carries the snapshot) without it leaves seeds pending. A pre-733 control
// plane has no conflict check and sends neither field: its positive list is
// its whole conflict answer, and refuseSyncedHash refuses a seed it lists
// together with that key. What it cannot report is a seed whose hash only a
// deleted row holds (C4's revoked-hash restart). Such a seed serves, as every
// seed did before 733, until Django is upgraded and its first conflict answer
// refuses it for good. So a gateway deployed before Django is ready after its
// first list, as one deployed after it is: the deploy order never changes
// readiness (Test733DeployOrderNeverChangesReadinessOrKeys). Under the 733
// latch an answer without the snapshot is no conflict answer of its own: only
// config_conflicts settles the seeds. Called only under the store lock.
func (ks *KeyStore) conflictsAnsweredLocked(s KeySyncState) bool {
	return s.ConfigConflicts != nil || (s.Snapshot == nil && !ks.latched733)
}

// dropUnownedSyncedLocked removes every synced key without user_id, as a
// replacing 733 list would, so that under the latch the keys that
// authenticate never include one: not one pushed before the first list, not
// one a pre-733 list loaded, whatever list comes next (an empty one that is
// not authoritative keeps the keys held, Sol's r4 P2). The hash is not
// blocked: a later list or push of it is refused by the latched owner rule.
// Called only under the store lock, in control-plane mode.
func (ks *KeyStore) dropUnownedSyncedLocked() {
	for hash, key := range ks.byHash {
		if key.Source != "sync" || personMetadata(key.Metadata) {
			continue
		}
		slog.Warn("key sync: dropping a synced key without user_id under 733 key semantics", "id", key.ID)
		delete(ks.byHash, hash)
		if ks.byID[key.ID] == key {
			delete(ks.byID, key.ID)
		}
	}
}

func personMetadata(metadata map[string]string) bool {
	return strings.TrimSpace(metadata["org_id"]) != "" && strings.TrimSpace(metadata["user_id"]) != ""
}

// holdsSyncedKeyLocked is called only under the store lock.
func (ks *KeyStore) holdsSyncedKeyLocked() bool {
	for _, key := range ks.byHash {
		if key.Source == "sync" {
			return true
		}
	}
	return false
}

func (ks *KeyStore) configPending(key *APIKey) bool {
	return ks.controlPlane && key.Source == "config" && !ks.listLoaded.Load()
}

// ConfigKeyPending distinguishes a known deployment identity awaiting the first
// conflict answer (503) from an invalid or refused key (401).
func (ks *KeyStore) ConfigKeyPending(raw string) bool {
	ks.mu.RLock()
	defer ks.mu.RUnlock()
	key := ks.byHash[HashKey(raw)]
	return key != nil && key.IsActive() && ks.configPending(key)
}

// ConfigKeyNotRevocable applies only to control-plane deployment identities.
// Standalone operators can still revoke their configured keys through the API.
func (ks *KeyStore) ConfigKeyNotRevocable(id string) bool {
	ks.mu.RLock()
	defer ks.mu.RUnlock()
	key := ks.byID[id]
	return ks.controlPlane && key != nil && key.Source == "config"
}

// ConfigKeyHashes is bounded at load time, and never contains raw credentials.
func (ks *KeyStore) ConfigKeyHashes() []string {
	ks.mu.RLock()
	defer ks.mu.RUnlock()
	hashes := make([]string, 0, len(ks.configSeeds))
	for hash := range ks.configSeeds {
		hashes = append(hashes, hash)
	}
	sort.Strings(hashes)
	return hashes
}

// ConfigKeysRefused returns a detached health-counter snapshot.
func (ks *KeyStore) ConfigKeysRefused() (int, map[string]int) {
	ks.mu.RLock()
	defer ks.mu.RUnlock()
	total := 0
	reasons := make(map[string]int, len(ks.configRefused))
	for reason, count := range ks.configRefused {
		reasons[reason] = count
		total += count
	}
	return total, reasons
}

// dropHash is called only under the store lock. Original seed hashes remain in
// configSeeds, so subsequent positive imports cannot revive a refused seed.
func (ks *KeyStore) dropHash(hash, reason string) {
	ks.blockedHashes[hash] = true
	for id, key := range ks.byID {
		if key.KeyHash != hash {
			continue
		}
		if key.Source == "config" {
			ks.configRefused[reason]++
			slog.Error("config key refused", "id", key.ID, "reason", reason)
		}
		delete(ks.byID, id)
	}
	delete(ks.byHash, hash)
}

func (ks *KeyStore) applyNegativeSync(state KeySyncState) {
	for _, retired := range state.Retired {
		if !isSHA256Hex(retired.KeyHash) {
			continue
		}
		hash := strings.ToLower(retired.KeyHash)
		ks.blockedHashes[hash] = true
		for _, key := range ks.byID {
			if key.KeyHash != hash && (retired.ID == "" || key.ID != retired.ID) {
				continue
			}
			if key.Source == "config" {
				ks.dropHash(key.KeyHash, "retired_collision")
			} else {
				ks.blockedHashes[key.KeyHash] = true
				key.Status = "revoked"
				key.UpdatedAt = ks.now()
				key.mintedAt = time.Time{}
			}
		}
	}
	if state.ConfigConflicts != nil {
		for _, hash := range *state.ConfigConflicts {
			if isSHA256Hex(hash) {
				ks.dropHash(strings.ToLower(hash), "hash_collision")
			}
		}
	}
}

// refuseSyncedHash also catches collisions from additive POST /-/keys/sync,
// and old peers whose positive list is their only conflict signal. A seed
// collision is refused from every peer; an unowned key only when requireOwner
// is set, which both the list and the additive push take from the 733 latch
// (KeyStore.latched733): a push carries no snapshot of its own, and a list
// without one after the latch does not revert it.
func (ks *KeyStore) refuseSyncedHash(sk SyncedKey, requireOwner bool) bool {
	if ks.controlPlane {
		if _, seed := ks.configSeeds[sk.KeyHash]; seed {
			ks.dropHash(sk.KeyHash, "hash_collision")
			slog.Error("synced key refused", "id", sk.ID, "reason", "hash_collision")
			return true
		}
		if requireOwner && !personMetadata(sk.Metadata) {
			return true
		}
	}
	return false
}
