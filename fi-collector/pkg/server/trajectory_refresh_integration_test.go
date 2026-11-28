package server

import (
	"context"
	"fmt"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/trajectory"
)

// A copy older than the reuse window (two generations) is classed Refresh:
// the collector writes a fresh copy and credits it. On real ClickHouse that
// leaves two rows of the item, the newer persisted_at wins
// (ReplacingMergeTree(persisted_at)), and the content is unchanged. Merges are
// stopped so the two rows stay visible.
func TestClickHouseTrajectoryRefreshWritesFreshCopy(t *testing.T) {
	origin := observedIntegrationOrigin(t)
	admin, adminPassword := observedTestCredentials()
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()

	database := fmt.Sprintf("trajectory_refresh_%d", time.Now().UnixNano())
	observedLocalSQL(t, origin, "default", "CREATE DATABASE "+database)
	t.Cleanup(func() { observedLocalSQL(t, origin, "default", "DROP DATABASE "+database) })
	ddl, _, found := strings.Cut(observedDDL(t, "schema", "040_trajectory_items.sql"), "ALTER TABLE spans")
	if !found {
		t.Fatal("040_trajectory_items.sql: CREATE TABLE / ALTER TABLE boundary changed")
	}
	observedLocalSQL(t, origin, database, strings.TrimSuffix(strings.TrimSpace(ddl), ";"))
	observedLocalSQL(t, origin, database, "SYSTEM STOP MERGES trajectory_items")

	writer, err := chwriter.New(chwriter.Config{
		URL:            origin,
		Username:       admin,
		Password:       adminPassword,
		Database:       database,
		MaxRetries:     1,
		DeadLetterFile: filepath.Join(t.TempDir(), "dlq.jsonl"),
	})
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()

	const reuseAge = 24 * time.Hour
	var clockMu sync.Mutex
	clock := time.Date(2026, 11, 22, 1, 0, 0, 0, time.UTC)
	now := func() time.Time { clockMu.Lock(); defer clockMu.Unlock(); return clock }
	setClock := func(t time.Time) { clockMu.Lock(); clock = t; clockMu.Unlock() }
	seen := trajectory.NewSeenStoreWithOptions(nil, trajectory.SeenOptions{MaxReuseAge: reuseAge, Now: now})

	pid := "33333333-3333-4333-8333-333333333333"
	items := []trajectory.Item{
		{ProjectID: pid, ScopeID: "sess-refresh", ItemHash: strings.Repeat("a1", 16), ItemType: "message", Role: "user", Content: `{"role":"user","content":"hello"}`},
		{ProjectID: pid, ScopeID: trajectory.ProjectScopeID, ItemHash: strings.Repeat("b2", 16), ItemType: "tool_def", Content: `{"type":"function","function":{"name":"lookup"}}`},
	}
	tokA, tokB, tokC := trajectory.SeenCommitterToken("trace-A"), trajectory.SeenCommitterToken("trace-B"), trajectory.SeenCommitterToken("trace-C")

	// The drain's order: insert the batch stamped persisted_at, then commit
	// the seen state with that stamp.
	persist := func(label string, res trajectory.SeenCheck) time.Time {
		t.Helper()
		batch := trajectory.NewBatch()
		for _, it := range res.CommitItems() {
			batch.AddItem(it)
		}
		if len(batch.Items()) != len(items) {
			t.Fatalf("%s: %d items to write, want %d", label, len(batch.Items()), len(items))
		}
		stamp := now()
		if err := writer.InsertTable(ctx, "trajectory_items", batch.ToRows(stamp)); err != nil {
			t.Fatalf("%s: insert trajectory_items: %v", label, err)
		}
		seen.MarkCommittedAt(ctx, batch.Items(), stamp)
		return stamp
	}
	wantClass := func(label string, res trajectory.SeenCheck, want trajectory.SeenClass) {
		t.Helper()
		for i, d := range res.Decisions {
			if d.Class != want {
				t.Fatalf("%s: item %d class=%s, want %s", label, i, d.Class, want)
			}
		}
	}

	first := seen.Check(ctx, items, tokA)
	wantClass("first request", first, trajectory.SeenNew)
	t1 := persist("first request", first)

	setClock(t1.Add(time.Hour))
	reuse := seen.Check(ctx, items, tokB)
	wantClass("within the reuse window", reuse, trajectory.SeenReuseOther)
	if len(reuse.Write()) != 0 {
		t.Fatalf("a reused item was written again")
	}

	setClock(t1.Add(reuseAge + time.Minute))
	refresh := seen.Check(ctx, items, tokB)
	wantClass("past the reuse window", refresh, trajectory.SeenRefresh)
	if refresh.BilledItemBytes() != 0 || refresh.CreditedItemBytes() == 0 {
		t.Fatalf("refresh billed=%d credited=%d, want credited only", refresh.BilledItemBytes(), refresh.CreditedItemBytes())
	}
	t2 := persist("refresh", refresh)

	where := "project_id = '" + pid + "'"
	if got := observedLocalSQL(t, origin, database, "SELECT count() FROM trajectory_items WHERE "+where); got != "4" {
		t.Fatalf("rows=%s, want 4 (two copies of each item)", got)
	}
	if got := observedLocalSQL(t, origin, database, "SELECT count() FROM trajectory_items FINAL WHERE "+where); got != "2" {
		t.Fatalf("rows after FINAL=%s, want 2", got)
	}
	for _, it := range items {
		cond := where + " AND scope_id = '" + it.ScopeID + "' AND item_hash = '" + it.ItemHash + "'"
		if got := observedLocalSQL(t, origin, database, "SELECT toString(min(persisted_at)), toString(max(persisted_at)) FROM trajectory_items WHERE "+cond); got != t1.Format("2006-01-02 15:04:05.000000")+"\t"+t2.Format("2006-01-02 15:04:05.000000") {
			t.Fatalf("%s persisted_at range=%q, want %s .. %s", it.ScopeID, got, t1, t2)
		}
		if got := observedLocalSQL(t, origin, database, "SELECT argMax(content, persisted_at) FROM trajectory_items WHERE "+cond+" FORMAT TSVRaw"); got != it.Content {
			t.Fatalf("%s argMax(content)=%q, want %q", it.ScopeID, got, it.Content)
		}
		if got := observedLocalSQL(t, origin, database, "SELECT toString(persisted_at) FROM trajectory_items FINAL WHERE "+cond); got != t2.Format("2006-01-02 15:04:05.000000") {
			t.Fatalf("%s FINAL persisted_at=%q, want the fresh copy %s", it.ScopeID, got, t2)
		}
	}

	// A re-poll of the refresh by the same billing key is Refresh again and
	// writes nothing; another key reuses the fresh copy.
	setClock(t2.Add(time.Minute))
	repoll := seen.Check(ctx, items, tokB)
	wantClass("re-poll of the refresh", repoll, trajectory.SeenRefresh)
	if len(repoll.Write()) != 0 {
		t.Fatalf("re-poll of a refresh wrote %d items", len(repoll.Write()))
	}
	wantClass("another key after the refresh", seen.Check(ctx, items, tokC), trajectory.SeenReuseOther)
}
