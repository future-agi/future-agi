package auth

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// The console creates a key on the gateway first and commits its own row only
// when its request ends, about a second later. A periodic sync that fetches
// the key list in between does not see the new key. These tests replay that
// race on a fake clock.

type fakeClock struct {
	mu sync.Mutex
	t  time.Time
}

func (c *fakeClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.t
}

func (c *fakeClock) Advance(d time.Duration) {
	c.mu.Lock()
	c.t = c.t.Add(d)
	c.mu.Unlock()
}

// graceKeyStore returns an empty store whose clock the test drives. A grace of
// 0 keeps the default window.
func graceKeyStore(grace time.Duration) (*KeyStore, *fakeClock) {
	clock := &fakeClock{t: time.Date(2026, 10, 18, 12, 0, 0, 0, time.UTC)}
	ks := NewKeyStore(authCfg())
	ks.now = clock.Now
	ks.SetSyncGrace(grace)
	return ks, clock
}

// otherKeys is a control-plane list that holds some other key but not the one
// the test created, so the empty-list guard does not skip the sync.
var otherKeys = []SyncedKey{{ID: "key_other", Name: "other", KeyHash: HashKey("sk-agentcc-other")}}

func TestSyncFromHashes_KeepsNewKeyTheListMisses(t *testing.T) {
	ks, clock := graceKeyStore(0)

	created, raw := ks.Create("console-key", "", nil, nil, map[string]string{"org_id": "org-1"})
	clock.Advance(time.Second) // the sync fetches its list before Django commits the row
	ks.SyncFromHashes(otherKeys)

	if k := ks.Authenticate(raw); k == nil || k.ID != created.ID {
		t.Fatalf("new key after a sync that missed it = %+v, want %s still authenticating", k, created.ID)
	}
	if k := ks.Get(created.ID); k == nil || k.Metadata["org_id"] != "org-1" {
		t.Fatalf("new key by ID = %+v, want it kept with its metadata", k)
	}
	if ks.Authenticate("sk-agentcc-other") == nil {
		t.Error("the listed key was not loaded")
	}
}

// A key the control plane still does not list once the window has passed was
// never stored there, or was deleted there before any sync listed it, so the
// first sync past the window removes it.
func TestSyncFromHashes_RemovesUnlistedNewKeyAfterGraceWindow(t *testing.T) {
	if config.DefaultKeySyncGrace != 30*time.Second {
		t.Fatalf("DefaultKeySyncGrace = %v, this test pins 30s", config.DefaultKeySyncGrace)
	}
	for _, tc := range []struct {
		name        string
		grace       time.Duration
		stillInside time.Duration // a later sync inside the window keeps the key
		past        time.Duration // a sync at the end of the window removes it
	}{
		{"default 30s", 0, 29 * time.Second, 30 * time.Second},
		{"configured two minutes", 2 * time.Minute, 2*time.Minute - time.Second, 2 * time.Minute},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ks, clock := graceKeyStore(tc.grace)
			window := tc.grace
			if window == 0 {
				window = config.DefaultKeySyncGrace
			}
			created, raw := ks.Create("console-key", "", nil, nil, nil)

			clock.Advance(tc.stillInside)
			ks.SyncFromHashes(otherKeys)
			if ks.Authenticate(raw) == nil {
				t.Fatalf("key gone %v after creation, inside a %v window", tc.stillInside, window)
			}

			clock.Advance(tc.past - tc.stillInside)
			ks.SyncFromHashes(otherKeys)
			if k := ks.Authenticate(raw); k != nil {
				t.Fatalf("key still authenticates %v after creation, at the end of a %v window", tc.past, window)
			}
			if ks.Get(created.ID) != nil {
				t.Fatalf("key %s still in the store at the end of the window", created.ID)
			}
		})
	}
}

// The window is measured back from when the sync started fetching its list,
// not from when the answer arrived: a slow answer must not eat into it, and a
// key created while the request is in flight cannot be in that list at all.
func TestSyncKeysFromControlPlane_KeepsKeysCreatedBeforeOrDuringTheFetch(t *testing.T) {
	ks, clock := graceKeyStore(0)

	_, rawBefore := ks.Create("created-before", "", nil, nil, nil)
	clock.Advance(28 * time.Second) // the list is fetched 28 s after the key was created

	var rawDuring string
	controlPlane := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, rawDuring = ks.Create("created-during", "", nil, nil, nil)
		clock.Advance(5 * time.Second) // a slow answer: it lands 33 s after the first key
		json.NewEncoder(w).Encode(map[string]any{
			"status": true,
			"result": []map[string]any{{"id": "key_other", "key_hash": HashKey("sk-agentcc-other")}},
		})
	}))
	defer controlPlane.Close()

	if err := SyncKeysFromControlPlane(context.Background(), controlPlane.URL, "", ks); err != nil {
		t.Fatalf("SyncKeysFromControlPlane: %v", err)
	}

	if ks.Authenticate(rawBefore) == nil {
		t.Error("key created 28 s before the fetch began was removed: the window was measured from the answer")
	}
	if ks.Authenticate(rawDuring) == nil {
		t.Error("key created while the list was being fetched was removed")
	}
	if ks.Authenticate("sk-agentcc-other") == nil {
		t.Error("the listed key was not loaded")
	}
}

// Once the control plane lists a key created here, it is a synced key like any
// other: the list updates it, and when the list drops it (revoked in Django)
// the next sync removes it at once, without waiting out the window.
func TestSyncFromHashes_ListedNewKeyIsUpdatedThenRemovedAtOnce(t *testing.T) {
	ks, clock := graceKeyStore(0)
	created, raw := ks.Create("console-key", "", nil, nil, nil)

	clock.Advance(time.Second)
	ks.SyncFromHashes([]SyncedKey{
		{ID: created.ID, Name: "renamed-in-console", KeyHash: HashKey(raw), Metadata: map[string]string{"org_id": "org-1"}},
		otherKeys[0],
	})
	k := ks.Authenticate(raw)
	if k == nil || k.ID != created.ID || k.Name != "renamed-in-console" || k.Metadata["org_id"] != "org-1" {
		t.Fatalf("listed key = %+v, want it updated from the list", k)
	}

	clock.Advance(time.Second)
	ks.SyncFromHashes(otherKeys)
	if ks.Authenticate(raw) != nil || ks.Get(created.ID) != nil {
		t.Fatal("a key the control plane listed and then dropped survived the next sync")
	}
}

// A key that only ever came from the control plane gets no window either.
func TestSyncFromHashes_RemovesDroppedSyncedKeyAtOnce(t *testing.T) {
	ks, clock := graceKeyStore(0)
	ks.SyncFromHashes([]SyncedKey{
		{ID: "key_django", Name: "from-django", KeyHash: HashKey("sk-agentcc-django")},
		otherKeys[0],
	})
	if ks.Authenticate("sk-agentcc-django") == nil {
		t.Fatal("synced key did not load")
	}

	clock.Advance(time.Second)
	ks.SyncFromHashes(otherKeys)
	if ks.Authenticate("sk-agentcc-django") != nil || ks.Get("key_django") != nil {
		t.Fatal("a synced key the control plane dropped survived the next sync")
	}
}

// A key the app deletes or revokes before any sync lists it is removed by
// the first sync past the window, which does not depend on the sync
// interval: at a 60s interval, the first periodic sync at 59s.
func TestSyncFromHashes_KeyDeletedInAppBeforeListingGoesAtFirstSyncPastWindow(t *testing.T) {
	for _, tc := range []struct {
		name      string
		racing    time.Duration // a sync that fetched before the app stored the key, or 0
		firstPast time.Duration // the first sync past the window
	}{
		{"first periodic sync at 59s", 0, 59 * time.Second},
		{"racing sync at 1s, next periodic sync at 61s", time.Second, 61 * time.Second},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ks, clock := graceKeyStore(0)
			created, raw := ks.Create("console-key", "", nil, nil, nil)

			if tc.racing > 0 {
				clock.Advance(tc.racing)
				ks.SyncFromHashes(otherKeys)
				if ks.Authenticate(raw) == nil {
					t.Fatal("the racing sync removed the new key")
				}
			}

			// The app deleted the key meanwhile, so no list ever names it.
			clock.Advance(tc.firstPast - tc.racing)
			ks.SyncFromHashes(otherKeys)
			if ks.Authenticate(raw) != nil || ks.Get(created.ID) != nil {
				t.Fatalf("a key the app never listed still works %v after creation, past the %v window",
					tc.firstPast, config.DefaultKeySyncGrace)
			}
		})
	}
}

// Syncs can overlap: at boot the startup sync retries next to the periodic
// one. A list whose fetch began before the last applied list's is older and
// is ignored.
func TestSyncFromList_IgnoresListFetchedBeforeTheAppliedOne(t *testing.T) {
	t.Run("does not drop a key the newer list named", func(t *testing.T) {
		ks, clock := graceKeyStore(0)
		created, raw := ks.Create("console-key", "", nil, nil, nil)
		staleFetchedAt := clock.Now().Add(500 * time.Millisecond) // before the app stored the key
		clock.Advance(2 * time.Second)

		ks.syncFromList(append([]SyncedKey{{ID: created.ID, KeyHash: HashKey(raw)}}, otherKeys...), clock.Now())
		ks.syncFromList(otherKeys, staleFetchedAt) // the older answer lands last
		if ks.Authenticate(raw) == nil {
			t.Fatal("a list fetched before the applied one removed a key the applied one listed")
		}
	})

	t.Run("does not bring back a key the newer list dropped", func(t *testing.T) {
		ks, clock := graceKeyStore(0)
		dropped := SyncedKey{ID: "key_dropped", Name: "dropped", KeyHash: HashKey("sk-agentcc-dropped")}
		ks.SyncFromHashes([]SyncedKey{dropped, otherKeys[0]})
		staleFetchedAt := clock.Now().Add(time.Second) // still lists the key
		clock.Advance(2 * time.Second)

		ks.syncFromList(otherKeys, clock.Now()) // revoked in the app: dropped
		ks.syncFromList([]SyncedKey{dropped, otherKeys[0]}, staleFetchedAt)
		if ks.Authenticate("sk-agentcc-dropped") != nil {
			t.Fatal("a list fetched before the applied one brought back a key the applied one dropped")
		}
	})

	t.Run("applies lists fetched at or after the applied one", func(t *testing.T) {
		ks, clock := graceKeyStore(0)
		ks.SyncFromHashes(otherKeys)
		ks.syncFromList([]SyncedKey{{ID: "key_new", KeyHash: HashKey("sk-agentcc-new")}, otherKeys[0]}, clock.Now())
		if ks.Authenticate("sk-agentcc-new") == nil {
			t.Fatal("a list fetched at the same time as the applied one was ignored")
		}
	})
}
