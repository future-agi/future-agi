package logspool

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"regexp"
	"strconv"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

var testKey = MACKey("synthetic-spool-key", "")

// testStore is a spool on the test Redis under a prefix of its own, removed
// after the test.
func testStore(t *testing.T, period string, maxBytes int64) (*Store, redis.UniversalClient) {
	t.Helper()
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Skip("TEST_REDIS_ADDR unset: a synthetic Redis is required")
	}
	db, _ := strconv.Atoi(os.Getenv("TEST_REDIS_DB"))
	c, err := redisstate.NewClient(redisstate.Config{Address: addr, DB: db})
	if err != nil {
		t.Fatal(err)
	}
	id, _ := randomID()
	prefix := "spooltest:" + id + ":"
	t.Cleanup(func() {
		ctx := context.Background()
		keys, _ := c.Redis().Keys(ctx, prefix+"*").Result()
		if len(keys) > 0 {
			c.Redis().Del(ctx, keys...)
		}
		c.Close()
	})
	return NewStore(c, prefix, period, maxBytes), c.Redis()
}

func entry(org, requestID string, cost float64, started time.Time) json.RawMessage {
	raw, _ := json.Marshal(map[string]any{
		"request_id": requestID, "timestamp": started, "model": "m-1", "cost": cost,
		"auth_key_id": "key-1", "user_id": "user-1",
		"metadata": map[string]string{"key_org_id": org},
	})
	return raw
}

func batch(t *testing.T, entries ...json.RawMessage) *Batch {
	t.Helper()
	b, skipped, err := NewBatch(testKey, time.Now(), entries, nil)
	if err != nil || skipped != 0 {
		t.Fatalf("NewBatch = %v, skipped %d", err, skipped)
	}
	return b
}

func spendOf(t *testing.T, s *Store, period string) *Spend {
	t.Helper()
	raw, err := s.Snapshot(context.Background(), testKey)
	if err != nil {
		t.Fatal(err)
	}
	return raw.Spend(period, PeriodStart(period, time.Now()))
}

func total(x *Spend, org string) float64 {
	if x == nil || x.Orgs[org] == nil {
		return 0
	}
	return x.Orgs[org].TotalSpend
}

func TestBatchMACCoversItsFieldsAndKeysStayDistinct(t *testing.T) {
	b := batch(t, entry("org-a", "req-1", 1, time.Now()))
	if !b.Verify(testKey) {
		t.Fatal("a freshly sealed batch does not verify")
	}
	b.Attempts++
	if b.Verify(testKey) {
		t.Fatal("changing attempts left the MAC valid")
	}
	b.Seal(testKey)
	b.SpooledAt = b.SpooledAt.Add(-time.Hour)
	if b.Verify(testKey) {
		t.Fatal("changing spooled_at left the MAC valid")
	}
	if b.Verify(MACKey("another-key", "")) {
		t.Fatal("a batch verifies under another key")
	}
	secret := "synthetic-webhook-secret"
	derived := MACKey("", secret)
	if len(derived) == 0 || string(derived) == secret || string(derived) == string(MACKey("dedicated", secret)) {
		t.Fatal("the spool key must be derived under its own label, distinct from the webhook secret and a dedicated key")
	}
	if MACKey("", "") != nil {
		t.Fatal("no key without a spool key or a webhook secret")
	}
}

func TestEntriesWithoutARequestIDAreNeverSpooled(t *testing.T) {
	noID, _ := json.Marshal(map[string]any{"model": "m", "cost": 1})
	b, skipped, err := NewBatch(testKey, time.Now(), []json.RawMessage{noID, entry("org-a", "req-1", 1, time.Now())}, nil)
	if err != nil || skipped != 1 || len(b.Entries) != 1 || len(b.Spend) != 1 {
		t.Fatalf("NewBatch = %v, skipped %d, %d entries", err, skipped, len(b.Entries))
	}
	if _, err := SpendOf(noID); !errors.Is(err, ErrNoRequestID) {
		t.Fatalf("SpendOf(no id) = %v, want ErrNoRequestID", err)
	}
}

func TestSpendOfTakesTheKeysOrgThenTheOrgID(t *testing.T) {
	both, _ := json.Marshal(map[string]any{"request_id": "r", "metadata": map[string]string{"key_org_id": "k-org", "org_id": "o-org"}})
	orgOnly, _ := json.Marshal(map[string]any{"request_id": "r", "metadata": map[string]string{"org_id": "o-org"}})
	if l, _ := SpendOf(both); l.Org != "k-org" {
		t.Fatalf("org = %q, want the key's org", l.Org)
	}
	if l, _ := SpendOf(orgOnly); l.Org != "o-org" {
		t.Fatalf("org = %q, want the org id", l.Org)
	}
}

func TestPeriodStartMatchesTheSpendSummary(t *testing.T) {
	now := time.Date(2026, 11, 12, 15, 4, 5, 0, time.UTC) // a Thursday
	for period, want := range map[string]time.Time{
		"daily":   time.Date(2026, 11, 12, 0, 0, 0, 0, time.UTC),
		"weekly":  time.Date(2026, 11, 9, 0, 0, 0, 0, time.UTC),
		"monthly": time.Date(2026, 11, 1, 0, 0, 0, 0, time.UTC),
		"":        time.Date(2026, 11, 1, 0, 0, 0, 0, time.UTC),
	} {
		if got := PeriodStart(period, now); !got.Equal(want) {
			t.Errorf("PeriodStart(%q) = %v, want %v", period, got, want)
		}
	}
}

// A full spool evicts its oldest batch, and the evicted spend stays counted
// through the tombstone.
func TestSizeEvictionKeepsTheEvictedSpend(t *testing.T) {
	ctx := context.Background()
	first := batch(t, entry("org-a", "req-1", 1.5, time.Now()))
	raw, _ := json.Marshal(first)
	s, _ := testStore(t, "monthly", int64(len(raw))+64)
	if n, err := s.Put(ctx, first); err != nil || n != 0 {
		t.Fatalf("first Put = %d, %v", n, err)
	}
	second := batch(t, entry("org-a", "req-2", 2.0, time.Now()))
	if n, err := s.Put(ctx, second); err != nil || n != 1 {
		t.Fatalf("second Put evicted %d (%v), want 1", n, err)
	}
	stats, _ := s.Stats(ctx)
	if stats.Pending != 1 {
		t.Fatalf("pending = %d, want 1", stats.Pending)
	}
	if got := total(spendOf(t, s, "monthly"), "org-a"); got != 3.5 {
		t.Fatalf("spool spend = %v, want 3.5 (the evicted 1.5 kept as a tombstone)", got)
	}
}

// A 2xx marks the batch delivered in the removal's own script; a
// batch marked but not removed (the lease was lost) is never counted again,
// and a later drain removes it.
func TestDeliveredIsMarkedEvenWhenTheRemovalCannotRun(t *testing.T) {
	ctx := context.Background()
	s, rdb := testStore(t, "monthly", 0)
	b := batch(t, entry("org-a", "req-1", 2.5, time.Now()))
	if _, err := s.Put(ctx, b); err != nil {
		t.Fatal(err)
	}
	lease, err := s.Acquire(ctx)
	if err != nil || lease == nil {
		t.Fatalf("Acquire = %v, %v", lease, err)
	}
	rdb.Del(ctx, s.leaseKey()) // the lease expired before the removal ran
	if err := lease.Delivered(ctx, b.ID); !errors.Is(err, ErrLeaseLost) {
		t.Fatalf("Delivered = %v, want ErrLeaseLost", err)
	}
	if got := total(spendOf(t, s, "monthly"), "org-a"); got != 0 {
		t.Fatalf("spool spend = %v, want 0: a delivered batch is never counted again (seed = S)", got)
	}
	next, _ := s.Acquire(ctx)
	if next == nil {
		t.Fatal("no lease for the next drain")
	}
	if err := next.Remove(ctx, b.ID); err != nil {
		t.Fatal(err)
	}
	if stats, _ := s.Stats(ctx); stats.Pending != 0 || stats.Bytes != 0 {
		t.Fatalf("after the next drain: %+v, want an empty spool", stats)
	}
}

// The lease is a random token released by compare-and-delete: a replica whose
// lease expired can neither remove batches nor release the next holder's.
func TestTheLeaseIsTokenFenced(t *testing.T) {
	ctx := context.Background()
	s, rdb := testStore(t, "monthly", 0)
	b := batch(t, entry("org-a", "req-1", 1, time.Now()))
	if _, err := s.Put(ctx, b); err != nil {
		t.Fatal(err)
	}
	first, _ := s.Acquire(ctx)
	if again, _ := s.Acquire(ctx); first == nil || again != nil {
		t.Fatalf("Acquire twice = %v, %v; want one holder", first, again)
	}
	rdb.Del(ctx, s.leaseKey())
	second, _ := s.Acquire(ctx)
	if second == nil {
		t.Fatal("no lease after the first expired")
	}
	if err := first.Remove(ctx, b.ID); !errors.Is(err, ErrLeaseLost) {
		t.Fatalf("an expired holder's Remove = %v, want ErrLeaseLost", err)
	}
	first.Release(ctx)
	if third, _ := s.Acquire(ctx); third != nil {
		t.Fatal("an expired holder released the current holder's lease")
	}
	if stats, _ := s.Stats(ctx); stats.Pending != 1 {
		t.Fatalf("pending = %d, want 1: nothing removed under a lost lease", stats.Pending)
	}
}

// X counts a request once, only in the period, and never a batch whose MAC
// fails.
func TestSnapshotFiltersThePeriodDedupesAndSkipsTamperedBatches(t *testing.T) {
	ctx := context.Background()
	s, rdb := testStore(t, "monthly", 0)
	now := time.Now().UTC()
	lastMonth := PeriodStart("monthly", now).Add(-24 * time.Hour)
	for _, b := range []*Batch{
		batch(t, entry("org-a", "req-1", 1, now), entry("org-a", "req-old", 5, lastMonth)),
		batch(t, entry("org-a", "req-1", 1, now)), // the same request, delivered again
	} {
		if _, err := s.Put(ctx, b); err != nil {
			t.Fatal(err)
		}
	}
	tampered := batch(t, entry("org-a", "req-forged", 100, now))
	if _, err := s.Put(ctx, tampered); err != nil {
		t.Fatal(err)
	}
	tampered.Spend[0].Cost = 1000
	raw, _ := json.Marshal(tampered)
	rdb.HSet(ctx, s.bKey(), tampered.ID, raw)
	if got := total(spendOf(t, s, "monthly"), "org-a"); got != 1 {
		t.Fatalf("spool spend = %v, want 1: req-1 once, not last month's, not the tampered batch", got)
	}
}

// S' = S + X merges: an org only X knows is added, and the levels add up.
func TestMergeAddsWhatOnlyTheSpoolKnows(t *testing.T) {
	s := &tenant.SpendSummary{Period: "monthly", Orgs: map[string]tenant.OrgSpendSummary{
		"org-a": {TotalSpend: 10, PerKey: map[string]float64{"key-1": 4}, PerUser: map[string]float64{}, PerModel: map[string]float64{"m-1": 10}},
	}}
	x := &Spend{}
	x.add("org-a", "key-1", "user-1", "m-1", 2)
	x.add("org-b", "key-9", "", "m-2", 3)
	got := Merge(s, x, "monthly", PeriodStart("monthly", time.Now()))
	a, b := got.Orgs["org-a"], got.Orgs["org-b"]
	if a.TotalSpend != 12 || a.PerKey["key-1"] != 6 || a.PerUser["user-1"] != 2 || a.PerModel["m-1"] != 12 {
		t.Fatalf("org-a = %+v", a)
	}
	if b.TotalSpend != 3 || b.PerKey["key-9"] != 3 {
		t.Fatalf("org-b = %+v, want the spool's 3", b)
	}
	if s.Orgs["org-a"].TotalSpend != 10 {
		t.Fatal("Merge changed the summary it was given")
	}
	if alone := Merge(nil, x, "monthly", PeriodStart("monthly", time.Now())); alone.Orgs["org-a"].TotalSpend != 2 {
		t.Fatalf("X alone = %+v", alone.Orgs["org-a"])
	}
}

// Key names are the prefix and fixed names only: no org, key or request id.
func TestKeyNamesHoldNoIDs(t *testing.T) {
	ctx := context.Background()
	first := batch(t, entry("org-secretive", "req-identifying", 1, time.Now()))
	raw, _ := json.Marshal(first)
	s, rdb := testStore(t, "monthly", int64(len(raw))+64)
	s.Put(ctx, first)
	s.Put(ctx, batch(t, entry("org-secretive", "req-identifying-2", 1, time.Now()))) // evicts: a tombstone
	lease, _ := s.Acquire(ctx)
	defer lease.Release(ctx)
	keys, err := rdb.Keys(ctx, s.prefix+"*").Result()
	if err != nil || len(keys) == 0 {
		t.Fatalf("keys = %v, %v", keys, err)
	}
	fixed := regexp.MustCompile(`^` + regexp.QuoteMeta(s.prefix) + `(b|t|d|s|bytes|lease|tomb:(\d+|total))$`)
	for _, k := range keys {
		if !fixed.MatchString(k) {
			t.Errorf("key %q is not a fixed name", k)
		}
	}
}

// Updating a batch (its attempts) is refused under a lost lease.
func TestUpdateNeedsTheLease(t *testing.T) {
	ctx := context.Background()
	s, rdb := testStore(t, "monthly", 0)
	b := batch(t, entry("org-a", "req-1", 1, time.Now()))
	s.Put(ctx, b)
	lease, _ := s.Acquire(ctx)
	b.Attempts++
	b.Seal(testKey)
	if err := lease.Update(ctx, b); err != nil {
		t.Fatal(err)
	}
	stored, _ := s.Oldest(ctx, 1)
	if len(stored) != 1 || stored[0].Batch.Attempts != 1 || !stored[0].Batch.Verify(testKey) {
		t.Fatalf("stored = %+v", stored)
	}
	rdb.Del(ctx, s.leaseKey())
	if err := lease.Update(ctx, b); !errors.Is(err, ErrLeaseLost) {
		t.Fatalf("Update under a lost lease = %v", err)
	}
}
