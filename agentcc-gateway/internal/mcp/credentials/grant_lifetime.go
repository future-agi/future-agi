package credentials

import (
	"context"
	"crypto/sha256"
	"fmt"
	"net/http"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// grantLocked binds token identity and distributed epoch to one lifetime. The
// caller releases u.mu before starting the watcher or performing a check.
func (r *Resolver) grantLocked(k Key, tok Token, epoch personalEpoch, u *userLifetime) (*mcp.AuthOverride, bool, context.CancelFunc) {
	fp := sha256.Sum256([]byte(fmt.Sprintf("%d\x00%s", tok.CredentialVersion, tok.AccessToken)))
	auth, fresh, cancel := r.lifetimeLocked(k, fp, tok.ExpiresAt, epoch, u)
	auth.Header = http.Header{"Authorization": {"Bearer " + tok.AccessToken}}
	auth.Version = tok.CredentialVersion
	return auth, fresh, cancel
}

// lifetimeLocked also serves pushed credentials, which may have no expiry and
// whose placement remains in the pushed configuration, never the token cache.
func (r *Resolver) lifetimeLocked(k Key, fp [32]byte, expiry time.Time, epoch personalEpoch, u *userLifetime) (*mcp.AuthOverride, bool, context.CancelFunc) {
	// The sealed cache keeps milliseconds. A cache hit on an unchanged grant
	// must not read as a shorter deadline and cancel its own live lifetime.
	if !expiry.IsZero() {
		expiry = expiry.Truncate(time.Millisecond)
	}
	shorter := !expiry.IsZero() && (u.expires.IsZero() || expiry.Before(u.expires))
	fresh := u.life == nil || u.life.Err() != nil || u.fingerprint != fp || u.epoch != epoch || shorter
	if fresh {
		if u.cancel != nil {
			u.cancel()
		}
		if expiry.IsZero() {
			u.life, u.cancel = context.WithCancel(context.Background())
		} else {
			u.life, u.cancel = context.WithTimeout(context.Background(), expiry.Sub(r.now()))
		}
		u.expires, u.fingerprint, u.epoch = expiry, fp, epoch
	}
	life, cancel, expires := u.life, u.cancel, u.expires
	check := func(ctx context.Context) error {
		if life.Err() != nil || (!expires.IsZero() && !r.now().Before(expires)) {
			cancel()
			return mcp.CredentialRequired(k.ServerID, "needs_reauth")
		}
		if r.cache != nil {
			var accepted bool
			var err error
			if epoch.revision > 0 {
				accepted, err = r.cache.pushedLive(ctx, k, epoch.revision)
			} else if k.Shared {
				accepted, err = r.cache.publishShared(ctx, k, Token{}, epoch.user, false)
			} else {
				accepted, err = r.cache.publishPersonal(ctx, k, Token{}, epoch, false)
			}
			if err != nil || !accepted {
				cancel()
				return mcp.CredentialRequired(k.ServerID, "needs_reauth")
			}
		}
		if life.Err() != nil || (!expires.IsZero() && !r.now().Before(expires)) {
			cancel()
			return mcp.CredentialRequired(k.ServerID, "needs_reauth")
		}
		return nil
	}
	return &mcp.AuthOverride{Lifetime: life, ExpiresAt: expires, Check: check}, fresh, cancel
}

func (r *Resolver) watchGrant(ctx context.Context, k Key, epoch personalEpoch, auth *mcp.AuthOverride, fresh bool, cancel context.CancelFunc) (*mcp.AuthOverride, error) {
	if fresh && r.cache != nil {
		r.cache.watchEpoch(auth.Lifetime, k, epoch, cancel)
	}
	if err := auth.Check(ctx); err != nil {
		return nil, &Refusal{Reason: RefusalNeedsReauth}
	}
	return auth, nil
}

// Credential pins the organization's current grant for construction and every
// later operation on that connection. A retained client cannot refresh itself
// into a different account; reconnect builds a separate client instead.
func (s sharedSource) Credential(ctx context.Context) (*mcp.AuthOverride, error) {
	if !s.k.valid() {
		return nil, ErrBadKey
	}
	u := s.r.users.get(s.k)
	u.mu.Lock()
	generation := u.generation
	u.next++
	sequence := u.next
	u.mu.Unlock()
	epoch := "0"
	if s.r.cache != nil {
		var err error
		epoch, err = s.r.cache.sharedEpoch(ctx, s.k)
		if err != nil {
			return nil, err
		}
	}
	tok, err := s.r.token(ctx, s.k)
	if err != nil {
		return nil, err
	}
	// The pre-fetch epoch deliberately cannot adopt an intervening purge.
	if _, err = s.r.publishSharedToken(ctx, s.k, tok, u, generation, epoch, false); err != nil {
		return nil, err
	}
	e := personalEpoch{user: epoch}
	fp := sha256.Sum256([]byte(fmt.Sprintf("%d\x00%s", tok.CredentialVersion, tok.AccessToken)))
	u.mu.Lock()
	if u.generation != generation || (sequence < u.applied && (u.epoch != e || u.fingerprint != fp)) {
		u.mu.Unlock()
		return nil, &Refusal{Reason: RefusalNeedsReauth}
	}
	u.applied = max(u.applied, sequence)
	auth, fresh, cancel := s.r.grantLocked(s.k, tok, e, u)
	u.mu.Unlock()
	return s.r.watchGrant(ctx, s.k, e, auth, fresh, cancel)
}
