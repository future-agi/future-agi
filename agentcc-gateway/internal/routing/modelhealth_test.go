package routing

import (
	"fmt"
	"math"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// fakeClock is a settable clock for the health window.
type fakeClock struct {
	mu sync.Mutex
	t  time.Time
}

func (c *fakeClock) now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.t
}

func (c *fakeClock) advance(d time.Duration) {
	c.mu.Lock()
	c.t = c.t.Add(d)
	c.mu.Unlock()
}

func newTestModelHealth() (*ModelHealth, *fakeClock) {
	clock := &fakeClock{t: time.Date(2026, 10, 15, 12, 0, 0, 0, time.UTC)}
	mh := NewModelHealth()
	mh.now = clock.now
	return mh, clock
}

func within(got, want, tolerance float64) bool {
	return math.Abs(got-want) <= want*tolerance
}

func TestModelHealthWindow(t *testing.T) {
	mh, clock := newTestModelHealth()
	var _ ModelHealthReader = mh
	var _ ModelHealthRecorder = mh

	if snap := mh.Snapshot("openrouter", "vendor/a"); snap != (ModelHealthSnapshot{}) {
		t.Fatalf("an unseen model has snapshot %+v, want the zero snapshot", snap)
	}

	// 18 successes at 100 ms, then 2 rate-limited calls at 1 s: 20 calls,
	// 90% success, p50 near 100 ms and p95 near 1 s.
	for i := 0; i < 18; i++ {
		mh.Record("openrouter", "vendor/a", 100*time.Millisecond, "")
	}
	for i := 0; i < 2; i++ {
		mh.Record("openrouter", "vendor/a", time.Second, models.ErrClassRateLimited)
	}
	snap := mh.Snapshot("openrouter", "vendor/a")
	if snap.N != 20 || !within(snap.SuccessRate, 0.9, 1e-9) || !within(snap.ErrorRate, 0.1, 1e-9) {
		t.Fatalf("snapshot = %+v, want n=20, success 0.9, error 0.1", snap)
	}
	if !within(snap.P50Ms, 100, 0.08) || !within(snap.P95Ms, 1000, 0.08) {
		t.Fatalf("p50 = %.1f ms, p95 = %.1f ms; want about 100 and 1000", snap.P50Ms, snap.P95Ms)
	}
	if snap.LastErrorClass != string(models.ErrClassRateLimited) {
		t.Fatalf("last error class = %q, want rate_limited", snap.LastErrorClass)
	}

	// Models on the same provider are kept apart (managed OpenRouter models
	// all share one provider id), and so are providers serving one model.
	if other := mh.Snapshot("openrouter", "vendor/b"); other.N != 0 {
		t.Fatalf("vendor/b shares vendor/a's window: %+v", other)
	}
	if other := mh.Snapshot("direct", "vendor/a"); other.N != 0 {
		t.Fatalf("another provider shares the window: %+v", other)
	}

	// Calls older than the window drop out, a slot at a time.
	clock.advance(3 * time.Minute)
	for i := 0; i < 5; i++ {
		mh.Record("openrouter", "vendor/a", 200*time.Millisecond, "")
	}
	if snap := mh.Snapshot("openrouter", "vendor/a"); snap.N != 25 {
		t.Fatalf("3 minutes later n = %d, want all 25 calls still in the 5-minute window", snap.N)
	}
	clock.advance(2*time.Minute + 30*time.Second)
	snap = mh.Snapshot("openrouter", "vendor/a")
	if snap.N != 5 || snap.SuccessRate != 1 || snap.ErrorRate != 0 || !within(snap.P50Ms, 200, 0.08) {
		t.Fatalf("after the first calls aged out: %+v, want the 5 recent successes at 200 ms", snap)
	}
	if snap.LastErrorClass != "" {
		t.Fatalf("an error that left the window still shows: %q", snap.LastErrorClass)
	}
	clock.advance(5 * time.Minute)
	if snap := mh.Snapshot("openrouter", "vendor/a"); snap != (ModelHealthSnapshot{}) {
		t.Fatalf("after 5 idle minutes the snapshot is %+v, want empty", snap)
	}
}

func TestModelHealthPercentilesSpanTheRange(t *testing.T) {
	mh, _ := newTestModelHealth()
	for _, ms := range []int{0, 3, 45, 2500, 70000, 900000} {
		mh.Record("p", "m", time.Duration(ms)*time.Millisecond, "")
	}
	snap := mh.Snapshot("p", "m")
	if snap.N != 6 || snap.P50Ms < 3 || snap.P50Ms > 2500 || snap.P95Ms < 70000 {
		t.Fatalf("snapshot over 0 ms..15 min = %+v", snap)
	}
}

// The window keeps at most maxHealthModels keys: past it, a model idle for
// the whole window is forgotten first, then the least recently recorded.
func TestModelHealthBoundsItsKeys(t *testing.T) {
	mh, clock := newTestModelHealth()
	mh.maxModels = 3
	mh.Record("p", "old", time.Millisecond, "")
	clock.advance(6 * time.Minute)
	mh.Record("p", "a", time.Millisecond, "")
	clock.advance(time.Millisecond)
	mh.Record("p", "b", time.Millisecond, "")
	clock.advance(time.Millisecond)
	mh.Record("p", "c", time.Millisecond, "") // evicts the idle "old"
	if got := mh.size(); got != 3 {
		t.Fatalf("tracking %d models, want 3", got)
	}
	clock.advance(time.Second)
	mh.Record("p", "a", time.Millisecond, "")
	mh.Record("p", "d", time.Millisecond, "") // evicts "b", the least recently recorded
	if mh.Snapshot("p", "b").N != 0 || mh.Snapshot("p", "a").N != 2 || mh.Snapshot("p", "d").N != 1 {
		t.Fatalf("eviction kept the wrong models: a=%+v b=%+v d=%+v", mh.Snapshot("p", "a"), mh.Snapshot("p", "b"), mh.Snapshot("p", "d"))
	}
}

func TestModelHealthConcurrent(t *testing.T) {
	mh := NewModelHealth()
	var wg sync.WaitGroup
	for g := 0; g < 8; g++ {
		wg.Add(1)
		go func(g int) {
			defer wg.Done()
			for i := 0; i < 500; i++ {
				model := fmt.Sprintf("m%d", i%4)
				class := models.ErrorClass("")
				if i%5 == 0 {
					class = models.ErrClassUpstream5xx
				}
				mh.Record("p", model, time.Duration(i)*time.Millisecond, class)
				_ = mh.Snapshot("p", model)
			}
		}(g)
	}
	wg.Wait()
	total := 0
	for i := 0; i < 4; i++ {
		total += mh.Snapshot("p", fmt.Sprintf("m%d", i)).N
	}
	if total != 8*500 {
		t.Fatalf("recorded %d calls, want %d", total, 8*500)
	}
}

// Only an attempt whose class speaks about the upstream feeds its model's
// health, which every org on the node shares: one org's bad requests, revoked
// key or empty balance, and the gateway's own refusals, must not move it. Such
// an attempt makes no window either, so names an upstream rejects cannot fill
// the store.
func TestModelHealthCountsOnlyUpstreamFailures(t *testing.T) {
	mh, _ := newTestModelHealth()
	ignored := []models.ErrorClass{
		models.ErrClassAuth, models.ErrClassInvalidRequest, models.ErrClassContextLength,
		models.ErrClassContentFilter, models.ErrClassUpstream4xx, models.ErrClassInsufficientCredits,
		models.ErrClassPolicy, models.ErrClassBudget, models.ErrClassCircuitOpen,
		models.ErrClassCancelled, models.ErrClassUnknown,
	}
	for _, class := range ignored {
		for i := 0; i < 20; i++ {
			mh.Record("openrouter", "vendor/a", 50*time.Millisecond, class)
		}
	}
	if snap := mh.Snapshot("openrouter", "vendor/a"); snap.N != 0 {
		t.Fatalf("failures the upstream did not cause fed the health: %+v", snap)
	}
	if n := mh.size(); n != 0 {
		t.Fatalf("ignored attempts made %d windows, want none", n)
	}

	counted := []models.ErrorClass{models.ErrClassRateLimited, models.ErrClassTimeout, models.ErrClassUpstream5xx, models.ErrClassNetwork}
	for _, class := range counted {
		for i := 0; i < 5; i++ {
			mh.Record("openrouter", "vendor/b", 50*time.Millisecond, class)
		}
	}
	snap := mh.Snapshot("openrouter", "vendor/b")
	if snap.N != 20 || snap.SuccessRate != 0 || snap.ErrorRate != 1 || snap.LastErrorClass != string(models.ErrClassNetwork) {
		t.Fatalf("20 upstream failures gave %+v, want n=20 and success rate 0", snap)
	}
}
