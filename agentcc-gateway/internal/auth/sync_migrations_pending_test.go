package auth

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// On a new install the app's database has no key table until its bootstrap
// job has migrated it, and with bundled datastores Helm runs that job only
// once the gateway reports ready (`helm install --wait`). The app's key list
// then answers 503 with code migrations_pending: it has made no key. The key
// store records that answer, as the latest one, without counting it as a
// loaded list: any other answer clears it, and a list loads.
func TestSyncKeysFromControlPlane_MigrationsPendingMeansTheAppHasNoKeyTable(t *testing.T) {
	answers := []func(w http.ResponseWriter){
		func(w http.ResponseWriter) { // the app before its bootstrap job
			w.WriteHeader(http.StatusServiceUnavailable)
			fmt.Fprint(w, `{"status":false,"type":"api_error","code":"migrations_pending","detail":"The database has no gateway key table yet: its migrations have not run."}`)
		},
		func(w http.ResponseWriter) { // starting, or down: says nothing about its keys
			w.WriteHeader(http.StatusServiceUnavailable)
			fmt.Fprint(w, `{"status":false,"result":"starting"}`)
		},
		func(w http.ResponseWriter) { // migrations_pending again
			w.WriteHeader(http.StatusServiceUnavailable)
			fmt.Fprint(w, `{"status":false,"code":"migrations_pending"}`)
		},
		func(w http.ResponseWriter) { // the code, but not on a 503
			w.WriteHeader(http.StatusInternalServerError)
			fmt.Fprint(w, `{"status":false,"code":"migrations_pending"}`)
		},
		func(w http.ResponseWriter) { // migrated: an empty first list
			fmt.Fprint(w, `{"status":true,"result":[]}`)
		},
	}
	var call atomic.Int32
	app := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		answers[int(call.Add(1))-1](w)
	}))
	defer app.Close()
	ks := NewKeyStore(config.AuthConfig{})
	sync := func() error { return SyncKeysFromControlPlane(context.Background(), app.URL, "t", ks) }

	if ks.AppHasNoKeyTable() {
		t.Fatal("a new store reports that the app has no key table before any answer")
	}

	err := sync()
	if !errors.Is(err, ErrAppNotMigrated) {
		t.Fatalf("503 migrations_pending: err = %v, want ErrAppNotMigrated", err)
	}
	if !ks.AppHasNoKeyTable() || ks.KeyListLoaded() {
		t.Fatalf("after 503 migrations_pending: AppHasNoKeyTable=%v KeyListLoaded=%v, want true false", ks.AppHasNoKeyTable(), ks.KeyListLoaded())
	}

	if err := sync(); err == nil || errors.Is(err, ErrAppNotMigrated) {
		t.Fatalf("a 503 without the code: err = %v, want a plain failure", err)
	}
	if ks.AppHasNoKeyTable() {
		t.Fatal("a 503 without the code left AppHasNoKeyTable set: only the latest answer counts")
	}

	if err := sync(); !errors.Is(err, ErrAppNotMigrated) || !ks.AppHasNoKeyTable() {
		t.Fatalf("503 migrations_pending again: err = %v, AppHasNoKeyTable = %v", err, ks.AppHasNoKeyTable())
	}
	if err := sync(); err == nil || errors.Is(err, ErrAppNotMigrated) || ks.AppHasNoKeyTable() {
		t.Fatalf("the code on a 500: err = %v, AppHasNoKeyTable = %v; want a plain failure that clears it", err, ks.AppHasNoKeyTable())
	}

	if err := sync(); err != nil {
		t.Fatalf("the empty first list: %v", err)
	}
	if !ks.KeyListLoaded() || ks.AppHasNoKeyTable() {
		t.Fatalf("after the first list: KeyListLoaded=%v AppHasNoKeyTable=%v, want true false", ks.KeyListLoaded(), ks.AppHasNoKeyTable())
	}
}

// The answer never touches the keys a store holds: a running gateway whose
// app answers migrations_pending (a database wiped or swapped under it) keeps
// every key it loaded, as on any failed sync.
func TestSyncKeysFromControlPlane_MigrationsPendingKeepsLoadedKeys(t *testing.T) {
	var pending atomic.Bool
	app := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if pending.Load() {
			w.WriteHeader(http.StatusServiceUnavailable)
			fmt.Fprint(w, `{"status":false,"code":"migrations_pending"}`)
			return
		}
		fmt.Fprintf(w, `{"status":true,"result":[{"id":"key-a","name":"a","key_hash":%q}]}`, HashKey("sk-agentcc-listed-a"))
	}))
	defer app.Close()
	ks := NewKeyStore(config.AuthConfig{})
	if err := SyncKeysFromControlPlane(context.Background(), app.URL, "t", ks); err != nil {
		t.Fatal(err)
	}
	pending.Store(true)
	if err := SyncKeysFromControlPlane(context.Background(), app.URL, "t", ks); !errors.Is(err, ErrAppNotMigrated) {
		t.Fatalf("err = %v, want ErrAppNotMigrated", err)
	}
	if ks.Authenticate("sk-agentcc-listed-a") == nil {
		t.Fatal("a migrations_pending answer dropped a key the store had loaded")
	}
	if !ks.KeyListLoaded() {
		t.Fatal("a migrations_pending answer cleared KeyListLoaded")
	}
}

// With no control plane URL the sync is skipped before the store is used: a
// nil store is never read or written.
func TestSyncKeysFromControlPlane_NoURLLeavesTheStoreAlone(t *testing.T) {
	if err := SyncKeysFromControlPlane(context.Background(), "", "", nil); err != nil {
		t.Fatalf("no control plane URL, nil store: err = %v, want nil", err)
	}
	ks := NewKeyStore(config.AuthConfig{})
	ks.appHasNoKeyTable.Store(true)
	if err := SyncKeysFromControlPlane(context.Background(), "", "", ks); err != nil {
		t.Fatal(err)
	}
	if !ks.AppHasNoKeyTable() {
		t.Error("a skipped sync (no control plane URL) cleared the store's latest answer: nothing was asked")
	}
}
