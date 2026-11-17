package ext

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"sort"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-mockup/mock"
)

// provisionedKey is a key made with POST /api/v1/keys.
type provisionedKey struct {
	owner, secret, hash string // owner: the bearer that provisioned it
	seq                 int    // creation order, for listing
	createdAt           time.Time
	updatedAt           time.Time
	keySettings
}

// keyStore holds the provisioned keys. Usage is never stored: it is read from
// the mock's charges, by bearer, whenever it is reported.
type keyStore struct {
	state    *mock.State
	mu       sync.Mutex
	seq      int
	byHash   map[string]*provisionedKey
	bySecret map[string]*provisionedKey
}

func newKeyStore(state *mock.State) *keyStore {
	ks := &keyStore{state: state}
	ks.reset()
	return ks
}

// reset forgets every provisioned key (POST /__reset).
func (ks *keyStore) reset() {
	ks.mu.Lock()
	defer ks.mu.Unlock()
	ks.byHash, ks.bySecret = map[string]*provisionedKey{}, map[string]*provisionedKey{}
}

// handleCurrent serves GET /api/v1/key: the calling key's usage and limit.
func (ks *keyStore) handleCurrent(w http.ResponseWriter, r *http.Request) {
	bearer := requireBearer(w, r)
	if bearer == "" {
		return
	}
	usage := ks.state.ChargesByBearer()[bearer]
	var limit *float64 // a provisioned key's; any other bearer has none
	ks.mu.Lock()
	if k := ks.bySecret[bearer]; k != nil {
		limit = k.Limit
	}
	ks.mu.Unlock()
	writeJSON(w, http.StatusOK, map[string]any{"data": map[string]any{
		"label": maskKey(bearer), "usage": microsToUSD(usage), "limit": limit,
		"limit_remaining": limitRemaining(limit, usage), "is_free_tier": false,
	}})
}

// handleCreate serves POST /api/v1/keys: 201 with the key's view and, this
// once, the key itself.
func (ks *keyStore) handleCreate(w http.ResponseWriter, r *http.Request) {
	bearer := requireBearer(w, r)
	if bearer == "" {
		return
	}
	var settings keySettings
	if err := settings.apply(r.Body, true); err != nil {
		writeError(w, http.StatusBadRequest, "%v", err)
		return
	}
	secret := newSecret()
	now := time.Now().UTC()
	charges := ks.state.ChargesByBearer()
	ks.mu.Lock()
	ks.seq++
	k := &provisionedKey{owner: bearer, secret: secret, hash: hashKey(secret), seq: ks.seq, createdAt: now, updatedAt: now, keySettings: settings}
	ks.byHash[k.hash], ks.bySecret[secret] = k, k
	view := ks.viewLocked(k, charges)
	ks.mu.Unlock()
	writeJSON(w, http.StatusCreated, map[string]any{"data": view, "key": secret})
}

// handleList serves GET /api/v1/keys: the caller's keys, oldest first.
func (ks *keyStore) handleList(w http.ResponseWriter, r *http.Request) {
	bearer := requireBearer(w, r)
	if bearer == "" {
		return
	}
	charges := ks.state.ChargesByBearer()
	ks.mu.Lock()
	var mine []*provisionedKey
	for _, k := range ks.byHash {
		if k.owner == bearer {
			mine = append(mine, k)
		}
	}
	sort.Slice(mine, func(i, j int) bool { return mine[i].seq < mine[j].seq })
	views := make([]any, 0, len(mine))
	for _, k := range mine {
		views = append(views, ks.viewLocked(k, charges))
	}
	ks.mu.Unlock()
	writeJSON(w, http.StatusOK, map[string]any{"data": views})
}

// handleGet serves GET /api/v1/keys/{hash}.
func (ks *keyStore) handleGet(w http.ResponseWriter, r *http.Request) {
	ks.withOwnKey(w, r, func(k *provisionedKey, charges map[string]int64) {
		writeJSON(w, http.StatusOK, map[string]any{"data": ks.viewLocked(k, charges)})
	})
}

// handleUpdate serves PATCH /api/v1/keys/{hash}: the fields sent change.
func (ks *keyStore) handleUpdate(w http.ResponseWriter, r *http.Request) {
	ks.withOwnKey(w, r, func(k *provisionedKey, charges map[string]int64) {
		if err := k.apply(r.Body, false); err != nil {
			writeError(w, http.StatusBadRequest, "%v", err)
			return
		}
		k.updatedAt = time.Now().UTC()
		writeJSON(w, http.StatusOK, map[string]any{"data": ks.viewLocked(k, charges)})
	})
}

// handleDelete serves DELETE /api/v1/keys/{hash}.
func (ks *keyStore) handleDelete(w http.ResponseWriter, r *http.Request) {
	ks.withOwnKey(w, r, func(k *provisionedKey, _ map[string]int64) {
		delete(ks.byHash, k.hash)
		delete(ks.bySecret, k.secret)
		writeJSON(w, http.StatusOK, map[string]any{"deleted": true})
	})
}

// withOwnKey runs fn, under the store's lock, on the key the path names when
// the caller provisioned it; any other key is 404, as if it did not exist.
func (ks *keyStore) withOwnKey(w http.ResponseWriter, r *http.Request, fn func(*provisionedKey, map[string]int64)) {
	bearer := requireBearer(w, r)
	if bearer == "" {
		return
	}
	charges := ks.state.ChargesByBearer()
	ks.mu.Lock()
	defer ks.mu.Unlock()
	k := ks.byHash[r.PathValue("hash")]
	if k == nil || k.owner != bearer {
		writeError(w, http.StatusNotFound, "API key not found")
		return
	}
	fn(k, charges)
}

// viewLocked is a key as OpenRouter reports it; usage is its bearer's charges.
func (ks *keyStore) viewLocked(k *provisionedKey, charges map[string]int64) map[string]any {
	usage := charges[k.secret]
	return map[string]any{
		"hash": k.hash, "name": k.Name, "label": maskKey(k.secret), "disabled": k.Disabled,
		"limit": k.Limit, "limit_remaining": limitRemaining(k.Limit, usage), "limit_reset": k.LimitReset,
		"include_byok_in_limit": k.IncludeBYOK, "usage": microsToUSD(usage),
		"created_at": k.createdAt.Format(time.RFC3339), "updated_at": k.updatedAt.Format(time.RFC3339),
	}
}

// limitRemaining is limit less usage in USD, or nil for a key with no limit.
func limitRemaining(limit *float64, usageMicros int64) any {
	if limit == nil {
		return nil
	}
	return microsToUSD(usdToMicros(*limit) - usageMicros)
}

// maskKey is a key's label: enough of it to recognise, never the whole key.
func maskKey(key string) string {
	if len(key) > 16 {
		return key[:12] + "..." + key[len(key)-4:]
	}
	return key[:min(3, len(key))] + "..."
}

func newSecret() string {
	b := make([]byte, 24)
	if _, err := rand.Read(b); err != nil {
		panic("mock: no randomness for a key: " + err.Error())
	}
	return "sk-or-v1-mock-" + hex.EncodeToString(b)
}

func hashKey(secret string) string {
	sum := sha256.Sum256([]byte(secret))
	return hex.EncodeToString(sum[:])
}
