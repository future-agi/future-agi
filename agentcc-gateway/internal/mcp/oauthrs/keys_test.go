package oauthrs

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp/oauthrs/oauthrstest"
)

// memStore is a KeyStore in memory, shared by the KeySets of one test the way
// Redis is shared by replicas.
type memStore struct {
	mu    sync.Mutex
	doc   []byte
	ver   int64
	loads int
	fail  bool
}

func (m *memStore) Load(context.Context) ([]byte, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.loads++
	if m.fail {
		return nil, errors.New("store down")
	}
	return m.doc, nil
}

func (m *memStore) SaveIfNewer(_ context.Context, version int64, doc []byte) (bool, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.fail {
		return false, errors.New("store down")
	}
	if version <= m.ver {
		return false, nil
	}
	m.ver, m.doc = version, append([]byte(nil), doc...)
	return true, nil
}

func (m *memStore) loadCount() int {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.loads
}

// A push is applied only when its version is newer than the set in force; an
// equal or older one changes nothing and is not an error, so a retried push
// is harmless.
func TestKeysPushVersioned(t *testing.T) {
	ctx := context.Background()
	a := oauthrstest.NewSigner(t, "as-a")
	b := oauthrstest.NewSigner(t, "as-b")
	ks := NewKeySet(testIssuer, nil, nil)

	res, err := ks.Push(ctx, oauthrstest.PushBody(t, testIssuer, 2, a))
	if err != nil || !res.Applied || res.Version != 2 {
		t.Fatalf("first push = %+v, %v, want applied at version 2", res, err)
	}
	if _, ok := ks.Lookup(ctx, "as-a"); !ok {
		t.Fatal("the pushed key is not held")
	}
	for _, v := range []int64{1, 2} {
		res, err = ks.Push(ctx, oauthrstest.PushBody(t, testIssuer, v, b))
		if err != nil || res.Applied || res.Version != 2 {
			t.Fatalf("push of version %d = %+v, %v, want not applied, version 2", v, res, err)
		}
		if _, ok := ks.Lookup(ctx, "as-b"); ok {
			t.Fatalf("a push of version %d replaced the newer set", v)
		}
	}
	res, err = ks.Push(ctx, oauthrstest.PushBody(t, testIssuer, 3, b))
	if err != nil || !res.Applied || res.Version != 3 {
		t.Fatalf("newer push = %+v, %v, want applied at version 3", res, err)
	}
	if _, ok := ks.Lookup(ctx, "as-a"); ok {
		t.Fatal("a key the newer set dropped is still accepted")
	}
	if _, ok := ks.Lookup(ctx, "as-b"); !ok {
		t.Fatal("the newer set's key is not held")
	}
}

// Only public P-256 signing keys of the trusted issuer are ever held: a push
// for another issuer, carrying a private key member, or holding a key of
// another type is refused whole and changes nothing.
func TestKeysPushRefusesAnythingButPublicKeysOfTheIssuer(t *testing.T) {
	ctx := context.Background()
	good := oauthrstest.NewSigner(t, "as-good")
	ks := NewKeySet(testIssuer, nil, nil)
	if _, err := ks.Push(ctx, oauthrstest.PushBody(t, testIssuer, 1, good)); err != nil {
		t.Fatal(err)
	}
	withKey := func(mutate func(map[string]any)) []byte {
		k := oauthrstest.NewSigner(t, "as-new").JWK()
		mutate(k)
		body, _ := json.Marshal(map[string]any{"issuer": testIssuer, "version": 9, "keys": []any{k}})
		return body
	}
	cases := map[string][]byte{
		"other issuer":     oauthrstest.PushBody(t, "https://other.example.test/as", 9, good),
		"version zero":     oauthrstest.PushBody(t, testIssuer, 0, good),
		"no keys":          []byte(`{"issuer":"` + testIssuer + `","version":9,"keys":[]}`),
		"unknown member":   []byte(`{"issuer":"` + testIssuer + `","version":9,"keys":[],"extra":1}`),
		"not json":         []byte(`issuer=x`),
		"private member d": withKey(func(k map[string]any) { k["d"] = k["x"] }),
		"private member k": withKey(func(k map[string]any) { k["k"] = "AAAA" }),
		"rsa key":          withKey(func(k map[string]any) { k["kty"] = "RSA" }),
		"other curve":      withKey(func(k map[string]any) { k["crv"] = "P-384" }),
		"other alg":        withKey(func(k map[string]any) { k["alg"] = "HS256" }),
		"encryption use":   withKey(func(k map[string]any) { k["use"] = "enc" }),
		"no kid":           withKey(func(k map[string]any) { delete(k, "kid") }),
		"short x":          withKey(func(k map[string]any) { k["x"] = "AAAA" }),
		"off curve":        withKey(func(k map[string]any) { k["y"] = k["x"] }),
		"duplicate kid": func() []byte {
			s := oauthrstest.NewSigner(t, "dup")
			return oauthrstest.PushBody(t, testIssuer, 9, s, s)
		}(),
	}
	for name, body := range cases {
		res, err := ks.Push(ctx, body)
		if !errors.Is(err, ErrInvalidPush) {
			t.Fatalf("%s: Push = %+v, %v, want ErrInvalidPush", name, res, err)
		}
		if strings.Contains(err.Error(), good.JWK()["x"].(string)) {
			t.Fatalf("%s: the refusal repeats key material: %v", name, err)
		}
		if ks.Version() != 1 {
			t.Fatalf("%s: a refused push changed the set to version %d", name, ks.Version())
		}
	}
	if _, ok := ks.Lookup(ctx, "as-good"); !ok {
		t.Fatal("a refused push dropped the key in force")
	}
}

// A push to one replica reaches the others through the shared store: a
// replica rereads it when a token names a key it does not hold, and at least
// every reloadEvery, so a dropped key stops working everywhere.
func TestKeysReachEveryReplicaThroughTheStore(t *testing.T) {
	ctx := context.Background()
	store := &memStore{}
	now := time.Unix(1_800_000_000, 0)
	clock := func() time.Time { return now }
	a := oauthrstest.NewSigner(t, "as-a")
	b := oauthrstest.NewSigner(t, "as-b")
	replica1 := NewKeySet(testIssuer, store, clock)
	replica2 := NewKeySet(testIssuer, store, clock)

	if res, err := replica1.Push(ctx, oauthrstest.PushBody(t, testIssuer, 1, a)); err != nil || !res.Applied {
		t.Fatalf("push = %+v, %v", res, err)
	}
	if _, ok := replica2.Lookup(ctx, "as-a"); !ok {
		t.Fatal("replica 2 does not accept the key pushed to replica 1")
	}

	// Rotation: version 2 holds only b.
	if res, err := replica1.Push(ctx, oauthrstest.PushBody(t, testIssuer, 2, b)); err != nil || !res.Applied {
		t.Fatalf("rotation push = %+v, %v", res, err)
	}
	if _, ok := replica2.Lookup(ctx, "as-a"); !ok {
		t.Fatal("replica 2 dropped its key before rereading")
	}
	now = now.Add(reloadEvery)
	if _, ok := replica2.Lookup(ctx, "as-a"); ok {
		t.Fatal("replica 2 still accepts the rotated-out key after the reread interval")
	}
	if _, ok := replica2.Lookup(ctx, "as-b"); !ok {
		t.Fatal("replica 2 does not hold the rotated-in key")
	}

	// Tokens naming unknown keys reread the store at most once a second.
	now = now.Add(reloadMissFloor)
	before := store.loadCount()
	for range 50 {
		replica2.Lookup(ctx, "made-up")
	}
	if got := store.loadCount() - before; got != 1 {
		t.Fatalf("50 unknown-key lookups in one instant read the store %d times, want once", got)
	}
}

// With a shared store the set changes only once the store took the push, so
// replicas never disagree; the authorization server is told to push again.
func TestKeysPushNotAppliedWhenTheStoreIsDown(t *testing.T) {
	ctx := context.Background()
	store := &memStore{fail: true}
	ks := NewKeySet(testIssuer, store, nil)
	_, err := ks.Push(ctx, oauthrstest.PushBody(t, testIssuer, 1, oauthrstest.NewSigner(t, "as-a")))
	if !errors.Is(err, ErrStoreUnavailable) {
		t.Fatalf("Push with the store down = %v, want ErrStoreUnavailable", err)
	}
	if ks.Version() != 0 {
		t.Fatalf("version %d after a push the store refused", ks.Version())
	}
}
