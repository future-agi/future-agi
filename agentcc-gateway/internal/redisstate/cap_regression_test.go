package redisstate

import (
	"testing"
	"time"
)

func TestSolCapValidRollbackVersionRemainsUsable(t *testing.T) {
	s := capTestStore(t)
	buckets := activatedBuckets(t, "agent:a", 50000, 4000)
	first, err := s.Reserve(bg, "g8-org", "2026-10-27", "v2", 2, buckets, time.Now().Add(time.Minute))
	if err != nil || first.Status != "ok" {
		t.Fatalf("initial reserve=%+v err=%v", first, err)
	}
	if err := s.Complete(bg, "g8-org", "2026-10-27", "v2", []CapBucket{{Level: "agent:a", Amount: 3000}}); err != nil {
		t.Fatal(err)
	}
	// Activating saved config version 1 now mints revision 3. The HTTP
	// config-to-chat regression verifies that assignment by the actual writer.
	result, err := s.Reserve(bg, "g8-org", "2026-10-27", "reactivated-v1", 3, buckets, time.Now().Add(time.Minute))
	if err != nil || result.Status != "ok" {
		t.Errorf("valid config rollback disabled all capped admission: result=%+v err=%v", result, err)
	}
}

func TestSolCapRunCrashDoesNotEraseUnknownSpend(t *testing.T) {
	s := capTestStore(t).store
	clock := time.Now()
	s.now = func() time.Time { return clock }
	first, err := s.ReserveStrict(bg, "g8-org", "run-a", "crashed", 4000, 5000, time.Minute, 8*24*time.Hour)
	if err != nil || first.Status != AgentBudgetOK {
		t.Fatalf("initial run reserve=%+v err=%v", first, err)
	}
	// The process disappears after dispatch; a new admission after expiry has no
	// actual terminal for the old work. Expiration is not proof of zero spend.
	clock = clock.Add(2 * time.Minute)
	result, err := s.ReserveStrict(bg, "g8-org", "run-a", "replica-next", 4000, 5000, time.Minute, 8*24*time.Hour)
	if err == nil && result.Status == AgentBudgetOK {
		t.Errorf("strict run admitted another 4000 after forgetting the crashed 4000 hold under limit 5000: %+v", result)
	}
}

func TestSolCapPolicyChangesRemainFencedAcrossUTCDate(t *testing.T) {
	s := capTestStore(t)
	tighter := activatedBuckets(t, "agent:a", 5000, 4000)
	first, err := s.Reserve(bg, "g8-org", "2026-10-27", "new-policy", 2, tighter, time.Now().Add(time.Minute))
	if err != nil || first.Status != "ok" {
		t.Fatalf("initial reserve=%+v err=%v", first, err)
	}
	stale := activatedBuckets(t, "agent:a", 50000, 10000)
	result, err := s.Reserve(bg, "g8-org", "2026-10-28", "stale-replica", 1, stale, time.Now().Add(time.Minute))
	if err == nil && result.Status == "ok" {
		t.Errorf("UTC rollover erased policy fence: old version admitted 10000 against the new 5000 cap: %+v", result)
	}
}
