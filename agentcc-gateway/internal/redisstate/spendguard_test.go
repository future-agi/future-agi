package redisstate

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strconv"
	"sync"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/futureagi/agentcc-gateway/internal/billing"
)

// spendGuardTestStore is a SpendGuardStore on the test Redis (TEST_REDIS_ADDR,
// TEST_REDIS_DB) under the prefix test:G17:<test>:<nonce>:, whose keys are
// deleted when the test ends (SCAN and DEL, never FLUSHDB). It skips without
// Redis.
func spendGuardTestStore(t *testing.T) (*SpendGuardStore, redis.UniversalClient, string) {
	t.Helper()
	db, _ := strconv.Atoi(os.Getenv("TEST_REDIS_DB"))
	client, err := NewClient(Config{Address: redisAddr(), DB: db, Timeout: time.Second})
	if err != nil {
		t.Skipf("skipping: Redis not available at %s: %v", redisAddr(), err)
	}
	prefix := fmt.Sprintf("test:G17:%s:%d:", t.Name(), time.Now().UnixNano())
	rdb := client.Redis()
	t.Cleanup(func() {
		ctx := context.Background()
		iter := rdb.Scan(ctx, 0, prefix+"*", 1000).Iterator()
		var keys []string
		for iter.Next(ctx) {
			keys = append(keys, iter.Val())
		}
		if len(keys) > 0 {
			rdb.Del(ctx, keys...)
		}
		client.Close()
	})
	return NewSpendGuardStore(client, prefix), rdb, prefix
}

// sgT0 is a fixed instant 10 s into a minute, so tests control which minute
// bucket each call lands in.
var sgT0 = time.Date(2026, 10, 18, 9, 30, 10, 0, time.UTC)

func sgMinute(t time.Time) int64 { return t.UnixMilli() / 60_000 }

func sgAdmit(t *testing.T, s *SpendGuardStore, a SpendGuardAdmission) SpendGuardVerdict {
	t.Helper()
	v, err := s.Admit(bg, a)
	if err != nil {
		t.Fatalf("admit %s: %v", a.HoldID, err)
	}
	return v
}

func sgInt(t *testing.T, rdb redis.UniversalClient, key string) int64 {
	t.Helper()
	n, err := rdb.Get(bg, key).Int64()
	if err != nil && !errors.Is(err, redis.Nil) {
		t.Fatalf("get %s: %v", key, err)
	}
	return n
}

// The org and key minute buckets count what each admission holds; the
// minute window is the current bucket, the hour window the last 60, and
// resets_at is when the oldest counted bucket leaves the window that tripped.
func TestSpendGuardWindowMath(t *testing.T) {
	s, rdb, prefix := spendGuardTestStore(t)
	org := "org-w"
	limits := SpendGuardLimits{MinuteMicros: 10_000, HourMicros: 25_000}

	// Minute 0: 6,000. Minute 20: 6,000, then 4,000 more fits the minute cap.
	for i, at := range []time.Time{sgT0, sgT0.Add(20 * time.Minute), sgT0.Add(20*time.Minute + 30*time.Second)} {
		est := int64(6_000)
		if i == 2 {
			est = 4_000
		}
		v := sgAdmit(t, s, SpendGuardAdmission{Org: org, KeyID: "k1", HoldID: fmt.Sprintf("h%d", i), EstimateMicros: est, Limits: limits, Now: at})
		if v.Outcome != SpendGuardAdmitted {
			t.Fatalf("admission %d: %+v", i, v)
		}
	}
	m0, m20 := sgMinute(sgT0), sgMinute(sgT0.Add(20*time.Minute))
	base := prefix + "sg:{" + org + "}:"
	if got := sgInt(t, rdb, fmt.Sprintf("%sm:%d", base, m0)); got != 6_000 {
		t.Fatalf("org bucket minute 0 = %d, want 6000", got)
	}
	if got := sgInt(t, rdb, fmt.Sprintf("%sm:%d", base, m20)); got != 10_000 {
		t.Fatalf("org bucket minute 20 = %d, want 10000", got)
	}
	if got := sgInt(t, rdb, fmt.Sprintf("%sk:k1:m:%d", base, m20)); got != 10_000 {
		t.Fatalf("key bucket minute 20 = %d, want 10000", got)
	}
	ttl := rdb.PTTL(bg, fmt.Sprintf("%sm:%d", base, m20)).Val()
	if ttl <= 0 || ttl > 2*time.Hour+time.Minute {
		t.Fatalf("bucket TTL %v, want about 2 hours from its minute", ttl)
	}

	// Minute 20 is full: one more micro trips the minute window, which
	// resets when minute 20 ends.
	at := sgT0.Add(20*time.Minute + 40*time.Second)
	v := sgAdmit(t, s, SpendGuardAdmission{Org: org, KeyID: "k2", HoldID: "h-minute", EstimateMicros: 1, Limits: limits, Now: at})
	want := time.UnixMilli((m20 + 1) * 60_000).UTC()
	if v.Outcome != SpendGuardOverCap || v.Scope != SpendGuardScopeOrg || v.Window != SpendGuardWindowMinute ||
		v.CountedMicros != 10_000 || v.CapMicros != 10_000 || !v.ResetsAt.Equal(want) {
		t.Fatalf("over the minute cap: %+v, want org minute window resetting at %v", v, want)
	}

	// Minute 21: the hour window holds 16,000; 9,001 more trips it, and it
	// resets when minute 0's bucket (the oldest counted) leaves the hour.
	at = sgT0.Add(21 * time.Minute)
	v = sgAdmit(t, s, SpendGuardAdmission{Org: org, KeyID: "k2", HoldID: "h-hour", EstimateMicros: 9_001, Limits: limits, Now: at})
	want = time.UnixMilli((m0 + 60) * 60_000).UTC()
	if v.Outcome != SpendGuardOverCap || v.Window != SpendGuardWindowHour || v.CountedMicros != 16_000 || !v.ResetsAt.Equal(want) {
		t.Fatalf("over the hour cap: %+v, want hour window resetting at %v", v, want)
	}
	// 9,000 fits exactly (16,000 + 9,000 = the cap), and nothing refused was added.
	v = sgAdmit(t, s, SpendGuardAdmission{Org: org, KeyID: "k2", HoldID: "h-fits", EstimateMicros: 9_000, Limits: limits, Now: at})
	if v.Outcome != SpendGuardAdmitted {
		t.Fatalf("an estimate that reaches the cap exactly: %+v", v)
	}
	if got := sgInt(t, rdb, fmt.Sprintf("%sm:%d", base, m20+1)); got != 9_000 {
		t.Fatalf("org bucket minute 21 = %d, want 9000 (the refused 9,001 added nothing)", got)
	}

	// Once minute 0 leaves the hour (minute 60), the window has room again.
	at = sgT0.Add(60 * time.Minute)
	v = sgAdmit(t, s, SpendGuardAdmission{Org: org, KeyID: "k2", HoldID: "h-later", EstimateMicros: 5_000, Limits: limits, Now: at})
	if v.Outcome != SpendGuardAdmitted {
		t.Fatalf("after the oldest bucket left the hour: %+v", v)
	}
}

// The key's own window is checked before the org's: one key that alone
// breaks a cap is named (scope key), and keys that only break it together
// trip the org's window.
func TestSpendGuardPerKeyAndOrgWindows(t *testing.T) {
	s, _, _ := spendGuardTestStore(t)
	limits := SpendGuardLimits{MinuteMicros: 10_000}
	admit := func(key, hold string, est int64) SpendGuardVerdict {
		return sgAdmit(t, s, SpendGuardAdmission{Org: "org-k", KeyID: key, HoldID: hold, EstimateMicros: est, Limits: limits, Now: sgT0})
	}
	if v := admit("a", "a1", 8_000); v.Outcome != SpendGuardAdmitted {
		t.Fatalf("a1: %+v", v)
	}
	if v := admit("a", "a2", 3_000); v.Outcome != SpendGuardOverCap || v.Scope != SpendGuardScopeKey || v.CountedMicros != 8_000 {
		t.Fatalf("key a alone over the cap: %+v, want scope key", v)
	}
	if v := admit("b", "b1", 2_000); v.Outcome != SpendGuardAdmitted {
		t.Fatalf("b1: %+v", v)
	}
	if v := admit("b", "b2", 1_000); v.Outcome != SpendGuardOverCap || v.Scope != SpendGuardScopeOrg || v.CountedMicros != 10_000 {
		t.Fatalf("keys a and b together over the cap: %+v, want scope org", v)
	}
	// A caller with no key (a runtime token, an MCP caller) has only the org window.
	if v := admit("", "n1", 1); v.Outcome != SpendGuardOverCap || v.Scope != SpendGuardScopeOrg {
		t.Fatalf("keyless admission: %+v", v)
	}
}

// Held micros count: ten parallel admissions of $0.004 against a $0.01
// per-minute cap, none settled, admit exactly two; a void gives an estimate
// back to the window.
func TestSpendGuardParallelAdmissionsCountHeld(t *testing.T) {
	s, rdb, prefix := spendGuardTestStore(t)
	limits := SpendGuardLimits{MinuteMicros: 10_000}
	var wg sync.WaitGroup
	var mu sync.Mutex
	var admitted []string
	refused := 0
	for i := 0; i < 10; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			hold := fmt.Sprintf("p%d", i)
			v, err := s.Admit(bg, SpendGuardAdmission{Org: "org-p", KeyID: "k", HoldID: hold, EstimateMicros: 4_000, Limits: limits, Now: sgT0})
			mu.Lock()
			defer mu.Unlock()
			switch {
			case err != nil:
				t.Errorf("admit %s: %v", hold, err)
			case v.Outcome == SpendGuardAdmitted:
				admitted = append(admitted, hold)
			case v.Outcome == SpendGuardOverCap:
				refused++
			default:
				t.Errorf("admit %s: %+v", hold, v)
			}
		}(i)
	}
	wg.Wait()
	if len(admitted) != 2 || refused != 8 {
		t.Fatalf("%d admitted, %d refused; want 2 and 8 (the third $0.004 does not fit $0.01)", len(admitted), refused)
	}
	bucket := fmt.Sprintf("%ssg:{org-p}:m:%d", prefix, sgMinute(sgT0))
	if got := sgInt(t, rdb, bucket); got != 8_000 {
		t.Fatalf("window holds %d, want the two held estimates (8000)", got)
	}
	applied, err := s.Void(bg, "org-p", admitted[0])
	if err != nil || !applied {
		t.Fatalf("void: applied %v, %v", applied, err)
	}
	if got := sgInt(t, rdb, bucket); got != 4_000 {
		t.Fatalf("after a void the window holds %d, want 4000", got)
	}
	if v := sgAdmit(t, s, SpendGuardAdmission{Org: "org-p", KeyID: "k", HoldID: "p-next", EstimateMicros: 4_000, Limits: limits, Now: sgT0}); v.Outcome != SpendGuardAdmitted {
		t.Fatalf("the voided estimate's room: %+v", v)
	}
}

// Settle replaces the estimate with what was billed, in the bucket the
// estimate went into even when it settles a minute later; void takes the
// estimate out; either applies once per hold id. A repeated admission of a
// held id adds nothing.
func TestSpendGuardSettleAndVoidCorrectTheAdmissionBucket(t *testing.T) {
	s, rdb, prefix := spendGuardTestStore(t)
	base := prefix + "sg:{org-s}:"
	m0 := sgMinute(sgT0)
	adm := SpendGuardAdmission{Org: "org-s", KeyID: "k", HoldID: "h1", EstimateMicros: 5_000, Now: sgT0}
	sgAdmit(t, s, adm)
	if v := sgAdmit(t, s, adm); v.Outcome != SpendGuardAdmitted {
		t.Fatalf("repeated admission: %+v", v)
	}
	orgBucket, keyBucket := fmt.Sprintf("%sm:%d", base, m0), fmt.Sprintf("%sk:k:m:%d", base, m0)
	dayBucket := fmt.Sprintf("%sd:%d", base, sgT0.UnixMilli()/86_400_000)
	for _, k := range []string{orgBucket, keyBucket, dayBucket} {
		if got := sgInt(t, rdb, k); got != 5_000 {
			t.Fatalf("%s = %d after a repeated admission, want 5000", k, got)
		}
	}

	sums, applied, err := s.Settle(bg, "org-s", "h1", 1_200, sgT0.Add(70*time.Second))
	if err != nil || !applied {
		t.Fatalf("settle: applied %v, %v", applied, err)
	}
	for _, k := range []string{orgBucket, keyBucket, dayBucket} {
		if got := sgInt(t, rdb, k); got != 1_200 {
			t.Fatalf("%s = %d after settle, want the billed 1200", k, got)
		}
	}
	if got := rdb.Exists(bg, fmt.Sprintf("%sm:%d", base, m0+1)).Val(); got != 0 {
		t.Fatal("settling a minute later wrote the settle minute's bucket")
	}
	if sums.Last5MinMicros != 1_200 || sums.LastHourMicros != 1_200 || sums.Prior60MinMicros != 0 {
		t.Fatalf("sums after settle = %+v", sums)
	}
	if _, applied, _ := s.Settle(bg, "org-s", "h1", 9_999, sgT0); applied {
		t.Fatal("a second settle of the hold applied")
	}
	if applied, _ := s.Void(bg, "org-s", "h1"); applied {
		t.Fatal("a void after the settle applied")
	}
	if got := sgInt(t, rdb, orgBucket); got != 1_200 {
		t.Fatalf("org bucket = %d after the repeats, want 1200", got)
	}

	// Billed more than held: the window counts the actual amount.
	sgAdmit(t, s, SpendGuardAdmission{Org: "org-s", HoldID: "h2", EstimateMicros: 1_000, Now: sgT0})
	if _, applied, _ := s.Settle(bg, "org-s", "h2", 3_500, sgT0); !applied {
		t.Fatal("settle h2 did not apply")
	}
	if got := sgInt(t, rdb, orgBucket); got != 4_700 {
		t.Fatalf("org bucket = %d, want 1200 + 3500", got)
	}
	// An unknown hold id is a no-op.
	if applied, err := s.Void(bg, "org-s", "never-admitted"); applied || err != nil {
		t.Fatalf("void of an unknown hold: applied %v, %v", applied, err)
	}
}

// The settle sums are what anomaly detection reads: the last 5 minutes, the
// 60 minutes before them, and the last hour.
func TestSpendGuardSettleSums(t *testing.T) {
	s, _, _ := spendGuardTestStore(t)
	org := "org-a"
	settleAt := func(hold string, at time.Time, billed int64) SpendGuardSums {
		sgAdmit(t, s, SpendGuardAdmission{Org: org, HoldID: hold, EstimateMicros: billed, Now: at})
		sums, applied, err := s.Settle(bg, org, hold, billed, at)
		if err != nil || !applied {
			t.Fatalf("settle %s: %v %v", hold, applied, err)
		}
		return sums
	}
	now := sgT0.Add(90 * time.Minute)
	settleAt("old", now.Add(-70*time.Minute), 100_000) // outside every window
	settleAt("prior", now.Add(-30*time.Minute), 60_000)
	settleAt("edge", now.Add(-5*time.Minute), 7_000) // the minute just before the 5-minute window
	got := settleAt("now", now, 2_000_000)
	want := SpendGuardSums{Last5MinMicros: 2_000_000, Prior60MinMicros: 67_000, LastHourMicros: 2_067_000}
	if got != want {
		t.Fatalf("sums = %+v, want %+v", got, want)
	}
}

// The new-org daily cap applies for 7 days after the org's first admitted
// request, counts held estimates by UTC day, and resets at midnight UTC.
func TestSpendGuardNewOrgDailyCap(t *testing.T) {
	s, _, _ := spendGuardTestStore(t)
	org := "org-new"
	limits := SpendGuardLimits{NewOrgDailyMicros: 50_000}
	admit := func(hold string, at time.Time, est int64) SpendGuardVerdict {
		return sgAdmit(t, s, SpendGuardAdmission{Org: org, HoldID: hold, EstimateMicros: est, Limits: limits, Now: at})
	}
	if v := admit("d1", sgT0, 30_000); v.Outcome != SpendGuardAdmitted {
		t.Fatalf("first request: %+v", v)
	}
	v := admit("d2", sgT0.Add(time.Hour), 30_000)
	midnight := time.Date(2026, 10, 19, 0, 0, 0, 0, time.UTC)
	if v.Outcome != SpendGuardNewOrgCap || v.Scope != SpendGuardScopeOrg || v.Window != SpendGuardWindowDay ||
		v.CountedMicros != 30_000 || v.CapMicros != 50_000 || !v.ResetsAt.Equal(midnight) {
		t.Fatalf("over the new-org cap: %+v, want the day window resetting at %v", v, midnight)
	}
	// The next UTC day starts a new count, still within the 7 days.
	if v := admit("d3", midnight.Add(time.Minute), 30_000); v.Outcome != SpendGuardAdmitted {
		t.Fatalf("next day: %+v", v)
	}
	if v := admit("d4", sgT0.Add(7*24*time.Hour-time.Minute), 60_000); v.Outcome != SpendGuardNewOrgCap {
		t.Fatalf("day 7, before the 7 days end: %+v, want the cap", v)
	}
	if v := admit("d5", sgT0.Add(7*24*time.Hour), 60_000); v.Outcome != SpendGuardAdmitted {
		t.Fatalf("7 days after the first request: %+v, want no cap", v)
	}

	// The 7 days start at the org's first managed request even when the cap
	// refused it, so an org whose first requests are all refused is not new
	// forever.
	refusedOrg := "org-refused-first"
	first := func(hold string, at time.Time) SpendGuardVerdict {
		return sgAdmit(t, s, SpendGuardAdmission{Org: refusedOrg, HoldID: hold, EstimateMicros: 60_000, Limits: limits, Now: at})
	}
	if v := first("r1", sgT0); v.Outcome != SpendGuardNewOrgCap {
		t.Fatalf("a first request over the cap: %+v", v)
	}
	if v := first("r2", sgT0.Add(7*24*time.Hour)); v.Outcome != SpendGuardAdmitted {
		t.Fatalf("7 days after a refused first request: %+v, want no cap", v)
	}
}

// The EE budget enforcer's pause:<org>:managed_spend (no hash tag, as EE
// writes it) and the auto-pause mc:{<org>}:paused refuse every admission,
// after the new-org cap and before the windows; Unpause clears only the
// auto-pause.
func TestSpendGuardPauseKeys(t *testing.T) {
	s, rdb, prefix := spendGuardTestStore(t)
	org := "org-pause"
	limits := SpendGuardLimits{MinuteMicros: 1, NewOrgDailyMicros: 1_000}
	adm := SpendGuardAdmission{Org: org, HoldID: "x", EstimateMicros: 10, Limits: limits, Now: sgT0}

	rdb.Set(bg, prefix+"pause:"+org+":managed_spend", "1", 0)
	if v := sgAdmit(t, s, adm); v.Outcome != SpendGuardBudgetPaused {
		t.Fatalf("EE pause: %+v", v)
	}
	rdb.Del(bg, prefix+"pause:"+org+":managed_spend")
	rdb.Set(bg, prefix+"mc:{"+org+"}:paused", "1", 0)
	if v := sgAdmit(t, s, adm); v.Outcome != SpendGuardAutoPaused {
		t.Fatalf("auto-pause: %+v", v)
	}
	// The new-org cap is checked first.
	if v := sgAdmit(t, s, SpendGuardAdmission{Org: org, HoldID: "big", EstimateMicros: 5_000, Limits: limits, Now: sgT0}); v.Outcome != SpendGuardNewOrgCap {
		t.Fatalf("new-org cap before the pause: %+v", v)
	}
	cleared, err := s.Unpause(bg, org)
	if err != nil || !cleared {
		t.Fatalf("unpause: %v %v", cleared, err)
	}
	if cleared, _ := s.Unpause(bg, org); cleared {
		t.Fatal("a second unpause reported a cleared pause")
	}
	if v := sgAdmit(t, s, SpendGuardAdmission{Org: org, HoldID: "y", EstimateMicros: 1, Limits: SpendGuardLimits{}, Now: sgT0}); v.Outcome != SpendGuardAdmitted {
		t.Fatalf("after unpause: %+v", v)
	}
}

// An anomaly is marked once per org per hour, and the mark sets the
// auto-pause only when asked.
func TestSpendGuardMarkAnomalyOncePerHour(t *testing.T) {
	s, rdb, prefix := spendGuardTestStore(t)
	first, err := s.MarkAnomaly(bg, "org-x", false)
	if err != nil || !first {
		t.Fatalf("first mark: %v %v", first, err)
	}
	if again, _ := s.MarkAnomaly(bg, "org-x", true); again {
		t.Fatal("a second mark within the hour was first")
	}
	if rdb.Exists(bg, prefix+"mc:{org-x}:paused").Val() != 0 {
		t.Fatal("a mark that was not first paused the org")
	}
	if ttl := rdb.TTL(bg, prefix+"sg:{org-x}:alerted").Val(); ttl <= 59*time.Minute || ttl > time.Hour {
		t.Fatalf("alert mark TTL %v, want an hour", ttl)
	}
	if first, _ := s.MarkAnomaly(bg, "org-y", true); !first {
		t.Fatal("org-y's first mark")
	}
	if rdb.Exists(bg, prefix+"mc:{org-y}:paused").Val() != 1 {
		t.Fatal("an auto-pause mark did not pause the org")
	}
}

// Without Redis every operation fails closed with billing.ErrUnavailable,
// and an org id that would break the hash tag is refused.
func TestSpendGuardStoreUnavailable(t *testing.T) {
	s := NewSpendGuardStore(nil, "")
	if _, err := s.Admit(bg, SpendGuardAdmission{Org: "o", HoldID: "h", Now: sgT0}); !errors.Is(err, billing.ErrUnavailable) {
		t.Fatalf("admit without Redis: %v", err)
	}
	if _, _, err := s.Settle(bg, "o", "h", 1, sgT0); !errors.Is(err, billing.ErrUnavailable) {
		t.Fatalf("settle without Redis: %v", err)
	}
	if _, err := s.Unpause(bg, "o"); !errors.Is(err, billing.ErrUnavailable) {
		t.Fatalf("unpause without Redis: %v", err)
	}
	if _, err := s.Admit(bg, SpendGuardAdmission{Org: "a{b}", HoldID: "h", Now: sgT0}); err == nil || errors.Is(err, billing.ErrUnavailable) {
		t.Fatalf("an org id with a brace: %v, want refused", err)
	}
	if _, err := s.Admit(bg, SpendGuardAdmission{Org: "o", Now: sgT0}); err == nil || errors.Is(err, billing.ErrUnavailable) {
		t.Fatalf("an empty hold id: %v, want refused", err)
	}
}
