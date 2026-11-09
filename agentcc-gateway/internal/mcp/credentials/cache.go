package credentials

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"regexp"
	"sync"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// Key names one cached upstream token: one user of one org on one server,
// or, with Shared, the org's own signed-in account on one server (CN24),
// which names no user.
type Key struct {
	OrgID    string
	UserID   string
	ServerID string
	Shared   bool
}

// SharedKey is the key of orgID's signed-in account on serverID.
func SharedKey(orgID, serverID string) Key {
	return Key{OrgID: orgID, ServerID: serverID, Shared: true}
}

// sharedSlot is a shared key's user part in the cache: '#' is in no user id,
// so no user's key names it.
const sharedSlot = "#shared"

// Identifier shapes for the cache key mtok:{<org>}:<user>:<server> (MC.6).
// Each field is drawn from a set that cannot be read as a key separator or a
// hash tag, so one caller's key never names another's.
var (
	orgIDPattern    = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`)
	userIDPattern   = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._@|+=-]{0,127}$`)
	serverIDPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9._-]{0,63}$`)
)

// valid reports whether every field of k has its identifier shape: a user's
// key names its user, and the org's shared key names none.
func (k Key) valid() bool {
	user := userIDPattern.MatchString(k.UserID)
	if k.Shared {
		user = k.UserID == ""
	}
	return orgIDPattern.MatchString(k.OrgID) && user && serverIDPattern.MatchString(k.ServerID)
}

// Token is a user's upstream access token as Django handed it out. It never
// prints its value: String, GoString and LogValue name only its expiry and
// version.
type Token struct {
	AccessToken       string
	ExpiresAt         time.Time
	CredentialVersion int
}

func (t Token) String() string {
	return fmt.Sprintf("Token{expires_at=%s, version=%d}", t.ExpiresAt.UTC().Format(time.RFC3339), t.CredentialVersion)
}

// GoString hides the value from %#v as well.
func (t Token) GoString() string { return t.String() }

// LogValue hides the value from slog.
func (t Token) LogValue() slog.Value { return slog.StringValue(t.String()) }

// Cache timing (MC.6). A token is cached until RefreshBefore ahead of its
// expiry, and never longer than DefaultMaxCacheTTL, so a credential revoked
// in Django stops being used within that bound even if the purge that
// follows a revoke never reaches this gateway.
const (
	RefreshBefore      = 60 * time.Second
	DefaultMaxCacheTTL = 5 * time.Minute
	// cacheOpTimeout bounds each Redis call.
	cacheOpTimeout = 500 * time.Millisecond
	// cacheKeyPrefix is MC.6's key family for per-user upstream tokens.
	cacheKeyPrefix = "mtok:"
)

// TokenCache keeps users' upstream tokens in Redis under MC.6's
// mtok:{<org>}:<user>:<server>, sealed with the state codec's token_cache
// purpose. The sealed payload names the org, user and server it was sealed
// for, and Get returns it only for that same key: a value copied under
// another user's or org's key opens for nobody. Beside the tokens, the set
// mtok:{<org>}:#users:<server> names the users with a token cached on that
// server ('#' is in no user id), so a purge of every user's tokens reads one
// set in the org's slot instead of scanning the keyspace.
type TokenCache struct {
	client  *redisstate.Client
	codec   *mcp.StateCodec
	prefix  string
	now     func() time.Time
	maxTTL  time.Duration
	watchMu sync.Mutex
	watcher *epochWatcher
}

// CacheOptions tune a TokenCache. The zero value is production's.
type CacheOptions struct {
	// Prefix goes ahead of every key ("" keeps MC.6's names; tests isolate
	// themselves with one).
	Prefix string
	// MaxTTL caps how long a token is cached (DefaultMaxCacheTTL when 0).
	MaxTTL time.Duration
	// Now is the clock that decides freshness (time.Now when nil).
	Now func() time.Time
}

// NewTokenCache returns the cache on client, sealing with codec.
func NewTokenCache(client *redisstate.Client, codec *mcp.StateCodec, o CacheOptions) *TokenCache {
	if o.Now == nil {
		o.Now = time.Now
	}
	if o.MaxTTL <= 0 {
		o.MaxTTL = DefaultMaxCacheTTL
	}
	return &TokenCache{client: client, codec: codec, prefix: o.Prefix, now: o.Now, maxTTL: o.MaxTTL}
}

// RedisKey is k's key: mtok:{<org>}:<user>:<server>, the org as hash tag;
// the org's shared key is mtok:{<org>}:#shared:<server>.
func (c *TokenCache) RedisKey(k Key) string {
	user := k.UserID
	if k.Shared {
		user = sharedSlot
	}
	return c.prefix + cacheKeyPrefix + "{" + k.OrgID + "}:" + user + ":" + k.ServerID
}

// indexKey is the set of users with a token cached on k's server.
func (c *TokenCache) indexKey(orgID, serverID string) string {
	return c.prefix + cacheKeyPrefix + "{" + orgID + "}:#users:" + serverID
}

// sealedToken is the sealed payload: the token and the key it belongs to.
type sealedToken struct {
	V                 int    `json:"v"`
	OrgID             string `json:"org_id"`
	UserID            string `json:"user_id"`
	ServerID          string `json:"server_id"`
	Shared            bool   `json:"shared,omitempty"`
	AccessToken       string `json:"access_token"`
	ExpiresAtMs       int64  `json:"expires_at_ms"`
	CredentialVersion int    `json:"credential_version"`
}

// ErrBadKey is an org, user or server id that has no valid shape; it is
// mcp.ErrInvalidCredentialKey, so the purge route answers it as a bad request.
var ErrBadKey = mcp.ErrInvalidCredentialKey

// Get returns k's cached token while it is fresh: more than RefreshBefore
// ahead of its expiry. A missing, stale, unreadable or misplaced value is a
// miss, and a value that is not k's own is deleted.
func (c *TokenCache) Get(ctx context.Context, k Key) (Token, bool, error) {
	if !k.valid() {
		return Token{}, false, ErrBadKey
	}
	var sealed string
	err := c.do(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		v, err := rdb.Get(ctx, c.RedisKey(k)).Result()
		if errors.Is(err, redis.Nil) {
			return nil
		}
		sealed = v
		return err
	})
	if err != nil || sealed == "" {
		return Token{}, false, err
	}
	tok, ok := c.open(k, sealed)
	if !ok {
		c.drop(ctx, k)
		return Token{}, false, nil
	}
	if !c.now().Before(tok.ExpiresAt.Add(-RefreshBefore)) {
		return Token{}, false, nil
	}
	return tok, true, nil
}

// open unseals one value and returns its token only when it is k's own.
func (c *TokenCache) open(k Key, sealed string) (Token, bool) {
	raw, err := c.codec.Open(mcp.StatePurposeTokenCache, sealed)
	if err != nil {
		return Token{}, false
	}
	var p sealedToken
	if json.Unmarshal(raw, &p) != nil || p.V != 1 {
		return Token{}, false
	}
	if p.OrgID != k.OrgID || p.UserID != k.UserID || p.ServerID != k.ServerID || p.Shared != k.Shared || p.AccessToken == "" {
		return Token{}, false
	}
	return Token{AccessToken: p.AccessToken, ExpiresAt: time.UnixMilli(p.ExpiresAtMs), CredentialVersion: p.CredentialVersion}, true
}

// TTL is how long tok may be cached from now: until RefreshBefore ahead of
// its expiry, at most the cache's cap. Zero or less is not cacheable.
func (c *TokenCache) TTL(tok Token) time.Duration {
	return min(tok.ExpiresAt.Sub(c.now())-RefreshBefore, c.maxTTL)
}

// Put caches tok for k, sealed, for TTL(tok). A token too close to its
// expiry is not cached.
func (c *TokenCache) Put(ctx context.Context, k Key, tok Token) error {
	if !k.valid() {
		return ErrBadKey
	}
	sealed, ttl, err := c.seal(k, tok)
	if err != nil {
		return err
	}
	if ttl <= 0 {
		return nil
	}
	if k.Shared {
		// Found by its fixed key, never through the users' index.
		return c.do(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
			return rdb.Set(ctx, c.RedisKey(k), sealed, ttl).Err()
		})
	}
	index := c.indexKey(k.OrgID, k.ServerID)
	return c.do(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		_, err := rdb.TxPipelined(ctx, func(p redis.Pipeliner) error {
			p.Set(ctx, c.RedisKey(k), sealed, ttl)
			p.SAdd(ctx, index, k.UserID)
			// The set outlives every token cached on the server so far: each
			// is cached for at most maxTTL from its own Put.
			p.PExpire(ctx, index, c.maxTTL)
			return nil
		})
		return err
	})
}

// seal prepares the cache payload for ordinary writes and epoch-fenced writes.
func (c *TokenCache) seal(k Key, tok Token) (string, time.Duration, error) {
	ttl := c.TTL(tok)
	if ttl <= 0 || tok.AccessToken == "" {
		return "", 0, nil
	}
	raw, err := json.Marshal(sealedToken{V: 1, OrgID: k.OrgID, UserID: k.UserID, ServerID: k.ServerID, Shared: k.Shared,
		AccessToken: tok.AccessToken, ExpiresAtMs: tok.ExpiresAt.UnixMilli(), CredentialVersion: tok.CredentialVersion})
	if err != nil {
		return "", 0, err
	}
	sealed, err := c.codec.Seal(mcp.StatePurposeTokenCache, raw, ttl)
	return sealed, ttl, err
}

// Purge drops the cached tokens of serverID in orgID: userID's only, or
// every user's and the org's shared one when userID is "" (the users the
// index names). It returns how many it dropped. Only the purged users leave
// the index, so a token cached while the purge runs stays findable by the
// next one.
func (c *TokenCache) Purge(ctx context.Context, orgID, userID, serverID string) (int, error) {
	if !orgIDPattern.MatchString(orgID) || !serverIDPattern.MatchString(serverID) || (userID != "" && !userIDPattern.MatchString(userID)) {
		return 0, ErrBadKey
	}
	k := Key{OrgID: orgID, UserID: userID, ServerID: serverID}
	index := c.indexKey(orgID, serverID)
	var n int64
	err := c.do(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		var err error
		if userID == "" {
			shared := SharedKey(orgID, serverID)
			n, err = purgeServer.Run(ctx, rdb, []string{
				c.sharedEpochKey(k), c.personalServerEpochKey(k), c.RedisKey(shared), index,
				c.pushedRevokedKey(shared), c.pushedAdmittedKey(shared),
			}, c.prefix+cacheKeyPrefix+"{"+orgID+"}:", serverID, c.invalidationChannel()).Int64()
		} else {
			n, err = purgePersonal.Run(ctx, rdb, []string{c.personalEpochKey(k), c.RedisKey(k), index}, userID, c.invalidationChannel()).Int64()
		}
		return err
	})
	return int(n), err
}

// drop deletes k's value, best effort.
func (c *TokenCache) drop(ctx context.Context, k Key) {
	_ = c.do(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		return rdb.Del(ctx, c.RedisKey(k)).Err()
	})
}

var errNoCacheRedis = errors.New("mcp credentials: no Redis client")

// do runs fn bounded by cacheOpTimeout and detached from the caller's
// cancellation, so a client that went away does not count against the
// breaker every Redis store shares.
func (c *TokenCache) do(ctx context.Context, fn func(ctx context.Context, rdb redis.UniversalClient) error) error {
	if c == nil || c.client == nil {
		return errNoCacheRedis
	}
	ctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), cacheOpTimeout)
	defer cancel()
	return c.client.Do(func(rdb redis.UniversalClient) error { return fn(ctx, rdb) })
}
