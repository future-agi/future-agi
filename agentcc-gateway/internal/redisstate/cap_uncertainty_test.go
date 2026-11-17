package redisstate

import (
	"testing"
	"time"
)

func TestCapUnknownHoldSettlesOnlyWithActuals(t *testing.T) {
	s := capTestStore(t)
	b := activatedBuckets(t, "agent:a", 5000, 4000)
	res, err := s.Reserve(bg, "g8-org", "2026-10-27", "unknown", 1, b, time.Now().Add(time.Minute))
	if err != nil || res.Status != "ok" {
		t.Fatalf("reserve=%+v %v", res, err)
	}
	for i := 0; i < 2; i++ {
		if err := s.Fence(bg, "g8-org", "2026-10-27", "unknown"); err != nil {
			t.Fatal(err)
		}
	}
	res, err = s.Reserve(bg, "g8-org", "2026-10-27", "next", 1, b, time.Now().Add(time.Minute))
	if err != nil || res.Status != "unavailable" {
		t.Fatalf("unknown spend not fenced: %+v %v", res, err)
	}
	if err := s.Complete(bg, "g8-org", "2026-10-27", "unknown", []CapBucket{{Level: "agent:a", Amount: 1000}}); err != nil {
		t.Fatal(err)
	}
	res, err = s.Reserve(bg, "g8-org", "2026-10-27", "next", 1, b, time.Now().Add(time.Minute))
	if err != nil || res.Status != "ok" {
		t.Fatalf("authoritative settlement did not release uncertainty: %+v %v", res, err)
	}
}

func TestCapPolicyCeilingSurvivesUTCDate(t *testing.T) {
	s := capTestStore(t)
	for _, c := range []struct {
		day, nonce    string
		revision      int
		limit, amount int64
		want          string
	}{
		{"2026-10-27", "tight", 2, 5000, 4000, "ok"},
		{"2026-10-28", "same-revision", 2, 50000, 10000, "exhausted"},
		{"2026-10-28", "old", 1, 50000, 10000, "stale"},
		{"2026-10-28", "new", 3, 50000, 10000, "ok"},
		{"2026-10-29", "old-after-rollover", 2, 50000, 10000, "stale"},
	} {
		r, err := s.Reserve(bg, "g8-org", c.day, c.nonce, c.revision, activatedBuckets(t, "agent:a", c.limit, c.amount), time.Now().Add(time.Minute))
		if err != nil || r.Status != c.want {
			t.Fatalf("%s: %+v %v want %s", c.nonce, r, err, c.want)
		}
	}
}

func TestStrictRunUnknownLifecycle(t *testing.T) {
	for _, terminal := range []int64{0, 1000, 4000} {
		t.Run(time.Duration(terminal).String(), func(t *testing.T) {
			s := capTestStore(t).store
			clock := time.Now()
			s.now = func() time.Time { return clock }
			r, err := s.ReserveStrict(bg, "g8-org", "run", "first", 4000, 5000, time.Minute, time.Second)
			if err != nil || r.Status != AgentBudgetOK {
				t.Fatalf("reserve %+v %v", r, err)
			}
			for _, k := range s.strictKeys("g8-org", "run") {
				if ttl := s.client.Redis().PTTL(bg, k).Val(); ttl > 0 {
					t.Fatalf("strict state expires: %s %v", k, ttl)
				}
			}
			clock = clock.Add(9 * 24 * time.Hour)
			r, err = s.ReserveStrict(bg, "g8-org", "run", "second", 4000, 5000, time.Minute, time.Second)
			if err != nil || r.Status != "uncertain" || r.HeldMicros != 4000 {
				t.Fatalf("lost hold %+v %v", r, err)
			}
			if _, err := s.Reserve(bg, "g8-org", "run", "legacy", 4000, 5000, time.Minute, time.Second); err == nil {
				t.Fatal("legacy reserve entered strict scope")
			}
			if _, err := s.Void(bg, "g8-org", "run", "first", time.Second); err == nil {
				t.Fatal("legacy void erased strict hold")
			}
			for i := 0; i < 2; i++ {
				r, err = s.Complete(bg, "g8-org", "run", "first", terminal, time.Second)
				if err != nil || r.SpentMicros != terminal || r.HeldMicros != 0 {
					t.Fatalf("terminal %+v %v", r, err)
				}
			}
			if _, err := s.Complete(bg, "g8-org", "run", "missing", 0, time.Second); err == nil {
				t.Fatal("unknown nonce settled")
			}
			r, err = s.ReserveStrict(bg, "g8-org", "run", "second", 4000, 5000, time.Minute, time.Second)
			want := AgentBudgetOK
			if terminal > 1000 {
				want = AgentBudgetExhausted
			}
			if err != nil || r.Status != want {
				t.Fatalf("after actual %+v %v want %s", r, err, want)
			}
		})
	}
}

func TestStrictRunStateAndReplyFailClosed(t *testing.T) {
	for _, reply := range [][]any{
		{"ok", "5000", "0", "0", int64(0), ""},
		{"ok", "5000", "bad", "0", int64(0), ""},
		{"unexpected", "5000", "0", "0", int64(0), ""},
		{"ok", "5000", "0", "0", "0", ""},
		{"ok", "5000", "0", "-1", int64(0), ""},
	} {
		_, err := parseStrictAgentBudgetState(reply)
		if (reply[0] == "ok" && reply[2] == "0" && reply[3] == "0" && reply[4] == int64(0)) != (err == nil) {
			t.Fatalf("reply=%v err=%v", reply, err)
		}
	}
	s := capTestStore(t).store
	if _, err := s.ReserveStrict(bg, "g8-org", "run", "first", 0, 0, time.Minute, time.Second); err != nil {
		t.Fatal(err)
	}
	if err := s.FenceStrict(bg, "g8-org", "run", "first"); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Complete(bg, "g8-org", "run", "first", 0, time.Second); err != nil {
		t.Fatal(err)
	}
	s.client.Redis().Del(bg, s.keys("g8-org", "run")[1])
	if r, err := s.ReserveStrict(bg, "g8-org", "run", "next", 0, 0, time.Minute, time.Second); err == nil {
		t.Fatalf("missing strict spend accepted: %+v", r)
	}
}

func TestCapLostPolicyOrCountersFailsClosed(t *testing.T) {
	for _, field := range []string{"revision", "limit:agent:a", "revision:agent:a", "held:agent:a", "spent:agent:a"} {
		t.Run(field, func(t *testing.T) {
			s := capTestStore(t)
			b := activatedBuckets(t, "agent:a", 5000, 4000)
			r, err := s.Reserve(bg, "g8-org", "2026-10-27", "first", 1, b, time.Now().Add(time.Minute))
			if err != nil || r.Status != "ok" {
				t.Fatalf("reserve %+v %v", r, err)
			}
			keys, _ := s.keys("g8-org", "2026-10-27")
			key, day := keys[4], "2026-10-28"
			if field == "held:agent:a" || field == "spent:agent:a" {
				key, day = keys[0], "2026-10-27"
			}
			s.store.client.Redis().HDel(bg, key, field)
			r, err = s.Reserve(bg, "g8-org", day, "next", 1, b, time.Now().Add(time.Minute))
			if err == nil && r.Status == "ok" {
				t.Fatalf("incomplete state admitted: %+v", r)
			}
			if key == keys[0] {
				if err := s.Complete(bg, "g8-org", day, "first", []CapBucket{{Level: "agent:a", Amount: 1000}}); err == nil {
					t.Fatal("terminal rebuilt lost counters")
				}
			}
		})
	}
}
