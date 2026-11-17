package auth

import (
	"sync"
	"time"
)

// DelegationSnapshot is the additive "delegation" field on the control-plane
// key-list response. It MUST include live people with zero API keys, read from
// the authoritative database. See api_contracts/delegation/v1/bulk_snapshot.json.
// FetchedAt is the producer's observation time; local freshness starts when
// this gateway APPLIES the response, not before a potentially slow HTTP fetch.
type DelegationSnapshot struct {
	Complete      bool              `json:"complete"`
	FetchedAt     time.Time         `json:"fetched_at"`
	Members       []DelegatedMember `json:"members"`
	RevokedGrants []string          `json:"revoked_grants"`
}
type DelegatedMember struct {
	Org       string `json:"org_id"`
	User      string `json:"user_id"`
	Workspace string `json:"workspace_id"` // "-" means org-wide membership
}
type delegatedEligibility struct {
	mu          sync.RWMutex
	requestedAt time.Time // Orders overlapping fetches independently of freshness.
	appliedAt   time.Time
	interval    time.Duration
	complete    bool
	members     map[DelegatedMember]bool
	revoked     map[string]bool
}

// DelegationRejection is safe for batch readback: no claims or credentials.
type DelegationRejection string

const (
	DelegationInvalid       DelegationRejection = "delegation_invalid"
	DelegationExpired       DelegationRejection = "delegation_expired"
	DelegationUnavailable   DelegationRejection = "delegation_snapshot_unavailable"
	DelegationStale         DelegationRejection = "delegation_snapshot_stale"
	DelegationRevoked       DelegationRejection = "delegation_grant_revoked"
	DelegationMemberRemoved DelegationRejection = "delegation_member_removed"
)

// SetDelegationSyncInterval follows the control-plane schedule. A failed sync
// may use the last complete snapshot for 2 intervals (at most 150s), inclusive.
// This is outage tolerance, NOT a revocation delay: each successful sync applies
// removals immediately, preserving the next-sync bound (interval <= 60s).
func (ks *KeyStore) SetDelegationSyncInterval(interval time.Duration) {
	if interval <= 0 || interval > time.Minute {
		interval = time.Minute
	}
	ks.delegation.mu.Lock()
	ks.delegation.interval = interval
	ks.delegation.mu.Unlock()
}

// SyncDelegations applies an in-process snapshot at appliedAt. HTTP sync uses
// separate request/application clocks to prevent late old fetches from
// resurrecting revoked membership while giving healthy responses headroom.
func (ks *KeyStore) SyncDelegations(snapshot *DelegationSnapshot, appliedAt time.Time) {
	ks.syncDelegations(snapshot, appliedAt, appliedAt)
}
func (ks *KeyStore) syncDelegations(snapshot *DelegationSnapshot, requestedAt, appliedAt time.Time) {
	members := make(map[DelegatedMember]bool)
	revoked := make(map[string]bool)
	complete := snapshot != nil && snapshot.Complete
	if complete {
		for _, m := range snapshot.Members {
			if !uuidRE.MatchString(m.Org) || !uuidRE.MatchString(m.User) || (m.Workspace != "-" && !uuidRE.MatchString(m.Workspace)) {
				complete = false
				break
			}
			members[m] = true
		}
		for _, id := range snapshot.RevokedGrants {
			if !nonceRE.MatchString(id) {
				complete = false
				break
			}
			revoked[id] = true
		}
	}
	d := &ks.delegation
	d.mu.Lock()
	defer d.mu.Unlock()
	if requestedAt.Before(d.requestedAt) {
		return
	}
	d.requestedAt, d.appliedAt = requestedAt, appliedAt
	d.complete, d.members, d.revoked = complete, members, revoked
}

// DelegatedEligible is checked before EVERY queued item, not only at submit.
func (ks *KeyStore) DelegatedEligible(c EnvelopeClaims, now time.Time) bool {
	return ks.DelegationRejection(c, now) == ""
}
func (ks *KeyStore) DelegationRejection(c EnvelopeClaims, now time.Time) DelegationRejection {
	if c.Kind != "batch" || c.Mode != "grant" || c.Aud != "fa-eval-grant" || c.User == "-" || now.Unix() < c.Iat-5 {
		return DelegationInvalid
	}
	if now.Unix() > c.Exp {
		return DelegationExpired
	}
	if ks == nil {
		return DelegationUnavailable
	}
	d := &ks.delegation
	d.mu.RLock()
	defer d.mu.RUnlock()
	if !d.complete || d.appliedAt.IsZero() {
		return DelegationUnavailable
	}
	interval := d.interval
	if interval <= 0 {
		interval = time.Minute
	}
	if now.Before(d.appliedAt) || now.Sub(d.appliedAt) > min(2*interval, 150*time.Second) {
		return DelegationStale
	}
	if d.revoked[c.ID] {
		return DelegationRevoked
	}
	if !d.members[DelegatedMember{Org: c.Org, User: c.User, Workspace: "-"}] || (c.Workspace != "-" && !d.members[DelegatedMember{Org: c.Org, User: c.User, Workspace: c.Workspace}]) {
		return DelegationMemberRemoved
	}
	return ""
}
