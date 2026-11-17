package credentials

import (
	"context"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// A user's authorization is checked with Django on each resolution, not with
// the sealed token cache. That cache is transport storage, not vault state.
// Each observed credential version owns a lifetime shared by its pool clients.
// Purge and refusal cancel that lifetime even while an upstream call is active.
type userLifetimes struct {
	mu    sync.Mutex
	users map[Key]*userLifetime
}
type userLifetime struct {
	mu            sync.Mutex
	generation    uint64
	life          context.Context
	cancel        context.CancelFunc
	fingerprint   [32]byte
	expires       time.Time
	epoch         personalEpoch
	flight        *userFetch
	next, applied uint64
	// Revision-less pushed identities (no distributed authority) never adopt
	// a later local fallback generation.
	pushed map[string]uint64
	// The pushed grant revision most recently admitted here, its identity, and
	// the highest revision this process has seen revoked for the slot.
	pushedRevision, pushedRevoked int64
	pushedFP                      [32]byte
}
type userFetch struct {
	epoch                personalEpoch
	refresh              bool
	done                 chan struct{}
	generation, sequence uint64
	token                Token
	err                  error
}

func (u *userLifetimes) get(k Key) *userLifetime {
	u.mu.Lock()
	defer u.mu.Unlock()
	if u.users == nil {
		u.users = map[Key]*userLifetime{}
	}
	if u.users[k] == nil {
		u.users[k] = &userLifetime{}
	}
	return u.users[k]
}
func (u *userLifetime) invalidate() {
	u.generation++
	if u.cancel != nil {
		u.cancel()
	}
	u.life, u.cancel = nil, nil
	// A post-purge request starts a new fetch rather than joining an old one.
	u.flight = nil
}
func (r *Resolver) userAuth(ctx context.Context, k Key) (*mcp.AuthOverride, error) {
	return r.userAuthAttempt(ctx, k, true)
}

func (r *Resolver) userAuthAttempt(ctx context.Context, k Key, retry bool) (*mcp.AuthOverride, error) {
	u := r.users.get(k)
	epoch := personalEpoch{}
	if r.cache != nil {
		var err error
		epoch, err = r.cache.personalEpoch(ctx, k)
		if err != nil {
			return nil, err
		}
	}
	u.mu.Lock()
	f := u.flight
	// Epoch identity also separates flights on replicas that did not purge locally.
	// Revalidation must never join another caller's refresh-enabled fetch.
	owner := f == nil || f.epoch != epoch || (!retry && f.refresh)
	if owner {
		u.next++
		f = &userFetch{done: make(chan struct{}), generation: u.generation, sequence: u.next, refresh: retry, epoch: epoch}
		u.flight = f
	}
	u.mu.Unlock()
	if owner {
		if retry {
			f.token, f.err = r.tokens.Fetch(context.WithoutCancel(ctx), k)
		} else if source, ok := r.tokens.(interface {
			FetchCurrent(context.Context, Key) (Token, error)
		}); ok {
			f.token, f.err = source.FetchCurrent(context.WithoutCancel(ctx), k)
		} else {
			// An authority without a non-refreshing read cannot safely revalidate.
			f.err = &Refusal{Reason: RefusalNeedsReauth}
		}
		u.mu.Lock()
		if u.flight == f {
			u.flight = nil
		}
		close(f.done)
		u.mu.Unlock()
	} else {
		select {
		case <-f.done:
		case <-ctx.Done():
			return nil, ErrTokenChannel
		}
	}
	u.mu.Lock()
	stale := u.generation != f.generation || f.sequence < u.applied
	u.mu.Unlock()
	// The fetch is immutable after done. Redis orders publication with purge;
	// no credential mutex is needed (or held) while the authority responds.
	if !stale && f.err == nil && f.token.ExpiresAt.After(r.now()) && r.cache != nil {
		accepted, err := r.cache.publishPersonal(ctx, k, f.token, f.epoch, true)
		if err != nil {
			return nil, err // An unavailable epoch authority cannot authorize a grant.
		}
		stale = !accepted
	}
	u.mu.Lock()
	stale = stale || u.generation != f.generation || f.sequence < u.applied
	if stale {
		u.mu.Unlock()
		// A refresh can evict the old grant while this fetch is in flight.
		// Never publish that response; read current state once without another
		// refresh. Short-lived refreshed tokens remain usable inside Django's
		// refresh window. A refusal retains its authoritative expiry reason.
		if f.err != nil {
			return nil, f.err
		}
		if retry {
			return r.userAuthAttempt(ctx, k, false)
		}
		return nil, &Refusal{Reason: RefusalNeedsReauth}
	}
	u.applied = f.sequence
	tok, err := f.token, f.err
	if err == nil && !tok.ExpiresAt.After(r.now()) {
		err = &Refusal{Reason: RefusalExpired}
	}
	if err == nil && tok.AccessToken == "" {
		err = ErrTokenChannel
	}
	if err != nil {
		u.invalidate()
		u.mu.Unlock()
		return nil, err
	}
	auth, fresh, cancel := r.grantLocked(k, tok, f.epoch, u)
	u.mu.Unlock()
	return r.watchGrant(ctx, k, f.epoch, auth, fresh, cancel)
}
func (r *Resolver) purgeUserLifetimes(org, user, server string) {
	r.users.mu.Lock()
	var matching []*userLifetime
	for k, u := range r.users.users {
		if k.OrgID == org && k.ServerID == server && (user == "" || k.UserID == user) {
			matching = append(matching, u)
		}
	}
	r.users.mu.Unlock()
	for _, u := range matching {
		u.mu.Lock()
		u.invalidate()
		// A revision-less purge revokes every pushed grant admitted so far.
		u.pushedRevoked = max(u.pushedRevoked, u.pushedRevision)
		u.mu.Unlock()
	}
}
