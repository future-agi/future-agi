package redisstate

import (
	"testing"
	"time"
)

// A hold reserved with a zero amount on one of its levels completes, and
// leaves that level's held total where it was. Releasing it must not send
// Redis a "-0" increment, which Redis before 7.4 refuses as not an integer.
func TestCapHoldWithAZeroAmountLevelCompletes(t *testing.T) {
	s := capTestStore(t)
	const org, day = "g8-org", "2026-10-27"
	until := time.Now().Add(time.Minute)

	// Another open hold keeps 700 held on the zero-amount level.
	other, err := s.Reserve(bg, org, day, "other", 1, activatedBuckets(t, "agent:z", 1000000, 700), until)
	if err != nil || other.Status != "ok" {
		t.Fatalf("other hold: %+v %v", other, err)
	}
	buckets := append(activatedBuckets(t, "agent:z", 1000000, 0), activatedBuckets(t, "key_id:z", 1000000, 500)...)
	res, err := s.Reserve(bg, org, day, "zero", 1, buckets, until)
	if err != nil || res.Status != "ok" {
		t.Fatalf("zero-amount hold: %+v %v", res, err)
	}

	if err := s.Complete(bg, org, day, "zero", []CapBucket{{Level: "agent:z", Amount: 0}, {Level: "key_id:z", Amount: 300}}); err != nil {
		t.Fatalf("completing a hold with a zero-amount level: %v", err)
	}

	keys, _ := s.keys(org, day)
	field := func(name string) string { return s.store.client.Redis().HGet(bg, keys[0], name).Val() }
	if got := field("held:agent:z"); got != "700" {
		t.Fatalf("held:agent:z = %q, want the other hold's 700 unchanged", got)
	}
	if got := field("held:key_id:z"); got != "0" {
		t.Fatalf("held:key_id:z = %q, want 0 once its hold completed", got)
	}
	if got := field("spent:key_id:z"); got != "300" {
		t.Fatalf("spent:key_id:z = %q, want 300", got)
	}
}
