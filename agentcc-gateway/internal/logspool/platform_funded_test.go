package logspool

import (
	"context"
	"encoding/json"
	"testing"
	"time"
)

// A platform-funded key's request is delivered to the webhook with its cost,
// but its spend is not the org's: the spool neither seeds it into the org's
// budgets nor keeps it as a tombstone.

func platformFundedEntry(org, requestID string, cost float64, started time.Time) json.RawMessage {
	raw, _ := json.Marshal(map[string]any{
		"request_id": requestID, "timestamp": started, "model": "m-1", "cost": cost,
		"auth_key_id": "key-platform", "user_id": "user-1",
		"metadata": map[string]string{"key_org_id": org, "key_billing": "platform_funded"},
	})
	return raw
}

// The batch keeps the entry, so it is still delivered with its cost, and holds
// no spend line for it.
func TestAPlatformFundedEntryIsSpooledWithoutASpendLine(t *testing.T) {
	b := batch(t, platformFundedEntry("org-a", "req-platform", 4, time.Now()), entry("org-a", "req-1", 1.5, time.Now()))
	if len(b.Entries) != 2 {
		t.Fatalf("entries = %d, want 2: the platform-funded request is still delivered", len(b.Entries))
	}
	if len(b.Spend) != 1 || b.Spend[0].RequestID != "req-1" {
		t.Fatalf("spend lines = %+v, want only req-1's", b.Spend)
	}
	body, err := b.Body()
	if err != nil {
		t.Fatal(err)
	}
	var sent struct {
		Logs []struct {
			RequestID string  `json:"request_id"`
			Cost      float64 `json:"cost"`
		} `json:"logs"`
	}
	if err := json.Unmarshal(body, &sent); err != nil {
		t.Fatal(err)
	}
	found := false
	for _, l := range sent.Logs {
		if l.RequestID == "req-platform" {
			found = l.Cost == 4
		}
	}
	if !found {
		t.Fatalf("the webhook body does not carry req-platform with its cost 4: %s", body)
	}
	if !b.Verify(testKey) {
		t.Fatal("the batch does not verify under its key")
	}
}

// A spooled batch holding one platform-funded entry and one normal entry
// seeds only the normal entry's spend.
func TestASpooledPlatformFundedEntrySeedsNoSpend(t *testing.T) {
	ctx := context.Background()
	s, _ := testStore(t, "monthly", 0)
	if _, err := s.Put(ctx, batch(t, platformFundedEntry("org-a", "req-platform", 4, time.Now()),
		entry("org-a", "req-1", 1.5, time.Now()))); err != nil {
		t.Fatal(err)
	}
	x := spendOf(t, s, "monthly")
	if got := total(x, "org-a"); got != 1.5 {
		t.Fatalf("seeded org spend = %v, want 1.5 (the normal entry's only)", got)
	}
	if got := x.Orgs["org-a"].PerKey["key-platform"]; got != 0 {
		t.Fatalf("seeded spend of the platform-funded key = %v, want 0", got)
	}
	if got := x.Orgs["org-a"].PerModel["m-1"]; got != 1.5 {
		t.Fatalf("seeded model spend = %v, want 1.5", got)
	}
}

// A batch evicted from a full spool keeps the normal entry's spend as a
// tombstone and nothing for the platform-funded one.
func TestAnEvictedPlatformFundedEntryLeavesNoTombstone(t *testing.T) {
	ctx := context.Background()
	first := batch(t, platformFundedEntry("org-a", "req-platform", 4, time.Now()), entry("org-a", "req-1", 1.5, time.Now()))
	raw, _ := json.Marshal(first)
	s, _ := testStore(t, "monthly", int64(len(raw))+64)
	if n, err := s.Put(ctx, first); err != nil || n != 0 {
		t.Fatalf("first Put = %d, %v", n, err)
	}
	second := batch(t, entry("org-a", "req-2", 2.0, time.Now()))
	if n, err := s.Put(ctx, second); err != nil || n != 1 {
		t.Fatalf("second Put evicted %d (%v), want 1", n, err)
	}
	if got := total(spendOf(t, s, "monthly"), "org-a"); got != 3.5 {
		t.Fatalf("spool spend = %v, want 3.5 (the evicted 1.5 as a tombstone, and 2.0)", got)
	}
}

// Only the exact value marks an entry platform-funded: any other key_billing
// is counted as before.
func TestOnlyPlatformFundedSkipsTheSpendLine(t *testing.T) {
	for _, v := range []string{"", "Platform_Funded", "platform_funded ", "org"} {
		raw, _ := json.Marshal(map[string]any{
			"request_id": "req-1", "timestamp": time.Now(), "cost": 1.0,
			"metadata": map[string]string{"key_org_id": "org-a", "key_billing": v},
		})
		if b := batch(t, raw); len(b.Spend) != 1 {
			t.Errorf("key_billing %q: spend lines = %d, want 1", v, len(b.Spend))
		}
	}
}
