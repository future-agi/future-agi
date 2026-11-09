package affinity

import (
	"context"
	"errors"
	"strconv"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

type fakeClock struct {
	mu sync.Mutex
	t  time.Time
}

func newClock() *fakeClock { return &fakeClock{t: time.Date(2026, 10, 16, 9, 0, 0, 0, time.UTC)} }

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

// fakeBackend is a Backend that keeps pins in a map, or fails every call
// while down is set.
type fakeBackend struct {
	mu   sync.Mutex
	down bool
	pins map[string]Value
}

var errDown = errors.New("backend down")

func (b *fakeBackend) GetAffinity(_ context.Context, key string) (Value, bool, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.down {
		return Value{}, false, errDown
	}
	v, ok := b.pins[key]
	return v, ok, nil
}

func (b *fakeBackend) SetAffinity(_ context.Context, key string, v Value, _ time.Duration) error {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.down {
		return errDown
	}
	if b.pins == nil {
		b.pins = map[string]Value{}
	}
	b.pins[key] = v
	return nil
}

func (b *fakeBackend) DelAffinity(_ context.Context, key string) error {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.down {
		return errDown
	}
	delete(b.pins, key)
	return nil
}

func (b *fakeBackend) setDown(down bool) {
	b.mu.Lock()
	b.down = down
	b.mu.Unlock()
}

var bg = context.Background()

func TestAffinityClassOfSteps(t *testing.T) {
	want := map[models.Step]Class{
		models.StepPlan: ClassMain, models.StepAct: ClassMain, models.StepTool: ClassMain,
		models.StepExtract: ClassMain, models.StepFinal: ClassMain, models.StepSummarize: ClassMain,
		models.StepReflect: ClassOrgan, models.StepMemory: ClassOrgan, models.StepCoordinate: ClassOrgan,
		models.StepVerify: ClassVerify,
	}
	for _, step := range models.Steps {
		got, ok := ClassOf(step)
		if w, listed := want[step]; listed != ok || got != w {
			t.Errorf("ClassOf(%q) = %q, %v; want %q, %v", step, got, ok, w, listed)
		}
	}
	if _, ok := ClassOf(""); ok {
		t.Error("an empty step has a class")
	}
}

func TestAffinitySubjectSessionThenRun(t *testing.T) {
	for _, c := range []struct {
		agent models.AgentContext
		want  string
	}{
		{models.AgentContext{SessionID: "s1", RunID: "r1"}, "s1"},
		{models.AgentContext{RunID: "r1"}, "run:r1"},
		{models.AgentContext{AgentID: "a"}, ""},
	} {
		if got := Subject(c.agent); got != c.want {
			t.Errorf("Subject(%+v) = %q, want %q", c.agent, got, c.want)
		}
	}
}

func TestAffinityKeyShape(t *testing.T) {
	if got, want := Key("org-1", "run:r1", ClassVerify), "aff:{org-1}:run:r1:verify"; got != want {
		t.Errorf("Key = %q, want %q", got, want)
	}
}

func TestAffinityTTLExpiry(t *testing.T) {
	clock := newClock()
	s := NewStore(nil, 10, clock.now)
	pin := Value{Model: "vendor/m", Provider: "openrouter", RequestID: "req-1", PrefixTokens: 1200}
	s.Set(bg, "org", "s1", ClassMain, pin, 30*time.Minute)
	clock.advance(30*time.Minute - time.Second)
	if got, ok := s.Get(bg, "org", "s1", ClassMain); !ok || got != pin {
		t.Fatalf("before the TTL: Get = %+v, %v; want %+v", got, ok, pin)
	}
	clock.advance(time.Second)
	if _, ok := s.Get(bg, "org", "s1", ClassMain); ok {
		t.Fatal("the pin outlived its TTL")
	}
}

func TestAffinityLRUFallbackWhenBackendDown(t *testing.T) {
	backend := &fakeBackend{}
	s := NewStore(backend, 10, newClock().now)
	backend.setDown(true)
	pin := Value{Model: "vendor/m", RequestID: "req-1"}
	s.Set(bg, "org", "s1", ClassMain, pin, time.Hour)
	if got, ok := s.Get(bg, "org", "s1", ClassMain); !ok || got != pin {
		t.Fatalf("with the backend down: Get = %+v, %v; want the cached pin", got, ok)
	}
	// Back up: the pin written during the outage is still found, and a new
	// one goes to the backend.
	backend.setDown(false)
	if got, ok := s.Get(bg, "org", "s1", ClassMain); !ok || got != pin {
		t.Fatalf("after recovery: Get = %+v, %v; want the cached pin", got, ok)
	}
	next := Value{Model: "vendor/n", RequestID: "req-2"}
	s.Set(bg, "org", "s1", ClassMain, next, time.Hour)
	if got := backend.pins[Key("org", "s1", ClassMain)]; got != next {
		t.Fatalf("the backend holds %+v, want %+v", got, next)
	}
	if got, _ := s.Get(bg, "org", "s1", ClassMain); got != next {
		t.Fatalf("Get = %+v, want the backend's %+v", got, next)
	}
}

func TestAffinityBreakRemovesBothCopies(t *testing.T) {
	backend := &fakeBackend{}
	s := NewStore(backend, 10, newClock().now)
	backend.setDown(true)
	s.Set(bg, "org", "s1", ClassMain, Value{Model: "old"}, time.Hour) // cached
	backend.setDown(false)
	s.Set(bg, "org", "s1", ClassMain, Value{Model: "new"}, time.Hour) // in the backend
	s.Break(bg, "org", "s1", ClassMain)
	if v, ok := s.Get(bg, "org", "s1", ClassMain); ok {
		t.Fatalf("a broken pin is still found: %+v", v)
	}
}

func TestAffinityLRUEvictsLeastRecentlyUsed(t *testing.T) {
	c := NewLRU[int](2, newClock().now)
	c.Set("a", 1, time.Hour)
	c.Set("b", 2, time.Hour)
	c.Get("a") // b is now the least recently used
	c.Set("c", 3, time.Hour)
	if _, ok := c.Get("b"); ok {
		t.Error("b was kept past the capacity")
	}
	for k, want := range map[string]int{"a": 1, "c": 3} {
		if got, ok := c.Get(k); !ok || got != want {
			t.Errorf("Get(%q) = %d, %v; want %d", k, got, ok, want)
		}
	}
	if c.Len() != 2 {
		t.Errorf("Len = %d, want 2", c.Len())
	}
}

func TestAffinityDefaultMemoryHoldsOneHundredThousand(t *testing.T) {
	c := NewLRU[int](DefaultMemoryEntries, nil)
	for i := range DefaultMemoryEntries + 1 {
		c.Set(strconv.Itoa(i), i, time.Hour)
	}
	if c.Len() != DefaultMemoryEntries {
		t.Errorf("Len = %d, want %d", c.Len(), DefaultMemoryEntries)
	}
}
