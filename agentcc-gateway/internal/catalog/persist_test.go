package catalog

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestSnapshotDirPersistsAndRestores(t *testing.T) {
	dir := t.TempDir()
	clock := newFakeClock()
	opts := Options{SnapshotDir: dir, HistorySize: 50, KeepSnapshots: 3, Now: clock.Now}

	s := NewStore()
	if err := s.Configure(opts); err != nil {
		t.Fatal(err)
	}
	var ids []string
	for i := 0; i < 5; i++ {
		clock.Advance(time.Hour)
		s.Put(&Snapshot{ProviderID: "or", Source: OriginLive, FetchedAt: clock.Now(), Models: pricedModels(2, float64(i))})
		ids = append(ids, s.Load().ID())
	}
	if _, err := s.SetBlocks([]string{"vendor/m000"}, "abuse"); err != nil {
		t.Fatal(err)
	}
	files, _ := filepath.Glob(filepath.Join(dir, "snapshots", "*.json.gz"))
	if len(files) != 3 {
		t.Fatalf("snapshot dir holds %d versions, want keep_snapshots=3", len(files))
	}

	// A restart: the gateway seeds an older catalog, then configures.
	clock.Advance(time.Hour)
	restarted := NewStore()
	restarted.Put(&Snapshot{ProviderID: "or", Source: OriginBundled, FetchedAt: clock.Now().Add(-72 * time.Hour), Models: pricedModels(2, 99)})
	if err := restarted.Configure(opts); err != nil {
		t.Fatal(err)
	}
	if got := restarted.History(0); len(got) != 5 || got[0].ID != ids[4] {
		t.Fatalf("restored history = %d entries", len(got))
	}
	if restarted.Load().ID() != ids[4] || restarted.Load().byProvider["or"].Source != OriginRestored {
		t.Fatal("the newest persisted version is fresher than the seed and must be restored as current")
	}
	if m, _ := restarted.Load().Get("or", "vendor/m000"); m.State != StateBlocked || m.StateReason != "abuse" {
		t.Fatalf("runtime block lost on restart: %s (%s)", m.State, m.StateReason)
	}
	old, ok := restarted.Snapshot(ids[2])
	if !ok {
		t.Fatal("a kept version must be readable from disk after a restart")
	}
	if m, _ := old.Get("or", "vendor/m001"); m.Prices.Prompt != 3e-6 {
		t.Fatalf("restored version price = %v", m.Prices.Prompt)
	}
	if _, ok := restarted.Snapshot(ids[0]); ok {
		t.Fatal("a version pruned beyond keep_snapshots must be gone")
	}
}

func TestSeedFresherThanPersistedStays(t *testing.T) {
	dir := t.TempDir()
	clock := newFakeClock()
	opts := Options{SnapshotDir: dir, Now: clock.Now}
	s := NewStore()
	s.Configure(opts)
	s.Put(&Snapshot{ProviderID: "or", Source: OriginLive, FetchedAt: clock.Now(), Models: pricedModels(2, 0)})

	restarted := NewStore()
	restarted.Put(&Snapshot{ProviderID: "or", Source: OriginBundled, FetchedAt: clock.Now().Add(time.Hour), Models: pricedModels(2, 7)})
	restarted.Configure(opts)
	if restarted.Load().byProvider["or"].Source != OriginBundled {
		t.Fatal("a persisted version older than the seed must not replace it")
	}
}

func TestUnwritableSnapshotDirFailsConfigure(t *testing.T) {
	file := filepath.Join(t.TempDir(), "a-file")
	if err := os.WriteFile(file, []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := NewStore().Configure(Options{SnapshotDir: file}); err == nil {
		t.Fatal("a snapshot_dir that cannot be a directory must fail loudly at startup")
	}
}

func TestRestoreOnlyConfiguredProviders(t *testing.T) {
	dir := t.TempDir()
	clock := newFakeClock()
	s := NewStore()
	mustConfigure(t, s, Options{SnapshotDir: dir, Now: clock.Now})
	s.Put(&Snapshot{ProviderID: "kept", Source: OriginLive, FetchedAt: clock.Now(), Models: pricedModels(1, 0)})
	s.Put(&Snapshot{ProviderID: "removed", Source: OriginLive, FetchedAt: clock.Now(), Models: pricedModels(1, 0)})

	restarted := NewStore()
	mustConfigure(t, restarted, Options{SnapshotDir: dir, Providers: []string{"kept"}, Now: clock.Now})
	if !restarted.Load().Has("kept", "vendor/m000") {
		t.Fatal("a configured provider with no seed must get its persisted catalog back")
	}
	if len(restarted.Load().Snapshots()) != 1 {
		t.Fatal("a provider the config does not name must not come back from disk")
	}
}

func TestServedSeedVersionIsKeptAcrossRestarts(t *testing.T) {
	dir := t.TempDir()
	clock := newFakeClock()
	opts := Options{SnapshotDir: dir, Providers: []string{"or"}, Now: clock.Now}
	boot := func() *Store {
		s := NewStore()
		s.Put(&Snapshot{ProviderID: "or", Source: OriginBundled, FetchedAt: clock.Now(), Models: pricedModels(2, 0)})
		if err := s.Configure(opts); err != nil {
			t.Fatal(err)
		}
		return s
	}
	seed := boot().Load().ID()
	// No live refresh came; a decision made on the seed must stay replayable
	// after a restart.
	if _, ok := boot().Snapshot(seed); !ok {
		t.Fatal("the seed version, served until a refresh, must be kept on disk")
	}
	if files, _ := filepath.Glob(filepath.Join(dir, "snapshots", "*.json.gz")); len(files) != 1 {
		t.Fatalf("two boots of one seed wrote %d versions, want 1", len(files))
	}
}
