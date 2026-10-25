package reqopts

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"log/slog"
	"os"
	"sync"
	"sync/atomic"
)

// upstreamUserEnv is the environment form of managed.upstream_user_hash_key.
const upstreamUserEnv = "AGENTCC_MANAGED_USER_HASH_KEY"

// userKeyState is the key that hashes org ids into the upstream user. nil
// key means the unkeyed fallback.
type userKeyState struct {
	key []byte
}

var (
	userKey     atomic.Pointer[userKeyState]
	userKeyLazy sync.Once
)

// ConfigureUpstreamUser sets the key that hashes org ids into the upstream
// `user` of managed attempts, from the loaded config's
// managed.upstream_user_hash_key (whose env form the config loader has
// already applied). It always wins over the lazy environment read
// UpstreamUser falls back to. An empty key logs a warning and selects the
// unkeyed fallback.
func ConfigureUpstreamUser(key string) {
	userKey.Store(newUserKeyState(key))
}

func newUserKeyState(key string) *userKeyState {
	if key == "" {
		slog.Warn("managed.upstream_user_hash_key is not set: managed requests send an unkeyed sha256 of the org id as the upstream user; set " + upstreamUserEnv)
		return &userKeyState{}
	}
	return &userKeyState{key: []byte(key)}
}

// UpstreamUser is the `user` a managed attempt sends upstream for orgID, in
// place of the caller's: "fa_org_" and the first 24 hex digits of
// HMAC-SHA256(key, org id), or of SHA-256("agentcc:" + org id) when no key is
// configured. Until ConfigureUpstreamUser runs, the key comes from
// AGENTCC_MANAGED_USER_HASH_KEY, read once.
func UpstreamUser(orgID string) string {
	st := userKey.Load()
	if st == nil {
		userKeyLazy.Do(func() { userKey.CompareAndSwap(nil, newUserKeyState(os.Getenv(upstreamUserEnv))) })
		st = userKey.Load()
	}
	return hashOrgID(st.key, orgID)
}

func hashOrgID(key []byte, orgID string) string {
	var sum []byte
	if key != nil {
		m := hmac.New(sha256.New, key)
		m.Write([]byte(orgID))
		sum = m.Sum(nil)
	} else {
		s := sha256.Sum256([]byte("agentcc:" + orgID))
		sum = s[:]
	}
	return "fa_org_" + hex.EncodeToString(sum)[:24]
}
