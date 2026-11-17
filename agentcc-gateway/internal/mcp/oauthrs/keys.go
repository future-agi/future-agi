package oauthrs

import (
	"bytes"
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"sync"
	"time"
)

// The authorization server's verification keys (mcp-oauth-keys-push-v1):
// Django pushes its public signing keys with PUT /-/mcp/oauth/keys whenever
// they change, under a version the gateway applies only when it is newer.
// The set lives in memory and, with Redis, under one global key every
// replica reads, so a push to any replica reaches all of them.

// MaxPushBytes caps a key push's body.
const MaxPushBytes = 64 << 10

// maxPushKeys caps the keys one push may carry.
const maxPushKeys = 16

// Store reloads: a replica rereads the shared set at least every
// reloadEvery, so a key the authorization server dropped stops being accepted
// everywhere within that time, and on a token naming a key it does not hold,
// at most once every reloadMissFloor, so tokens naming made-up keys cannot
// turn into a stream of Redis reads.
const (
	reloadEvery     = 30 * time.Second
	reloadMissFloor = time.Second
)

// ErrInvalidPush is a key push the gateway refuses: its shape, its issuer or
// a key in it. The wrapped message says which, and never repeats a key.
var ErrInvalidPush = errors.New("invalid key push")

// ErrStoreUnavailable is a push the shared store did not take: nothing
// changed, and the authorization server should push again.
var ErrStoreUnavailable = errors.New("the key store is unavailable")

// KeyStore keeps the pushed key set where every replica reads it.
type KeyStore interface {
	// Load returns the stored document, nil when there is none.
	Load(ctx context.Context) ([]byte, error)
	// SaveIfNewer stores doc unless the stored document's version is at
	// least version, and reports whether it did.
	SaveIfNewer(ctx context.Context, version int64, doc []byte) (bool, error)
}

// PushResult answers a push: whether this push changed the set, and the
// version in force after it.
type PushResult struct {
	Applied bool  `json:"applied"`
	Version int64 `json:"version"`
}

// KeySet holds the authorization server's verification keys by kid.
type KeySet struct {
	issuer string
	store  KeyStore
	clock  func() time.Time

	mu       sync.RWMutex
	version  int64
	keys     map[string]*ecdsa.PublicKey
	lastLoad time.Time

	reloadMu sync.Mutex
}

// NewKeySet returns an empty set for issuer, shared through store (nil keeps
// it in this process only). clock defaults to time.Now.
func NewKeySet(issuer string, store KeyStore, clock func() time.Time) *KeySet {
	if clock == nil {
		clock = time.Now
	}
	return &KeySet{issuer: issuer, store: store, clock: clock, keys: map[string]*ecdsa.PublicKey{}}
}

// Version is the version of the set in force, 0 before the first push.
func (k *KeySet) Version() int64 {
	k.mu.RLock()
	defer k.mu.RUnlock()
	return k.version
}

// Push applies a mcp-oauth-keys-push-v1 body when its version is newer than
// the set in force. A body that is not a valid push for this issuer is
// ErrInvalidPush and changes nothing; with a store, the set changes only
// once the store took it (ErrStoreUnavailable otherwise).
func (k *KeySet) Push(ctx context.Context, body []byte) (PushResult, error) {
	doc, keys, err := parsePush(body, k.issuer)
	if err != nil {
		return PushResult{}, err
	}
	k.mu.Lock()
	current := k.version
	k.mu.Unlock()
	if doc.Version <= current {
		return PushResult{Applied: false, Version: current}, nil
	}
	if k.store != nil {
		canonical, err := json.Marshal(doc)
		if err != nil {
			return PushResult{}, fmt.Errorf("%w: %v", ErrInvalidPush, err)
		}
		saved, err := k.store.SaveIfNewer(ctx, doc.Version, canonical)
		if err != nil {
			return PushResult{}, ErrStoreUnavailable
		}
		if !saved {
			// Another replica stored a newer set first: take it.
			_ = k.reload(ctx)
			return PushResult{Applied: false, Version: k.Version()}, nil
		}
	}
	k.mu.Lock()
	defer k.mu.Unlock()
	if doc.Version <= k.version {
		return PushResult{Applied: false, Version: k.version}, nil
	}
	k.version, k.keys = doc.Version, keys
	return PushResult{Applied: true, Version: doc.Version}, nil
}

// Load reads the shared set from the store and applies it when it is newer.
// Without a store it does nothing.
func (k *KeySet) Load(ctx context.Context) error {
	return k.reload(ctx)
}

// Lookup returns the key kid names. It rereads the shared set when the copy
// in memory is older than reloadEvery, or when kid is unknown and the last
// read is older than reloadMissFloor.
func (k *KeySet) Lookup(ctx context.Context, kid string) (*ecdsa.PublicKey, bool) {
	key, ok, due := k.lookup(kid)
	if k.store == nil || !due {
		return key, ok
	}
	k.reloadMu.Lock()
	// Another request may have reread the set while this one waited.
	if _, _, due = k.lookup(kid); due {
		if err := k.reloadLocked(ctx); err != nil {
			slog.Warn("mcp oauth: cannot reread the verification keys; keeping the keys in memory", "error", err)
		}
	}
	k.reloadMu.Unlock()
	key, ok, _ = k.lookup(kid)
	return key, ok
}

// lookup returns the key kid names and whether the shared set is due a
// reread for it.
func (k *KeySet) lookup(kid string) (*ecdsa.PublicKey, bool, bool) {
	k.mu.RLock()
	defer k.mu.RUnlock()
	key, ok := k.keys[kid]
	age := k.clock().Sub(k.lastLoad)
	return key, ok, age >= reloadEvery || (!ok && age >= reloadMissFloor)
}

// reload reads the store once (one reader at a time) and applies a newer
// set. A stored document that is no longer a valid push is ignored.
func (k *KeySet) reload(ctx context.Context) error {
	if k.store == nil {
		return nil
	}
	k.reloadMu.Lock()
	defer k.reloadMu.Unlock()
	return k.reloadLocked(ctx)
}

// reloadLocked is reload with reloadMu held.
func (k *KeySet) reloadLocked(ctx context.Context) error {
	k.mu.Lock()
	k.lastLoad = k.clock()
	k.mu.Unlock()
	raw, err := k.store.Load(ctx)
	if err != nil {
		return err
	}
	if raw == nil {
		return nil
	}
	doc, keys, err := parsePush(raw, k.issuer)
	if err != nil {
		return fmt.Errorf("the stored key set is not valid: %w", err)
	}
	k.mu.Lock()
	defer k.mu.Unlock()
	if doc.Version > k.version {
		k.version, k.keys = doc.Version, keys
	}
	return nil
}

// pushDoc is a mcp-oauth-keys-push-v1 document.
type pushDoc struct {
	Issuer  string `json:"issuer"`
	Version int64  `json:"version"`
	Keys    []jwk  `json:"keys"`
}

// jwk is a public EC key as the push carries it.
type jwk struct {
	Kty string `json:"kty"`
	Crv string `json:"crv"`
	Kid string `json:"kid"`
	Use string `json:"use,omitempty"`
	Alg string `json:"alg,omitempty"`
	X   string `json:"x"`
	Y   string `json:"y"`
}

// privateMembers are the JWK members that only a private key has.
var privateMembers = []string{"d", "p", "q", "k"}

// parsePush checks body as a push for issuer and returns it with its keys.
func parsePush(body []byte, issuer string) (pushDoc, map[string]*ecdsa.PublicKey, error) {
	var raw struct {
		Issuer  string            `json:"issuer"`
		Version int64             `json:"version"`
		Keys    []json.RawMessage `json:"keys"`
	}
	dec := json.NewDecoder(bytes.NewReader(body))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&raw); err != nil {
		return pushDoc{}, nil, fmt.Errorf("%w: the body is not a mcp-oauth-keys-push-v1 document", ErrInvalidPush)
	}
	switch {
	case raw.Issuer != issuer:
		return pushDoc{}, nil, fmt.Errorf("%w: issuer is not the authorization server this gateway trusts", ErrInvalidPush)
	case raw.Version < 1:
		return pushDoc{}, nil, fmt.Errorf("%w: version must be at least 1", ErrInvalidPush)
	case len(raw.Keys) == 0 || len(raw.Keys) > maxPushKeys:
		return pushDoc{}, nil, fmt.Errorf("%w: a push carries 1 to %d keys", ErrInvalidPush, maxPushKeys)
	}
	doc := pushDoc{Issuer: raw.Issuer, Version: raw.Version}
	keys := make(map[string]*ecdsa.PublicKey, len(raw.Keys))
	for i, rk := range raw.Keys {
		k, pub, err := parseJWK(rk)
		if err != nil {
			return pushDoc{}, nil, fmt.Errorf("%w: keys[%d]: %s", ErrInvalidPush, i, err)
		}
		if _, dup := keys[k.Kid]; dup {
			return pushDoc{}, nil, fmt.Errorf("%w: keys[%d]: kid is used twice", ErrInvalidPush, i)
		}
		keys[k.Kid] = pub
		doc.Keys = append(doc.Keys, k)
	}
	return doc, keys, nil
}

// parseJWK checks one key: a public P-256 signing key for ES256, never a
// private one.
func parseJWK(raw json.RawMessage) (jwk, *ecdsa.PublicKey, error) {
	var members map[string]json.RawMessage
	if err := json.Unmarshal(raw, &members); err != nil {
		return jwk{}, nil, errors.New("not a JSON object")
	}
	for _, m := range privateMembers {
		if _, ok := members[m]; ok {
			return jwk{}, nil, errors.New("a private key member is present; push public keys only")
		}
	}
	var k jwk
	if err := json.Unmarshal(raw, &k); err != nil {
		return jwk{}, nil, errors.New("members have the wrong types")
	}
	switch {
	case k.Kid == "":
		return jwk{}, nil, errors.New("kid is required")
	case k.Kty != "EC" || k.Crv != "P-256":
		return jwk{}, nil, errors.New("only EC P-256 keys are accepted")
	case k.Alg != "" && k.Alg != "ES256":
		return jwk{}, nil, errors.New("alg must be ES256")
	case k.Use != "" && k.Use != "sig":
		return jwk{}, nil, errors.New("use must be sig")
	}
	x, errX := base64.RawURLEncoding.DecodeString(k.X)
	y, errY := base64.RawURLEncoding.DecodeString(k.Y)
	if errX != nil || errY != nil || len(x) != 32 || len(y) != 32 {
		return jwk{}, nil, errors.New("x and y must be 32-byte base64url values")
	}
	pub, err := ecdsa.ParseUncompressedPublicKey(elliptic.P256(), append(append([]byte{4}, x...), y...))
	if err != nil {
		return jwk{}, nil, errors.New("x and y are not a point on P-256")
	}
	return k, pub, nil
}
