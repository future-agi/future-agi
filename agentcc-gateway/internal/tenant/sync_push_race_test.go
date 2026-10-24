package tenant

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// A control-plane sync reads every org's config from the bulk endpoint while
// the control plane may push one org's new config to the gateway. Django
// pushes from inside the transaction that saves the config, so a bulk read
// that began before the push can answer with the org's previous config, or
// without the org when this is its first. A sync that began before the push
// keeps the push over that older answer: the org runs its pushed config at
// once, and the next sync applies the control plane's state again.

// bulkVersions is a bulk-endpoint body holding one config per org, each
// carrying only its control-plane version.
func bulkVersions(versions map[string]int) map[string]any {
	result := make(map[string]any, len(versions))
	for orgID, v := range versions {
		result[orgID] = map[string]any{"meta": map[string]any{"version": v}}
	}
	return map[string]any{"status": true, "result": result}
}

func versioned(v int) *OrgConfig { return &OrgConfig{Meta: &ConfigMeta{Version: v}} }

// syncWithPush runs one sync against a bulk endpoint that calls push while it
// serves body, as a push arriving during the bulk read does.
func syncWithPush(t *testing.T, store *Store, body map[string]any, push func()) {
	t.Helper()
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if push != nil {
			push()
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(body)
	}))
	defer ts.Close()
	if err := SyncFromControlPlane(context.Background(), ts.URL, "", store); err != nil {
		t.Fatalf("sync: %v", err)
	}
}

func versionOf(store *Store, orgID string) int {
	cfg := store.Get(orgID)
	if cfg == nil {
		return 0
	}
	if cfg.Meta == nil {
		return -1
	}
	return cfg.Meta.Version
}

func TestSyncKeepsAConfigPushedWhileItRan(t *testing.T) {
	t.Run("an org's first config, pushed while the bulk read left it out", func(t *testing.T) {
		store := NewStore()
		syncWithPush(t, store, bulkVersions(map[string]int{"org-other": 3}), func() {
			store.Set("org-new", versioned(1))
		})
		if got := versionOf(store, "org-new"); got != 1 {
			t.Fatalf("org-new after the sync: version %d (0 = no config), want the pushed version 1", got)
		}
		if got := versionOf(store, "org-other"); got != 3 {
			t.Fatalf("org-other after the sync: version %d, want 3 from the bulk read", got)
		}
	})

	t.Run("a new config, pushed while the bulk read returned the previous one", func(t *testing.T) {
		store := NewStore()
		syncWithPush(t, store, bulkVersions(map[string]int{"org-a": 1}), nil)
		syncWithPush(t, store, bulkVersions(map[string]int{"org-a": 1}), func() {
			store.Set("org-a", versioned(2))
		})
		if got := versionOf(store, "org-a"); got != 2 {
			t.Fatalf("org-a after the sync: version %d, want the pushed version 2", got)
		}
	})

	t.Run("a config deleted while the bulk read still returned it", func(t *testing.T) {
		store := NewStore()
		syncWithPush(t, store, bulkVersions(map[string]int{"org-a": 1}), nil)
		syncWithPush(t, store, bulkVersions(map[string]int{"org-a": 1}), func() {
			store.Delete("org-a")
		})
		if got := versionOf(store, "org-a"); got != 0 {
			t.Fatalf("org-a after the sync: version %d, want no config (it was deleted during the read)", got)
		}
	})

	t.Run("every config replaced while the bulk read ran", func(t *testing.T) {
		store := NewStore()
		syncWithPush(t, store, bulkVersions(map[string]int{"org-y": 1}), func() {
			store.LoadBulk(map[string]*OrgConfig{"org-x": versioned(4)})
		})
		if got := versionOf(store, "org-x"); got != 4 {
			t.Fatalf("org-x after the sync: version %d, want 4 from the bulk load", got)
		}
		if got := versionOf(store, "org-y"); got != 0 {
			t.Fatalf("org-y after the sync: version %d, want no config (the bulk load replaced every config after the read began)", got)
		}
	})

	// The next sync, which began after the push, reads the control plane's answer
	// and applies it: the guard holds a pushed org for one sync only.
	t.Run("the next sync applies the control plane's state again", func(t *testing.T) {
		store := NewStore()
		syncWithPush(t, store, bulkVersions(map[string]int{"org-a": 1, "org-b": 1}), nil)
		syncWithPush(t, store, bulkVersions(map[string]int{"org-a": 1, "org-b": 1}), func() {
			store.Set("org-a", versioned(2))
		})
		syncWithPush(t, store, bulkVersions(map[string]int{"org-a": 3}), nil)
		if got := versionOf(store, "org-a"); got != 3 {
			t.Fatalf("org-a after the next sync: version %d, want 3 from the control plane", got)
		}
		if got := versionOf(store, "org-b"); got != 0 {
			t.Fatalf("org-b after the next sync: version %d, want no config (the control plane's answer leaves it out)", got)
		}
	})
}
