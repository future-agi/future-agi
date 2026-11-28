package trajectory

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"
	"github.com/redis/go-redis/v9"
)

// seenV2T0 is a generation boundary for every reuse age used below.
var seenV2T0 = time.Date(2026, 11, 22, 0, 0, 0, 0, time.UTC)

const (
	seenV2Pid   = "11111111-1111-4111-8111-111111111111"
	seenV2Pid2  = "22222222-2222-4222-8222-222222222222"
	seenV2Sess  = "sess-1"
	seenV2Age   = 24 * time.Hour
	seenV2Rlen  = 12 * time.Hour
	seenV2HashA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	seenV2HashB = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
)

type seenV2Clock struct {
	mu sync.Mutex
	t  time.Time
}

func (c *seenV2Clock) now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.t
}

type seenV2Env struct {
	t     *testing.T
	mr    *miniredis.Miniredis
	rdb   *redis.Client
	clock *seenV2Clock
}

func newSeenV2Env(t *testing.T) *seenV2Env {
	t.Helper()
	mr := miniredis.RunT(t)
	mr.SetTime(seenV2T0)
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	t.Cleanup(func() { _ = rdb.Close() })
	return &seenV2Env{t: t, mr: mr, rdb: rdb, clock: &seenV2Clock{t: seenV2T0}}
}

// advance moves the store clock and the miniredis clock (expiring keys).
func (e *seenV2Env) advance(d time.Duration) {
	e.clock.mu.Lock()
	e.clock.t = e.clock.t.Add(d)
	now := e.clock.t
	e.clock.mu.Unlock()
	e.mr.SetTime(now)
	e.mr.FastForward(d)
}

// advanceClock moves only the store clock (for use after the Redis is closed).
func (e *seenV2Env) advanceClock(d time.Duration) {
	e.clock.mu.Lock()
	e.clock.t = e.clock.t.Add(d)
	e.clock.mu.Unlock()
}

func (e *seenV2Env) store(opts SeenOptions) *SeenStore {
	if opts.LRUSize == 0 {
		opts.LRUSize = 10_000
	}
	if opts.MaxReuseAge == 0 {
		opts.MaxReuseAge = seenV2Age
	}
	if opts.MemoryProbe == nil {
		opts.MemoryProbe = func(context.Context) (int64, int64, error) { return 0, 0, nil }
	}
	s := NewSeenStoreWithOptions(e.rdb, opts)
	s.nowFn = e.clock.now
	return s
}

func (e *seenV2Env) pod() *SeenStore { return e.store(SeenOptions{}) }

func (e *seenV2Env) epoch() string {
	e.t.Helper()
	v, err := e.mr.Get(SeenEpochKey)
	if err != nil {
		e.t.Fatalf("read %s: %v", SeenEpochKey, err)
	}
	return v
}

func (e *seenV2Env) seenKeys() []string {
	var out []string
	for _, k := range e.mr.Keys() {
		if strings.HasPrefix(k, seenKeyPrefix) {
			out = append(out, k)
		}
	}
	return out
}

func seenV2Items(pid string) []Item {
	return []Item{
		{ProjectID: pid, ScopeID: seenV2Sess, ItemHash: seenV2HashA, ItemType: "message", Content: `{"role":"user","content":"hello"}`},
		{ProjectID: pid, ScopeID: ProjectScopeID, ItemHash: seenV2HashB, ItemType: "message", Content: `{"role":"system","content":"rules"}`},
	}
}

func seenV2Bytes(items []Item) int64 {
	var n int64
	for _, it := range items {
		n += int64(len(it.Content))
	}
	return n
}

func seenV2Classes(c SeenCheck) []SeenClass {
	out := make([]SeenClass, len(c.Decisions))
	for i, d := range c.Decisions {
		out[i] = d.Class
	}
	return out
}

func seenV2WantAll(t *testing.T, label string, c SeenCheck, want SeenClass) {
	t.Helper()
	for i, d := range c.Decisions {
		if d.Class != want {
			t.Fatalf("%s: decision %d (%s) class=%s, want %s (all=%v)", label, i, d.Item.ScopeID, d.Class, want, seenV2Classes(c))
		}
	}
}

var (
	seenV2TokA = SeenCommitterToken("trace-A")
	seenV2TokB = SeenCommitterToken("trace-B")
	seenV2TokC = SeenCommitterToken("trace-C")
)

// A reused copy is never older than the reuse age; the class follows the
// generation distance exactly (0-1 reuse, 2-3 refresh, 4+ new) on both the
// pod-local and the cross-pod Redis path.
func TestSeenReuseBoundedByMaxReuseAge(t *testing.T) {
	for _, off := range []time.Duration{0, time.Hour, seenV2Rlen - time.Minute} {
		t.Run(off.String(), func(t *testing.T) {
			env := newSeenV2Env(t)
			env.advance(off)
			committedAt := env.clock.now()
			podA := env.pod()
			items := seenV2Items(seenV2Pid)
			podA.Commit(context.Background(), items, SeenStamp{Committer: seenV2TokA, At: committedAt})
			gc := seenGen(committedAt, seenV2Rlen)

			sawReuse, sawRefresh, sawNew := false, false, false
			for step := 0; step <= int(3*seenV2Age/(30*time.Minute)); step++ {
				now := env.clock.now()
				age := now.Sub(committedAt)
				diff := seenGen(now, seenV2Rlen) - gc
				want := SeenNew
				switch {
				case diff <= 1:
					want = SeenReuseOther
				case diff <= 3:
					want = SeenRefresh
				}
				local := podA.Check(context.Background(), items, seenV2TokB)
				remote := env.pod().Check(context.Background(), items, seenV2TokB)
				seenV2WantAll(t, fmt.Sprintf("local age=%s", age), local, want)
				seenV2WantAll(t, fmt.Sprintf("redis age=%s", age), remote, want)
				if want == SeenReuseOther {
					sawReuse = true
					if age >= seenV2Age {
						t.Fatalf("reused a copy aged %s >= max reuse age %s", age, seenV2Age)
					}
				}
				if age < seenV2Rlen && want != SeenReuseOther {
					t.Fatalf("age %s < R must reuse, got %s", age, want)
				}
				if want == SeenRefresh {
					sawRefresh = true
				}
				if want == SeenNew {
					sawNew = true
				}
				env.advance(30 * time.Minute)
			}
			if !sawReuse || !sawRefresh || !sawNew {
				t.Fatalf("classes not all exercised: reuse=%v refresh=%v new=%v", sawReuse, sawRefresh, sawNew)
			}
		})
	}
}

// The legacy constructor clamps the old 7-day project TTL to the 48h cap.
func TestSeenLegacyConstructorClampsReuseAge(t *testing.T) {
	// The pre-v2 defaults (24h session, 7-day project) as literals: the
	// config defaults may change, the clamp must not.
	s := NewSeenStore(nil, 100, 24*time.Hour, 7*24*time.Hour)
	if got := s.MaxReuseAge(); got != SeenMaxReuseAge {
		t.Fatalf("MaxReuseAge=%s, want %s", got, SeenMaxReuseAge)
	}
	now := seenV2T0
	s.nowFn = func() time.Time { return now }
	items := seenV2Items(seenV2Pid)
	s.MarkCommitted(context.Background(), items)
	now = now.Add(5 * 24 * time.Hour)
	if newItems, reused, _ := s.CheckNew(context.Background(), items); len(newItems) != 2 || reused != 0 {
		t.Fatalf("5 days later: new=%d reused=%d, want 2/0 (no reuse beyond 48h)", len(newItems), reused)
	}
	if s2 := NewSeenStore(nil, 100, time.Minute, 7*24*time.Hour); s2.MaxReuseAge() != SeenMaxReuseAge || s2.rSession != SeenMinReuseAge/2 {
		t.Fatalf("clamp: max=%s rSession=%s", s2.MaxReuseAge(), s2.rSession)
	}
}

// A Redis hit warms the pod-local cache with the generation of the hit, not
// with the time of the read, so a read never extends the reuse window.
func TestSeenRedisHitKeepsPersistedGeneration(t *testing.T) {
	env := newSeenV2Env(t)
	ctx := context.Background()
	items := seenV2Items(seenV2Pid)
	env.pod().Commit(ctx, items, SeenStamp{Committer: seenV2TokA})
	g0 := seenGen(seenV2T0, seenV2Rlen)

	env.advance(seenV2Rlen + time.Hour)
	podB := env.pod()
	seenV2WantAll(t, "podB in g0+1", podB.Check(ctx, items, seenV2TokB), SeenReuseOther)
	for _, it := range items {
		elem, ok := podB.entries[it.Key()]
		if !ok {
			t.Fatalf("podB LRU not warmed for %s", it.ScopeID)
		}
		if got := elem.Value.(*lruEntry).gen; got != g0 {
			t.Fatalf("podB LRU gen=%d, want hit generation %d (not now=%d)", got, g0, g0+1)
		}
	}

	// Remove the Redis copy; podB's LRU alone must not keep reusing past g0+1.
	for _, it := range items {
		env.mr.Del(SeenKey(it.ProjectID, env.epoch(), it.ScopeID, g0))
	}
	env.advance(seenV2Rlen)
	seenV2WantAll(t, "podB in g0+2 after Redis copy removed", podB.Check(ctx, items, seenV2TokB), SeenNew)
}

// Generation keys expire at (gen+4)*R + 1h; checks and later commits never
// move that deadline.
func TestSeenGenerationKeysExpireAt(t *testing.T) {
	env := newSeenV2Env(t)
	ctx := context.Background()
	items := seenV2Items(seenV2Pid)
	env.advance(time.Hour)
	podA := env.pod()
	podA.Commit(ctx, items[:1], SeenStamp{Committer: seenV2TokA})
	g0 := seenGen(env.clock.now(), seenV2Rlen)
	key := SeenKey(seenV2Pid, env.epoch(), seenV2Sess, g0)
	deadline := seenV2T0.Add(4*seenV2Rlen + time.Hour)
	if got, want := env.mr.TTL(key), deadline.Sub(env.clock.now()); got != want {
		t.Fatalf("TTL=%s, want %s (expire at (gen+4)*R+1h)", got, want)
	}

	env.advance(time.Hour)
	_ = env.pod().Check(ctx, items, seenV2TokB)
	env.pod().Commit(ctx, items[:1], SeenStamp{Committer: seenV2TokB})
	if got, want := env.mr.TTL(key), deadline.Sub(env.clock.now()); got != want {
		t.Fatalf("after check+commit TTL=%s, want %s (deadline unchanged)", got, want)
	}
	if v := env.mr.HGet(key, seenV2HashA); v != seenV2TokA {
		t.Fatalf("value=%q, want first committer %q", v, seenV2TokA)
	}

	env.advance(deadline.Sub(env.clock.now()) + time.Second)
	if env.mr.Exists(key) {
		t.Fatalf("key %s still present after its deadline", key)
	}
}

// Changing traj:epoch invalidates pod-local and Redis seen state within the
// 5 s epoch cache.
func TestSeenEpochChangeInvalidatesLRUAndRedis(t *testing.T) {
	env := newSeenV2Env(t)
	ctx := context.Background()
	items := seenV2Items(seenV2Pid)
	podA := env.pod()
	podA.Commit(ctx, items, SeenStamp{Committer: seenV2TokA})
	seenV2WantAll(t, "before reset", podA.Check(ctx, items, seenV2TokB), SeenReuseOther)
	oldEpoch := env.epoch()

	if err := env.mr.Set(SeenEpochKey, "reset-epoch"); err != nil {
		t.Fatal(err)
	}
	env.advance(2 * time.Second)
	seenV2WantAll(t, "within epoch cache", podA.Check(ctx, items, seenV2TokB), SeenReuseOther)
	env.advance(4 * time.Second)
	seenV2WantAll(t, "podA after epoch change", podA.Check(ctx, items, seenV2TokB), SeenNew)
	seenV2WantAll(t, "fresh pod after epoch change", env.pod().Check(ctx, items, seenV2TokB), SeenNew)

	podA.Commit(ctx, items, SeenStamp{Committer: seenV2TokA})
	for _, k := range env.seenKeys() {
		if !strings.Contains(k, ":reset-epoch:") && !strings.Contains(k, ":"+oldEpoch+":") {
			t.Fatalf("unexpected key %s", k)
		}
	}
	if !env.mr.Exists(SeenKey(seenV2Pid, "reset-epoch", seenV2Sess, seenGen(env.clock.now(), seenV2Rlen))) {
		t.Fatalf("commit after reset not written under the new epoch")
	}
	seenV2WantAll(t, "fresh pod after recommit", env.pod().Check(ctx, items, seenV2TokB), SeenReuseOther)
}

// Once the epoch has been read, an unreadable epoch means nothing is reused.
func TestSeenEpochUnreadableAfterKnownDisablesReuse(t *testing.T) {
	env := newSeenV2Env(t)
	ctx := context.Background()
	items := seenV2Items(seenV2Pid)
	podA := env.pod()
	podA.Commit(ctx, items, SeenStamp{Committer: seenV2TokA})
	seenV2WantAll(t, "redis up", podA.Check(ctx, items, seenV2TokB), SeenReuseOther)

	env.mr.Close()
	env.advanceClock(6 * time.Second)
	res := podA.Check(ctx, items, seenV2TokB)
	seenV2WantAll(t, "epoch unreadable", res, SeenNew)
	if !res.EpochUnknown || podA.Stats().EpochUnavailable == 0 {
		t.Fatalf("EpochUnknown=%v stats=%+v, want epoch unavailability reported", res.EpochUnknown, podA.Stats())
	}
	if len(res.Write()) != len(items) {
		t.Fatalf("Write()=%d, want %d (fail open writes the items again)", len(res.Write()), len(items))
	}
}

// Hits in g-2/g-3 are refreshes (written again, credited); older hits are new.
func TestSeenRefreshVsNewClassification(t *testing.T) {
	env := newSeenV2Env(t)
	ctx := context.Background()
	items := seenV2Items(seenV2Pid)
	env.pod().Commit(ctx, items, SeenStamp{Committer: seenV2TokA})

	env.advance(2 * seenV2Rlen)
	res := env.pod().Check(ctx, items, seenV2TokB)
	seenV2WantAll(t, "g0+2", res, SeenRefresh)
	if len(res.Write()) != 2 || res.CreditedItemBytes() != seenV2Bytes(items) || res.BilledItemBytes() != 0 {
		t.Fatalf("refresh: write=%d credited=%d billed=%d", len(res.Write()), res.CreditedItemBytes(), res.BilledItemBytes())
	}
	env.advance(seenV2Rlen)
	seenV2WantAll(t, "g0+3", env.pod().Check(ctx, items, seenV2TokB), SeenRefresh)

	env.advance(seenV2Rlen)
	res = env.pod().Check(ctx, items, seenV2TokB)
	seenV2WantAll(t, "g0+4", res, SeenNew)
	if len(res.Write()) != 2 || res.BilledItemBytes() != seenV2Bytes(items) || res.CreditedItemBytes() != 0 {
		t.Fatalf("new: write=%d billed=%d credited=%d", len(res.Write()), res.BilledItemBytes(), res.CreditedItemBytes())
	}
}

// Every v2 key is traj:seen:v2:<project>:<epoch>:<scope>:<gen>.
func TestSeenKeysAreProjectPrefixed(t *testing.T) {
	env := newSeenV2Env(t)
	ctx := context.Background()
	pod := env.pod()
	pod.Commit(ctx, append(seenV2Items(seenV2Pid), seenV2Items(seenV2Pid2)...), SeenStamp{Committer: seenV2TokA})
	epoch := env.epoch()
	keys := env.seenKeys()
	if len(keys) != 4 {
		t.Fatalf("keys=%v, want 4 (2 projects x 2 scopes)", keys)
	}
	for _, k := range keys {
		parts := strings.Split(strings.TrimPrefix(k, seenKeyPrefix), ":")
		if len(parts) != 4 {
			t.Fatalf("key %s: want project:epoch:scope:gen", k)
		}
		if parts[0] != seenV2Pid && parts[0] != seenV2Pid2 {
			t.Fatalf("key %s does not start with a project id", k)
		}
		if parts[1] != epoch {
			t.Fatalf("key %s epoch=%s, want %s", k, parts[1], epoch)
		}
		gen, err := strconv.ParseInt(parts[3], 10, 64)
		if err != nil || gen != seenGen(seenV2T0, seenV2Rlen) {
			t.Fatalf("key %s gen=%s", k, parts[3])
		}
		if typ := env.mr.Type(k); typ != "hash" {
			t.Fatalf("key %s type=%s, want hash", k, typ)
		}
	}
}

// Erasure clears every v2 and legacy seen key of one project and nothing else.
func TestSeenEraseProjectClearsV2AndLegacyKeys(t *testing.T) {
	env := newSeenV2Env(t)
	ctx := context.Background()
	pod := env.pod()
	pod.Commit(ctx, append(seenV2Items(seenV2Pid), seenV2Items(seenV2Pid2)...), SeenStamp{Committer: seenV2TokA})
	env.advance(seenV2Rlen)
	pod.Commit(ctx, seenV2Items(seenV2Pid)[:1], SeenStamp{Committer: seenV2TokA}) // a second generation
	for _, pid := range []string{seenV2Pid, seenV2Pid2} {
		if _, err := env.mr.SAdd("traj:seen:"+pid+":"+seenV2Sess, seenV2HashA); err != nil {
			t.Fatal(err)
		}
	}
	p1Keys := 0
	for _, k := range env.seenKeys() {
		if strings.HasPrefix(k, seenKeyPrefix+seenV2Pid+":") {
			p1Keys++
		}
	}

	n, err := pod.EraseProject(ctx, seenV2Pid)
	if err != nil {
		t.Fatalf("EraseProject: %v", err)
	}
	if n != p1Keys+1 {
		t.Fatalf("removed=%d, want %d (%d v2 + 1 legacy)", n, p1Keys+1, p1Keys)
	}
	for _, k := range env.mr.Keys() {
		if strings.Contains(k, seenV2Pid) {
			t.Fatalf("key %s survived erasure", k)
		}
	}
	if !env.mr.Exists("traj:seen:"+seenV2Pid2+":"+seenV2Sess) || len(env.seenKeys()) != 2 || !env.mr.Exists(SeenEpochKey) {
		t.Fatalf("other project's keys or the epoch were removed: %v", env.mr.Keys())
	}
	seenV2WantAll(t, "erased project", pod.Check(ctx, seenV2Items(seenV2Pid), seenV2TokB), SeenNew)
	seenV2WantAll(t, "other project", pod.Check(ctx, seenV2Items(seenV2Pid2), seenV2TokB), SeenReuseOther)

	for _, bad := range []string{"", "  ", "v2", "a:b"} {
		if _, err := SeenProjectKeyPatterns(bad); err == nil {
			t.Fatalf("SeenProjectKeyPatterns(%q) accepted", bad)
		}
	}
	pats, err := SeenProjectKeyPatterns("p*1")
	if err != nil || pats[0] != `traj:seen:v2:p\*1:*` || pats[1] != `traj:seen:p\*1:*` {
		t.Fatalf("patterns=%v err=%v", pats, err)
	}
}

// The contract key is a HASH with one field per collector: written by
// Run/WriteContract (key EX 1d) with the store's own largest reuse age and the
// write time, refreshed by the keeper, pruned of writers silent for over a
// day, removed by the writer on shutdown, and never written by a check or a
// commit.
func TestCollectorWritesContractKey(t *testing.T) {
	env := newSeenV2Env(t)
	ctx := context.Background()
	pod := env.store(SeenOptions{WriterID: "collector-a", Logger: slog.New(slog.DiscardHandler)})
	pod.Commit(ctx, seenV2Items(seenV2Pid), SeenStamp{Committer: seenV2TokA})
	_ = pod.Check(ctx, seenV2Items(seenV2Pid), seenV2TokB)
	if env.mr.Exists(SeenContractKey) {
		t.Fatalf("check/commit must not write %s", SeenContractKey)
	}

	readField := func(field string) (SeenContract, bool) {
		t.Helper()
		raw := env.mr.HGet(SeenContractKey, field)
		if raw == "" {
			return SeenContract{}, false
		}
		var c SeenContract
		if err := json.Unmarshal([]byte(raw), &c); err != nil {
			t.Fatalf("contract field %s = %q: %v", field, raw, err)
		}
		return c, true
	}

	if err := pod.WriteContract(ctx); err != nil {
		t.Fatalf("WriteContract: %v", err)
	}
	if ttl := env.mr.TTL(SeenContractKey); ttl != SeenContractTTL {
		t.Fatalf("contract TTL=%s, want %s", ttl, SeenContractTTL)
	}
	if typ := env.mr.Type(SeenContractKey); typ != "hash" {
		t.Fatalf("contract key type=%s, want hash", typ)
	}
	c, ok := readField("collector-a")
	if !ok || c.Version != 2 || c.Writer != "collector-a" || c.WrittenAt != seenV2T0.Unix() ||
		c.MaxReuseAgeSeconds != int64(seenV2Age/time.Second) ||
		c.KeyLifetimeSeconds != int64((2*seenV2Age+time.Hour)/time.Second) {
		t.Fatalf("contract=%+v ok=%v", c, ok)
	}

	// The published age is the store's own largest one: the legacy
	// constructor's 24h session / 7d project ages publish the 48h cap.
	legacy := NewSeenStore(env.rdb, 100, 24*time.Hour, 7*24*time.Hour)
	legacy.nowFn, legacy.writerID = env.clock.now, "collector-legacy"
	if err := legacy.WriteContract(ctx); err != nil {
		t.Fatalf("legacy WriteContract: %v", err)
	}
	if c, _ := readField("collector-legacy"); c.MaxReuseAgeSeconds != int64(SeenMaxReuseAge/time.Second) {
		t.Fatalf("legacy store published max_reuse_age_s=%d, want %d", c.MaxReuseAgeSeconds, int64(SeenMaxReuseAge/time.Second))
	}

	// A writer silent for more than a day is pruned by the next write.
	env.advance(SeenContractTTL - time.Hour)
	if err := legacy.WriteContract(ctx); err != nil {
		t.Fatal(err)
	}
	env.advance(2 * time.Hour)
	if err := legacy.WriteContract(ctx); err != nil {
		t.Fatal(err)
	}
	if _, ok := readField("collector-a"); ok {
		t.Fatalf("stale writer collector-a not pruned")
	}
	if c, ok := readField("collector-legacy"); !ok || c.WrittenAt != env.clock.now().Unix() {
		t.Fatalf("collector-legacy field=%+v ok=%v, want written now", c, ok)
	}

	// The keeper writes at once, rewrites a lost field, and removes its own
	// field on shutdown.
	env.mr.Del(SeenContractKey)
	kctx, cancel := context.WithCancel(ctx)
	done := make(chan struct{})
	go func() {
		pod.run(kctx, 20*time.Millisecond)
		close(done)
	}()
	waitField := func(label string) {
		t.Helper()
		deadline := time.Now().Add(3 * time.Second)
		for env.mr.HGet(SeenContractKey, "collector-a") == "" {
			if time.Now().After(deadline) {
				t.Fatalf("%s: keeper did not write %s", label, SeenContractKey)
			}
			time.Sleep(5 * time.Millisecond)
		}
	}
	waitField("initial write")
	env.mr.Del(SeenContractKey)
	waitField("refresh after loss")
	cancel()
	<-done
	if env.mr.HGet(SeenContractKey, "collector-a") != "" {
		t.Fatalf("keeper left its field after shutdown")
	}

	if err := NewSeenStoreWithOptions(nil, SeenOptions{}).WriteContract(ctx); err == nil {
		t.Fatalf("WriteContract without Redis must fail")
	}
	lruOnly := NewSeenStoreWithOptions(nil, SeenOptions{})
	finished := make(chan struct{})
	go func() { lruOnly.Run(ctx); close(finished) }()
	select {
	case <-finished:
	case <-time.After(3 * time.Second):
		t.Fatalf("Run on an LRU-only store must return at once")
	}
}

// An item committed by the same billing key is billed as new (not credited);
// another key or "*" is credited.
func TestSeenStoreSelfCommittedItemIsBillableNotCredited(t *testing.T) {
	env := newSeenV2Env(t)
	ctx := context.Background()
	items := seenV2Items(seenV2Pid)
	podA := env.pod()
	podA.Commit(ctx, items, SeenStamp{Committer: seenV2TokA})
	total := seenV2Bytes(items)

	// "redis" uses a cold pod for every check, so each one reads Redis.
	for name, pod := range map[string]func() *SeenStore{"local": func() *SeenStore { return podA }, "redis": env.pod} {
		self := pod().Check(ctx, items, seenV2TokA)
		seenV2WantAll(t, name+" self", self, SeenReuseSelf)
		if self.BilledItemBytes() != total || self.CreditedItemBytes() != 0 || len(self.Write()) != 0 {
			t.Fatalf("%s self: billed=%d credited=%d write=%d", name, self.BilledItemBytes(), self.CreditedItemBytes(), len(self.Write()))
		}
		other := pod().Check(ctx, items, seenV2TokB)
		seenV2WantAll(t, name+" other", other, SeenReuseOther)
		if other.CreditedItemBytes() != total || other.BilledItemBytes() != 0 {
			t.Fatalf("%s other: billed=%d credited=%d", name, other.BilledItemBytes(), other.CreditedItemBytes())
		}
		seenV2WantAll(t, name+" any", pod().Check(ctx, items, SeenAnyCommitter), SeenReuseOther)
	}
}

// A re-poll of a refresh by the same billing key is classed refresh again, so
// both polls bill the same amount whichever is accounted first.
func TestSeenStoreRepollOfRefreshIsRefreshAgain(t *testing.T) {
	env := newSeenV2Env(t)
	ctx := context.Background()
	items := seenV2Items(seenV2Pid)
	env.pod().Commit(ctx, items, SeenStamp{Committer: seenV2TokB})
	env.advance(2 * seenV2Rlen)

	podA := env.pod()
	poll1 := podA.Check(ctx, items, seenV2TokA)
	seenV2WantAll(t, "poll 1", poll1, SeenRefresh)
	if len(poll1.Write()) != 2 {
		t.Fatalf("poll 1 must write a fresh copy, write=%d", len(poll1.Write()))
	}
	podA.CommitChecked(ctx, poll1, SeenStamp{Committer: seenV2TokA, At: env.clock.now()})
	g := seenGen(env.clock.now(), seenV2Rlen)
	if v := env.mr.HGet(SeenKey(seenV2Pid, env.epoch(), seenV2Sess, g), seenV2HashA); v != seenV2TokA+"|r" {
		t.Fatalf("stored value=%q, want %q", v, seenV2TokA+"|r")
	}

	// "redis" uses a cold pod for every check, so each one reads Redis.
	for name, pod := range map[string]func() *SeenStore{"local": func() *SeenStore { return podA }, "redis": env.pod} {
		poll2 := pod().Check(ctx, items, seenV2TokA)
		seenV2WantAll(t, name+" poll 2", poll2, SeenRefresh)
		if len(poll2.Write()) != 0 {
			t.Fatalf("%s poll 2 rewrote %d items", name, len(poll2.Write()))
		}
		if poll2.BilledItemBytes() != poll1.BilledItemBytes() || poll2.CreditedItemBytes() != poll1.CreditedItemBytes() {
			t.Fatalf("%s amounts differ: poll1 billed=%d credited=%d, poll2 billed=%d credited=%d", name,
				poll1.BilledItemBytes(), poll1.CreditedItemBytes(), poll2.BilledItemBytes(), poll2.CreditedItemBytes())
		}
		seenV2WantAll(t, name+" other key", pod().Check(ctx, items, seenV2TokC), SeenReuseOther)
	}
}

// The first committer wins in Redis and in the second pod's cache.
func TestSeenStoreCommitterFirstWinsAcrossPods(t *testing.T) {
	env := newSeenV2Env(t)
	ctx := context.Background()
	items := seenV2Items(seenV2Pid)
	podA, podB := env.pod(), env.pod()
	podA.Commit(ctx, items, SeenStamp{Committer: seenV2TokA})
	podB.Commit(ctx, items, SeenStamp{Committer: seenV2TokB})

	g := seenGen(seenV2T0, seenV2Rlen)
	for _, it := range items {
		if v := env.mr.HGet(SeenKey(it.ProjectID, env.epoch(), it.ScopeID, g), it.ItemHash); v != seenV2TokA {
			t.Fatalf("Redis committer=%q, want %q", v, seenV2TokA)
		}
		if v := podB.entries[it.Key()].Value.(*lruEntry).value; v != seenV2TokA {
			t.Fatalf("podB LRU committer=%q, want %q", v, seenV2TokA)
		}
	}
	seenV2WantAll(t, "podB as A", podB.Check(ctx, items, seenV2TokA), SeenReuseSelf)
	seenV2WantAll(t, "podB as B", podB.Check(ctx, items, seenV2TokB), SeenReuseOther)

	local := NewSeenStoreWithOptions(nil, SeenOptions{MaxReuseAge: seenV2Age})
	local.Commit(ctx, items, SeenStamp{Committer: seenV2TokA})
	local.Commit(ctx, items, SeenStamp{Committer: seenV2TokB})
	seenV2WantAll(t, "LRU-only as A", local.Check(ctx, items, seenV2TokA), SeenReuseSelf)
}

// Legacy traj:seen:<project>:<scope> SETs neither error nor count as seen.
func TestSeenStoreV2IgnoresLegacySetKeys(t *testing.T) {
	env := newSeenV2Env(t)
	ctx := context.Background()
	items := seenV2Items(seenV2Pid)
	for _, it := range items {
		if _, err := env.mr.SAdd(redisLegacySeenKey(it.ProjectID, it.ScopeID), it.ItemHash); err != nil {
			t.Fatal(err)
		}
	}
	pod := env.pod()
	res := pod.Check(ctx, items, seenV2TokB)
	seenV2WantAll(t, "legacy members", res, SeenNew)
	if res.RedisFailed || pod.RedisErrors() != 0 {
		t.Fatalf("legacy keys caused Redis errors: failed=%v errors=%d", res.RedisFailed, pod.RedisErrors())
	}
	pod.CommitChecked(ctx, res, SeenStamp{Committer: seenV2TokA})
	seenV2WantAll(t, "after v2 commit", env.pod().Check(ctx, items, seenV2TokB), SeenReuseOther)
	for _, it := range items {
		members, err := env.mr.Members(redisLegacySeenKey(it.ProjectID, it.ScopeID))
		if err != nil || len(members) != 1 || members[0] != it.ItemHash {
			t.Fatalf("legacy SET changed: %v %v", members, err)
		}
	}
}

func redisLegacySeenKey(projectID, scopeID string) string {
	return "traj:seen:" + projectID + ":" + scopeID
}

// seenV2RejectHook fails every command in banned, as a Redis without those
// commands (or with scripting disabled) would.
type seenV2RejectHook struct {
	banned map[string]bool
	hits   atomic.Int64
}

func (h *seenV2RejectHook) reject(cmd redis.Cmder) error {
	if !h.banned[strings.ToLower(cmd.Name())] {
		return nil
	}
	h.hits.Add(1)
	err := fmt.Errorf("ERR unknown command '%s'", cmd.Name())
	cmd.SetErr(err)
	return err
}

func (h *seenV2RejectHook) DialHook(next redis.DialHook) redis.DialHook { return next }

func (h *seenV2RejectHook) ProcessHook(next redis.ProcessHook) redis.ProcessHook {
	return func(ctx context.Context, cmd redis.Cmder) error {
		if err := h.reject(cmd); err != nil {
			return err
		}
		return next(ctx, cmd)
	}
}

func (h *seenV2RejectHook) ProcessPipelineHook(next redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return func(ctx context.Context, cmds []redis.Cmder) error {
		for _, c := range cmds {
			if err := h.reject(c); err != nil {
				return err
			}
		}
		return next(ctx, cmds)
	}
}

// Cross-pod dedup works with SMISMEMBER, set commands and scripting all
// unavailable (Redis < 6.2, scripting disabled).
func TestSeenStoreCrossPodDedupWithoutSMISMEMBER(t *testing.T) {
	env := newSeenV2Env(t)
	hook := &seenV2RejectHook{banned: map[string]bool{
		"smismember": true, "sismember": true, "sadd": true, "srem": true, "smembers": true,
		"eval": true, "evalsha": true, "script": true, "expire": true,
	}}
	env.rdb.AddHook(hook)
	ctx := context.Background()
	items := seenV2Items(seenV2Pid)
	podA, podB := env.pod(), env.pod()
	resA := podA.Check(ctx, items, seenV2TokA)
	seenV2WantAll(t, "podA first", resA, SeenNew)
	podA.CommitChecked(ctx, resA, SeenStamp{Committer: seenV2TokA})
	seenV2WantAll(t, "podB cross-pod", podB.Check(ctx, items, seenV2TokB), SeenReuseOther)
	podA.Forget(ctx, items)
	seenV2WantAll(t, "after forget", env.pod().Check(ctx, items, seenV2TokB), SeenNew)
	if hook.hits.Load() != 0 || podA.RedisErrors() != 0 || podB.RedisErrors() != 0 {
		t.Fatalf("banned commands used: hits=%d errA=%d errB=%d", hook.hits.Load(), podA.RedisErrors(), podB.RedisErrors())
	}
}

// InvalidateProject is O(1): it touches no entry, yet the project's entries
// stop being reused (also when Redis no longer has them) and other projects'
// entries keep working.
func TestInvalidateProjectDoesNotScan(t *testing.T) {
	ctx := context.Background()
	s := NewSeenStoreWithOptions(nil, SeenOptions{LRUSize: 100_000, MaxReuseAge: seenV2Age})
	other := make([]Item, 50_000)
	for i := range other {
		other[i] = Item{ProjectID: seenV2Pid2, ScopeID: seenV2Sess, ItemHash: fmt.Sprintf("%032x", i), Content: "x"}
	}
	s.Commit(ctx, other, SeenStamp{Committer: seenV2TokA})
	mine := seenV2Items(seenV2Pid)
	s.Commit(ctx, mine, SeenStamp{Committer: seenV2TokA})
	before := len(s.entries)

	if n := s.InvalidateProject(seenV2Pid); n != len(mine) {
		t.Fatalf("InvalidateProject=%d, want %d", n, len(mine))
	}
	if len(s.entries) != before || s.ll.Len() != before {
		t.Fatalf("InvalidateProject touched entries: %d -> %d", before, len(s.entries))
	}
	seenV2WantAll(t, "invalidated project", s.Check(ctx, mine, seenV2TokB), SeenNew)
	seenV2WantAll(t, "other project", s.Check(ctx, other[:100], seenV2TokB), SeenReuseOther)
	if n := s.InvalidateProject(seenV2Pid); n != 0 {
		t.Fatalf("second InvalidateProject=%d, want 0", n)
	}
	s.Commit(ctx, mine, SeenStamp{Committer: seenV2TokA})
	seenV2WantAll(t, "recommitted", s.Check(ctx, mine, seenV2TokB), SeenReuseOther)
	if n := s.InvalidateProject(seenV2Pid); n != len(mine) {
		t.Fatalf("InvalidateProject after recommit=%d, want %d", n, len(mine))
	}

	// With Redis: after invalidation the pod-local entry is not trusted and
	// Redis is asked again. InvalidateProject alone does not stop reuse while
	// the project's Redis keys exist; erasure also removes them
	// (EraseProject), after which nothing is reused.
	env := newSeenV2Env(t)
	pod := env.pod()
	pod.Commit(ctx, mine, SeenStamp{Committer: seenV2TokA})
	pod.InvalidateProject(seenV2Pid)
	seenV2WantAll(t, "invalidated, Redis keys still present", pod.Check(ctx, mine, seenV2TokB), SeenReuseOther)
	pod.InvalidateProject(seenV2Pid)
	for _, k := range env.seenKeys() {
		env.mr.Del(k)
	}
	seenV2WantAll(t, "redis-backed invalidated", pod.Check(ctx, mine, seenV2TokB), SeenNew)
	pod.Commit(ctx, mine, SeenStamp{Committer: seenV2TokA})
	if _, err := pod.EraseProject(ctx, seenV2Pid); err != nil {
		t.Fatal(err)
	}
	seenV2WantAll(t, "erased", pod.Check(ctx, mine, seenV2TokB), SeenNew)
}

// Above the memory budget the store stops writing Redis (reads continue and
// the pod-local cache still works); below it writes resume.
func TestSeenStoreStopsWritingAboveBudget(t *testing.T) {
	env := newSeenV2Env(t)
	ctx := context.Background()
	var used, maxMem atomic.Int64
	var probeErr atomic.Bool
	probe := func(context.Context) (int64, int64, error) {
		if probeErr.Load() {
			return 0, 0, errors.New("INFO disabled")
		}
		return used.Load(), maxMem.Load(), nil
	}
	items := seenV2Items(seenV2Pid)

	// By default there is no absolute cap: used_memory counts the whole Redis
	// instance, so a Redis already holding other data must not stop the store.
	used.Store(4 << 30)
	dflt := env.store(SeenOptions{MemoryProbe: probe})
	dflt.Commit(ctx, seenV2Items(seenV2Pid2), SeenStamp{Committer: seenV2TokA})
	if keys := env.seenKeys(); len(keys) != 2 || dflt.Stats().BudgetSkips != 0 {
		t.Fatalf("default store skipped writes on a 4 GiB Redis without maxmemory: keys=%v stats=%+v", keys, dflt.Stats())
	}
	for _, k := range env.seenKeys() {
		env.mr.Del(k)
	}

	const capBytes = 256 << 20
	pod := env.store(SeenOptions{MemoryProbe: probe, RedisMaxBytes: capBytes})
	used.Store(capBytes + 1)
	pod.Commit(ctx, items, SeenStamp{Committer: seenV2TokA})
	if keys := env.seenKeys(); len(keys) != 0 {
		t.Fatalf("wrote %v above the absolute budget", keys)
	}
	if pod.Stats().BudgetSkips == 0 {
		t.Fatalf("budget skip not counted: %+v", pod.Stats())
	}
	seenV2WantAll(t, "same pod above budget", pod.Check(ctx, items, seenV2TokB), SeenReuseOther)
	seenV2WantAll(t, "other pod above budget", env.pod().Check(ctx, items, seenV2TokB), SeenNew)

	env.advance(11 * time.Second)
	used.Store(80 << 20)
	maxMem.Store(100 << 20) // 80% of maxmemory > 70%
	pod.Commit(ctx, items, SeenStamp{Committer: seenV2TokA})
	if keys := env.seenKeys(); len(keys) != 0 {
		t.Fatalf("wrote %v above the maxmemory fraction", keys)
	}

	env.advance(11 * time.Second)
	used.Store(10 << 20)
	pod.Commit(ctx, items, SeenStamp{Committer: seenV2TokA})
	if len(env.seenKeys()) != 2 {
		t.Fatalf("writes did not resume under budget: %v", env.seenKeys())
	}
	seenV2WantAll(t, "other pod under budget", env.pod().Check(ctx, items, seenV2TokB), SeenReuseOther)

	env.advance(11 * time.Second)
	probeErr.Store(true)
	pod2 := env.store(SeenOptions{MemoryProbe: pod.memProbe})
	pod2.Commit(ctx, seenV2Items(seenV2Pid2), SeenStamp{Committer: seenV2TokA})
	if len(env.seenKeys()) != 4 || pod2.Stats().MemProbeErrors == 0 {
		t.Fatalf("probe error must not stop writes: keys=%v stats=%+v", env.seenKeys(), pod2.Stats())
	}
}

// The default INFO memory probe parses real output, and its failure (INFO
// rejected, as on a Redis that disables it) does not stop writes.
func TestSeenDefaultMemoryProbe(t *testing.T) {
	used, maxMem, err := parseRedisInfoMemory("# Memory\r\nused_memory:1048576\r\nused_memory_human:1.00M\r\nmaxmemory:402653184\r\nmaxmemory_policy:volatile-lru\r\n")
	if err != nil || used != 1<<20 || maxMem != 384<<20 {
		t.Fatalf("parse: used=%d max=%d err=%v", used, maxMem, err)
	}
	if _, _, err := parseRedisInfoMemory("# Memory\r\nmaxmemory:0\r\n"); err == nil {
		t.Fatalf("missing used_memory must fail")
	}

	env := newSeenV2Env(t)
	hook := &seenV2RejectHook{banned: map[string]bool{"info": true}}
	env.rdb.AddHook(hook)
	s := NewSeenStoreWithOptions(env.rdb, SeenOptions{MaxReuseAge: seenV2Age, Now: env.clock.now})
	s.Commit(context.Background(), seenV2Items(seenV2Pid), SeenStamp{Committer: seenV2TokA})
	if len(env.seenKeys()) != 2 || s.Stats().MemProbeErrors == 0 || hook.hits.Load() == 0 {
		t.Fatalf("default probe on miniredis: keys=%v stats=%+v", env.seenKeys(), s.Stats())
	}
}

// A commit whose usage event is a known duplicate (unbilled) records
// nothing, so it is never credited to a later request; the next billed
// commit is recorded normally.
func TestSeenUnbilledCommitClassifiedNew(t *testing.T) {
	env := newSeenV2Env(t)
	ctx := context.Background()
	items := seenV2Items(seenV2Pid)
	env.pod().Commit(ctx, items, SeenStamp{Committer: seenV2TokA, Unbilled: true})
	if keys := env.seenKeys(); len(keys) != 0 {
		t.Fatalf("unbilled commit wrote %v", keys)
	}

	podB := env.pod()
	res := podB.Check(ctx, items, seenV2TokB)
	seenV2WantAll(t, "after unbilled commit", res, SeenNew)
	if res.BilledItemBytes() != seenV2Bytes(items) {
		t.Fatalf("unbilled hit billed=%d, want %d", res.BilledItemBytes(), seenV2Bytes(items))
	}
	seenV2WantAll(t, "same key after unbilled commit", env.pod().Check(ctx, items, seenV2TokA), SeenNew)

	podB.CommitChecked(ctx, res, SeenStamp{Committer: seenV2TokB})
	g := seenGen(seenV2T0, seenV2Rlen)
	if v := env.mr.HGet(SeenKey(seenV2Pid, env.epoch(), seenV2Sess, g), seenV2HashA); v != seenV2TokB {
		t.Fatalf("billed commit after unbilled commit stored %q, want %q", v, seenV2TokB)
	}
	seenV2WantAll(t, "third key", env.pod().Check(ctx, items, seenV2TokC), SeenReuseOther)
	seenV2WantAll(t, "billed committer", env.pod().Check(ctx, items, seenV2TokB), SeenReuseSelf)

	// Per-item: an Item whose Commit says Unbilled records nothing either.
	unbilled := seenV2Items(seenV2Pid2)
	for i := range unbilled {
		unbilled[i].Commit = ItemCommit{Committer: seenV2TokA, Unbilled: true}
	}
	env.pod().MarkCommitted(ctx, unbilled)
	seenV2WantAll(t, "per-item unbilled", env.pod().Check(ctx, seenV2Items(seenV2Pid2), seenV2TokB), SeenNew)
}

// Two pods that both class an item New after an unbilled commit both commit
// it; the first committer stays recorded, so its re-poll is ReuseSelf
// (billed) and the other's is ReuseOther, whichever pod answers.
func TestSeenStoreFirstBilledCommitterWinsAfterUnbilledCommit(t *testing.T) {
	env := newSeenV2Env(t)
	ctx := context.Background()
	items := seenV2Items(seenV2Pid)
	env.pod().Commit(ctx, items, SeenStamp{Committer: seenV2TokC, Unbilled: true})

	podA, podB := env.pod(), env.pod()
	resA := podA.Check(ctx, items, seenV2TokA)
	resB := podB.Check(ctx, items, seenV2TokB)
	seenV2WantAll(t, "podA", resA, SeenNew)
	seenV2WantAll(t, "podB", resB, SeenNew)
	podA.CommitChecked(ctx, resA, SeenStamp{Committer: seenV2TokA})
	podB.CommitChecked(ctx, resB, SeenStamp{Committer: seenV2TokB})

	g := seenGen(seenV2T0, seenV2Rlen)
	for _, it := range items {
		if v := env.mr.HGet(SeenKey(it.ProjectID, env.epoch(), it.ScopeID, g), it.ItemHash); v != seenV2TokA {
			t.Fatalf("Redis committer=%q, want first committer %q", v, seenV2TokA)
		}
	}
	for name, pod := range map[string]func() *SeenStore{"podA": func() *SeenStore { return podA }, "podB": func() *SeenStore { return podB }, "cold": env.pod} {
		seenV2WantAll(t, name+" re-poll by A", pod().Check(ctx, items, seenV2TokA), SeenReuseSelf)
		seenV2WantAll(t, name+" re-poll by B", pod().Check(ctx, items, seenV2TokB), SeenReuseOther)
	}
}

// Requests not billed on stored bytes (events mode, unknown mode, no emitter)
// and multi-trace requests commit as "*" right after the items insert; "*"
// hits are credited to any later request and a "*" caller is never "self".
func TestSeenUnmeteredCommitUsesAnyCommitter(t *testing.T) {
	if SeenCommitterToken("") != SeenAnyCommitter {
		t.Fatalf("empty billing key must map to %q", SeenAnyCommitter)
	}
	if tok := SeenCommitterToken("trace-A"); len(tok) != 16 || strings.Trim(tok, "0123456789abcdef") != "" || tok == seenV2TokB {
		t.Fatalf("token %q: want 16 lowercase hex, distinct per key", tok)
	}
	ns := NewSeenStoreWithOptions(nil, SeenOptions{})
	if ns.normalizeCommitter(" ") != SeenAnyCommitter || ns.Stats().InvalidCommitters != 0 {
		t.Fatalf("an empty committer is %q without a warning", SeenAnyCommitter)
	}
	if ns.normalizeCommitter("a|r") != SeenAnyCommitter || ns.Stats().InvalidCommitters != 1 {
		t.Fatalf("a committer carrying flags must become %q and be counted: %+v", SeenAnyCommitter, ns.Stats())
	}
	env := newSeenV2Env(t)
	ctx := context.Background()
	items := seenV2Items(seenV2Pid)
	env.pod().Commit(ctx, items, SeenStamp{Committer: SeenAnyCommitter})
	pod := env.pod()
	seenV2WantAll(t, "storage request after unmetered commit", pod.Check(ctx, items, seenV2TokA), SeenReuseOther)
	seenV2WantAll(t, "unmetered request after unmetered commit", pod.Check(ctx, items, SeenAnyCommitter), SeenReuseOther)
}

// Duplicates inside one check are credited once and written once.
func TestSeenCheckCollapsesInRequestDuplicates(t *testing.T) {
	env := newSeenV2Env(t)
	items := seenV2Items(seenV2Pid)
	res := env.pod().Check(context.Background(), append(append([]Item{}, items...), items[0]), seenV2TokA)
	if got := seenV2Classes(res); got[0] != SeenNew || got[1] != SeenNew || got[2] != SeenDuplicate {
		t.Fatalf("classes=%v", got)
	}
	if len(res.Write()) != 2 || res.CreditedItemBytes() != int64(len(items[0].Content)) {
		t.Fatalf("write=%d credited=%d", len(res.Write()), res.CreditedItemBytes())
	}
}

// The startup check passes on a healthy Redis and reports a dead one.
func TestSeenStoreStartupCheck(t *testing.T) {
	env := newSeenV2Env(t)
	pod := env.pod()
	if err := pod.StartupCheck(context.Background()); err != nil {
		t.Fatalf("healthy Redis: %v", err)
	}
	env.mr.Close()
	if err := pod.StartupCheck(context.Background()); err == nil {
		t.Fatalf("closed Redis must fail the startup check")
	}
	if err := NewSeenStoreWithOptions(nil, SeenOptions{}).StartupCheck(context.Background()); err != nil {
		t.Fatalf("LRU-only store: %v", err)
	}
}

// Concurrent checks, commits and invalidations are race-free.
func TestSeenStoreConcurrentUse(t *testing.T) {
	env := newSeenV2Env(t)
	ctx := context.Background()
	pods := []*SeenStore{env.pod(), env.pod()}
	var wg sync.WaitGroup
	for w := 0; w < 8; w++ {
		wg.Add(1)
		go func(w int) {
			defer wg.Done()
			pod := pods[w%2]
			tok := SeenCommitterToken(fmt.Sprintf("trace-%d", w))
			for i := 0; i < 50; i++ {
				items := []Item{
					{ProjectID: seenV2Pid, ScopeID: seenV2Sess, ItemHash: fmt.Sprintf("%032x", i%7), Content: "c"},
					{ProjectID: seenV2Pid, ScopeID: ProjectScopeID, ItemHash: fmt.Sprintf("%032x", i%3), Content: "p"},
				}
				res := pod.Check(ctx, items, tok)
				pod.CommitChecked(ctx, res, SeenStamp{Committer: tok})
				if i%10 == 0 {
					pod.InvalidateProject(seenV2Pid)
				}
				_ = pod.Stats()
			}
		}(w)
	}
	wg.Wait()
}

// A store that has never read the epoch (Redis down since start) reuses
// nothing, not even what it committed itself: it cannot know whether the
// epoch changed. Once Redis answers it reuses again.
func TestSeenRedisDownFromStartReusesNothing(t *testing.T) {
	env := newSeenV2Env(t)
	ctx := context.Background()
	items := seenV2Items(seenV2Pid)
	env.mr.Close()
	pod := env.pod()
	res := pod.Check(ctx, items, seenV2TokB)
	seenV2WantAll(t, "redis down, first check", res, SeenNew)
	if !res.RedisFailed || !res.EpochUnknown {
		t.Fatalf("RedisFailed=%v EpochUnknown=%v, want true/true", res.RedisFailed, res.EpochUnknown)
	}
	pod.CommitChecked(ctx, res, SeenStamp{Committer: seenV2TokA})
	env.advanceClock(2 * time.Second)
	again := pod.Check(ctx, items, seenV2TokB)
	seenV2WantAll(t, "redis down, own commit", again, SeenNew)
	if len(again.Write()) != len(items) || !again.EpochUnknown {
		t.Fatalf("write=%d EpochUnknown=%v, want every item written again", len(again.Write()), again.EpochUnknown)
	}

	if err := env.mr.Restart(); err != nil {
		t.Fatalf("restart miniredis: %v", err)
	}
	env.advance(2 * time.Second)
	res = pod.Check(ctx, items, seenV2TokB)
	seenV2WantAll(t, "after epoch read", res, SeenNew)
	pod.CommitChecked(ctx, res, SeenStamp{Committer: seenV2TokA})
	seenV2WantAll(t, "after recommit", pod.Check(ctx, items, seenV2TokB), SeenReuseOther)
	seenV2WantAll(t, "cold pod after recommit", env.pod().Check(ctx, items, seenV2TokB), SeenReuseOther)
}

// Per-item commit records survive Batch.Merge: the drain commits the merged
// batch in one MarkCommittedAt call, and every item is recorded with the
// committer and refresh mark of its first occurrence. CheckNew results stay
// as passed in, and the record never reaches the ClickHouse row.
func TestSeenCommitItemsCarryCommitThroughBatchMerge(t *testing.T) {
	env := newSeenV2Env(t)
	ctx := context.Background()
	refreshItem := Item{ProjectID: seenV2Pid, ScopeID: ProjectScopeID, ItemHash: seenV2HashB, ItemType: "message", Content: `{"role":"system","content":"rules"}`}
	newItem := Item{ProjectID: seenV2Pid, ScopeID: seenV2Sess, ItemHash: seenV2HashA, ItemType: "message", Content: `{"role":"user","content":"hello"}`}
	env.pod().Commit(ctx, []Item{refreshItem}, SeenStamp{Committer: seenV2TokC})
	env.advance(2 * seenV2Rlen)

	podA, podB := env.pod(), env.pod()
	resA := podA.Check(ctx, []Item{refreshItem, newItem}, seenV2TokA)
	if got := seenV2Classes(resA); got[0] != SeenRefresh || got[1] != SeenNew {
		t.Fatalf("request A classes=%v, want [refresh new]", got)
	}
	resB := podB.Check(ctx, []Item{newItem}, seenV2TokB)
	seenV2WantAll(t, "request B", resB, SeenNew)

	batchA, batchB := NewBatch(), NewBatch()
	for _, it := range resA.CommitItems() {
		batchA.AddItem(it)
	}
	for _, it := range resB.CommitItems() {
		batchB.AddItem(it)
	}
	drained := NewBatch()
	drained.Merge(batchA)
	drained.Merge(batchB)
	if len(drained.Items()) != 2 {
		t.Fatalf("merged items=%d, want 2", len(drained.Items()))
	}
	for _, row := range drained.ToRows(env.clock.now()) {
		if len(row) != 7 {
			t.Fatalf("row carries extra fields: %v", row)
		}
	}
	stamp := env.clock.now()
	podA.MarkCommittedAt(ctx, drained.Items(), stamp)

	g := seenGen(stamp, seenV2Rlen)
	if v := env.mr.HGet(SeenKey(seenV2Pid, env.epoch(), ProjectScopeID, g), seenV2HashB); v != seenV2TokA+"|r" {
		t.Fatalf("refresh item stored %q, want %q", v, seenV2TokA+"|r")
	}
	if v := env.mr.HGet(SeenKey(seenV2Pid, env.epoch(), seenV2Sess, g), seenV2HashA); v != seenV2TokA {
		t.Fatalf("new item stored %q, want first occurrence's committer %q", v, seenV2TokA)
	}

	repollA := env.pod().Check(ctx, []Item{refreshItem, newItem}, seenV2TokA)
	if got := seenV2Classes(repollA); got[0] != SeenRefresh || got[1] != SeenReuseSelf || len(repollA.Write()) != 0 {
		t.Fatalf("re-poll by A classes=%v write=%d, want [refresh reuse_self] and nothing written", got, len(repollA.Write()))
	}
	if repollA.BilledItemBytes() != resA.BilledItemBytes() || repollA.CreditedItemBytes() != resA.CreditedItemBytes() {
		t.Fatalf("re-poll by A bills differently: first billed=%d credited=%d, re-poll billed=%d credited=%d",
			resA.BilledItemBytes(), resA.CreditedItemBytes(), repollA.BilledItemBytes(), repollA.CreditedItemBytes())
	}
	seenV2WantAll(t, "re-poll by B", env.pod().Check(ctx, []Item{newItem}, seenV2TokB), SeenReuseOther)

	legacyNew, _, _ := env.pod().CheckNew(ctx, seenV2Items(seenV2Pid2))
	for _, it := range legacyNew {
		if it.Commit != (ItemCommit{}) {
			t.Fatalf("CheckNew returned a stamped item: %+v", it.Commit)
		}
	}
}

// seenV2RecordHook records the command names of every pipeline.
type seenV2RecordHook struct {
	mu        sync.Mutex
	pipelines [][]string
}

func (h *seenV2RecordHook) DialHook(next redis.DialHook) redis.DialHook { return next }

func (h *seenV2RecordHook) ProcessHook(next redis.ProcessHook) redis.ProcessHook { return next }

func (h *seenV2RecordHook) ProcessPipelineHook(next redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return func(ctx context.Context, cmds []redis.Cmder) error {
		names := make([]string, len(cmds))
		for i, c := range cmds {
			names[i] = strings.ToLower(c.Name())
		}
		h.mu.Lock()
		h.pipelines = append(h.pipelines, names)
		h.mu.Unlock()
		return next(ctx, cmds)
	}
}

// A commit's HSETNX and EXPIREAT run in one MULTI/EXEC, so a dropped
// connection can never leave a generation key without its expiry.
func TestSeenCommitIsOneTransactionWithExpireAt(t *testing.T) {
	env := newSeenV2Env(t)
	hook := &seenV2RecordHook{}
	env.rdb.AddHook(hook)
	env.pod().Commit(context.Background(), seenV2Items(seenV2Pid), SeenStamp{Committer: seenV2TokA})

	hook.mu.Lock()
	defer hook.mu.Unlock()
	found := false
	for _, names := range hook.pipelines {
		hsetnx, expireat := 0, 0
		for _, n := range names {
			switch n {
			case "hsetnx":
				hsetnx++
			case "expireat":
				expireat++
			}
		}
		if hsetnx == 0 {
			continue
		}
		found = true
		if names[0] != "multi" || names[len(names)-1] != "exec" || hsetnx != 2 || expireat != 2 {
			t.Fatalf("commit pipeline %v: want MULTI, 2 HSETNX, 2 EXPIREAT, EXEC", names)
		}
	}
	if !found {
		t.Fatalf("no commit pipeline recorded: %v", hook.pipelines)
	}
	for _, k := range env.seenKeys() {
		if env.mr.TTL(k) <= 0 {
			t.Fatalf("key %s has no TTL", k)
		}
	}
}

// MarkCommittedAt records the generation of the persisted_at stamp, not of
// the time the commit runs, so a slow insert cannot make a copy look newer.
func TestSeenMarkCommittedAtUsesPersistedAtGeneration(t *testing.T) {
	env := newSeenV2Env(t)
	ctx := context.Background()
	items := seenV2Items(seenV2Pid)
	persistedAt := env.clock.now().Add(time.Hour)
	env.advance(seenV2Rlen + time.Minute) // the commit runs one generation later
	env.pod().MarkCommittedAt(ctx, items, persistedAt)

	g0 := seenGen(persistedAt, seenV2Rlen)
	for _, it := range items {
		if v := env.mr.HGet(SeenKey(it.ProjectID, env.epoch(), it.ScopeID, g0), it.ItemHash); v != SeenAnyCommitter {
			t.Fatalf("%s: generation %d value=%q, want %q", it.ScopeID, g0, v, SeenAnyCommitter)
		}
	}
	env.advance(seenV2Rlen) // now g0+2: the copy is past the reuse window
	seenV2WantAll(t, "two generations after persisted_at", env.pod().Check(ctx, items, seenV2TokB), SeenRefresh)
}

type seenV2LogBuf struct {
	mu sync.Mutex
	b  strings.Builder
}

func (w *seenV2LogBuf) Write(p []byte) (int, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	return w.b.Write(p)
}

func (w *seenV2LogBuf) String() string {
	w.mu.Lock()
	defer w.mu.Unlock()
	return w.b.String()
}

// Run starts with the Redis startup check: with Redis down it logs the
// startup WARN and the failed contract write, and still stops on shutdown.
func TestSeenRunWarnsWhenRedisIsDown(t *testing.T) {
	env := newSeenV2Env(t)
	buf := &seenV2LogBuf{}
	pod := env.store(SeenOptions{Logger: slog.New(slog.NewTextHandler(buf, nil))})
	env.mr.Close()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { pod.run(ctx, time.Hour); close(done) }()
	deadline := time.Now().Add(5 * time.Second)
	for !strings.Contains(buf.String(), "trajectory_seen_redis_unavailable") || !strings.Contains(buf.String(), "op=contract") {
		if time.Now().After(deadline) {
			cancel()
			<-done
			t.Fatalf("Run did not log the startup check and contract failures: %s", buf.String())
		}
		time.Sleep(10 * time.Millisecond)
	}
	cancel()
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatalf("Run did not stop on shutdown")
	}
}
