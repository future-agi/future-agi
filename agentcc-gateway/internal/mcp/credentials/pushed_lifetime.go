package credentials

import (
	"context"
	"crypto/sha256"
	"errors"
	"strconv"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/redis/go-redis/v9"
)

// A pushed grant carries the control plane's revision for its server's shared
// credential slot. Redis keeps, per slot, the highest revision an eviction has
// revoked. Admission only compares a revision with that value: it never records
// an identity or adopts an epoch, so a stale snapshot (a cold replica, or a
// first admission delayed across the eviction) cannot authorize itself after an
// eviction that names its revision, and a refused admission leaves nothing in
// Redis that authorizes a retry. The admitted high-water mark only lets a
// revision-less purge revoke every grant admitted so far.
func (c *TokenCache) pushedRevokedKey(k Key) string {
	return c.prefix + cacheKeyPrefix + "{" + k.OrgID + "}:#pushed-revoked:" + k.ServerID
}

func (c *TokenCache) pushedAdmittedKey(k Key) string {
	return c.prefix + cacheKeyPrefix + "{" + k.OrgID + "}:#pushed-admitted:" + k.ServerID
}

var admitPushed = redis.NewScript(`
if tonumber(ARGV[1]) <= tonumber(redis.call('GET', KEYS[1]) or '0') then return 0 end
if tonumber(ARGV[1]) > tonumber(redis.call('GET', KEYS[2]) or '0') then
 redis.call('SET', KEYS[2], ARGV[1])
end
return 1
`)

// Raising the value is monotonic, so a delayed or repeated eviction can never
// lower it, and it never touches a newer revision. The message is a wakeup.
var revokePushed = redis.NewScript(`
if tonumber(ARGV[1]) > tonumber(redis.call('GET', KEYS[1]) or '0') then
 redis.call('SET', KEYS[1], ARGV[1])
end
redis.call('PUBLISH', ARGV[2], KEYS[1])
return 1
`)

func (c *TokenCache) admitPushed(ctx context.Context, k Key, revision int64) (bool, error) {
	var admitted int64
	err := c.do(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		var err error
		admitted, err = admitPushed.Run(ctx, rdb, []string{c.pushedRevokedKey(k), c.pushedAdmittedKey(k)}, revision).Int64()
		return err
	})
	return admitted == 1, err
}

func (c *TokenCache) revokePushed(ctx context.Context, k Key, through int64) error {
	return c.do(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		return revokePushed.Run(ctx, rdb, []string{c.pushedRevokedKey(k)}, through, c.invalidationChannel()).Err()
	})
}

// pushedLive is the completion fence: one read, ordered against every
// eviction's write on any process.
func (c *TokenCache) pushedLive(ctx context.Context, k Key, revision int64) (bool, error) {
	through := int64(0)
	err := c.do(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		v, err := rdb.Get(ctx, c.pushedRevokedKey(k)).Result()
		if errors.Is(err, redis.Nil) {
			return nil
		}
		if err != nil {
			return err
		}
		through, err = strconv.ParseInt(v, 10, 64)
		return err
	})
	return err == nil && revision > through, err
}

// pushedStaleLocked refuses a revision this process knows to be revoked or
// superseded before any lifetime changes: a stale retry can never replace,
// and so cancel, the lifetime of an admitted successor.
func (u *userLifetime) pushedStaleLocked(revision int64, fp [32]byte) bool {
	return revision <= u.pushedRevoked || revision < u.pushedRevision ||
		(revision == u.pushedRevision && fp != u.pushedFP)
}

// PushedCredential binds a shared org or supplied-server grant before any
// factory runs. The original auth/template remains in the client config; this
// override contributes only the distributed lifetime and completion fence.
func (r *Resolver) PushedCredential(ctx context.Context, org, server string, g mcp.PushedGrant) (*mcp.AuthOverride, error) {
	k := SharedKey(org, server)
	if !k.valid() || g.Identity == "" || g.Revision < 0 {
		return nil, ErrBadKey
	}
	if !g.Expiry.IsZero() && !r.now().Before(g.Expiry) {
		return nil, &Refusal{Reason: RefusalExpired}
	}
	if g.Revision == 0 {
		if r.cache != nil {
			// Unregistered: no eviction could ever name it, so a replica that
			// never admitted it could not be fenced. It is not authorized.
			return nil, &Refusal{Reason: RefusalNeedsReauth}
		}
		return r.localPushed(ctx, k, g)
	}
	fp := sha256.Sum256([]byte(g.Identity))
	u := r.users.get(k)
	u.mu.Lock()
	stale := u.pushedStaleLocked(g.Revision, fp)
	u.mu.Unlock()
	if stale {
		return nil, &Refusal{Reason: RefusalNeedsReauth}
	}
	if r.cache != nil {
		admitted, err := r.cache.admitPushed(ctx, k, g.Revision)
		if err != nil {
			return nil, err
		}
		if !admitted {
			return nil, &Refusal{Reason: RefusalNeedsReauth}
		}
	}
	e := personalEpoch{revision: g.Revision}
	u.mu.Lock()
	// A newer grant admitted, or a local eviction, while Redis answered wins.
	if u.pushedStaleLocked(g.Revision, fp) {
		u.mu.Unlock()
		return nil, &Refusal{Reason: RefusalNeedsReauth}
	}
	u.pushedRevision, u.pushedFP = g.Revision, fp
	auth, fresh, cancel := r.lifetimeLocked(k, fp, g.Expiry, e, u)
	u.mu.Unlock()
	return r.watchGrant(ctx, k, e, auth, fresh, cancel)
}

// localPushed serves a revision-less grant without a distributed authority:
// the process's own purge fences it, and an identity it has seen is never
// rebound to a later local generation.
func (r *Resolver) localPushed(ctx context.Context, k Key, g mcp.PushedGrant) (*mcp.AuthOverride, error) {
	u := r.users.get(k)
	u.mu.Lock()
	if prior, ok := u.pushed[g.Identity]; ok && prior != u.generation {
		u.mu.Unlock()
		return nil, &Refusal{Reason: RefusalNeedsReauth}
	}
	if u.pushed == nil {
		u.pushed = map[string]uint64{}
	}
	u.pushed[g.Identity] = u.generation
	e := personalEpoch{user: "0"}
	auth, fresh, cancel := r.lifetimeLocked(k, sha256.Sum256([]byte(g.Identity)), g.Expiry, e, u)
	u.mu.Unlock()
	return r.watchGrant(ctx, k, e, auth, fresh, cancel)
}

// RevokePushedGrants is mcp.PushedGrantRevoker. Local grants at or below the
// revision stop at once; the Redis value stops them on every other replica
// (message, else the poll) and refuses their admission anywhere, including
// on a replica which has never seen them. No lock is held across Redis.
func (r *Resolver) RevokePushedGrants(ctx context.Context, org, server string, through int64) error {
	k := SharedKey(org, server)
	if !k.valid() || through <= 0 {
		return ErrBadKey
	}
	u := r.users.get(k)
	u.mu.Lock()
	u.pushedRevoked = max(u.pushedRevoked, through)
	if u.epoch.revision > 0 && u.epoch.revision <= through && u.cancel != nil {
		u.cancel()
	}
	u.mu.Unlock()
	if r.cache == nil {
		return nil
	}
	if err := r.cache.revokePushed(ctx, k, through); err != nil {
		return err
	}
	r.cache.checkLifetimes()
	return nil
}
