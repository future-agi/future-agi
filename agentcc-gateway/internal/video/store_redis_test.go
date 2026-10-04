package video_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	video "github.com/futureagi/agentcc-gateway/internal/video"
	"github.com/futureagi/agentcc-gateway/internal/video/storetest"
	"github.com/redis/go-redis/v9"
	"os"
	"sync"
	"testing"
	"time"
)

func redisFixture(t *testing.T) (*redisstate.Client, *redisstate.BudgetStore, string) {
	t.Helper()
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Skip("TEST_REDIS_ADDR unset")
	}
	client, err := redisstate.NewClient(redisstate.Config{Address: addr, PoolSize: 120, Timeout: time.Second})
	if err != nil {
		t.Fatal(err)
	}
	prefix := fmt.Sprintf("test:video:%d:%s:", time.Now().UnixNano(), t.Name())
	t.Cleanup(func() {
		ctx := context.Background()
		var cursor uint64
		for {
			keys, next, err := client.Redis().Scan(ctx, cursor, prefix+"*", 1000).Result()
			if err != nil {
				t.Error(err)
				break
			}
			if len(keys) > 0 {
				if err := client.Redis().Del(ctx, keys...).Err(); err != nil {
					t.Error(err)
				}
			}
			cursor = next
			if cursor == 0 {
				break
			}
		}
		_ = client.Close()
	})
	return client, redisstate.NewBudgetStore(client, prefix+"budget:"), prefix
}
func TestRedisStore(t *testing.T) {
	storetest.Run(t, func(t *testing.T) video.Store {
		c, b, p := redisFixture(t)
		return video.NewRedisStore(c, video.StoreOptions{Prefix: p, Budget: b})
	})
}
func TestReplay_Concurrent100Replicas(t *testing.T) {
	c, b, p := redisFixture(t)
	stores := []video.Store{video.NewRedisStore(c, video.StoreOptions{Prefix: p, Budget: b}), video.NewRedisStore(c, video.StoreOptions{Prefix: p, Budget: b})}
	var wg sync.WaitGroup
	ids := make(chan string, 100)
	errs := make(chan error, 100)
	start := make(chan struct{})
	for i := 0; i < 100; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			<-start
			j := storetest.Job(fmt.Sprintf("video_%d", i), "org")
			j.ReservedMicros = 1_000_000
			result, err := stores[i%2].Accept(context.Background(), video.AcceptRequest{Job: j, Operation: "submit", IdempotencyKey: "same", Reservations: []video.BudgetReservation{{Level: "org", Period: "total", Limit: 1000}}})
			if err != nil {
				errs <- err
				return
			}
			ids <- result.Job.ID
		}(i)
	}
	close(start)
	wg.Wait()
	close(ids)
	close(errs)
	for err := range errs {
		t.Error(err)
	}
	set := map[string]bool{}
	for id := range ids {
		set[id] = true
	}
	if len(set) != 1 {
		t.Fatal(set)
	}
	spend, _, ok := b.GetSpend("org", "org", "", "total")
	if !ok || spend != 1 {
		t.Fatal(spend, ok)
	}
	_, n, err := stores[0].ListByOrg("org", video.VideoListFilters{Limit: 100})
	if err != nil || n != 1 {
		t.Fatal(n, err)
	}
}

type barrierBudget struct {
	*redisstate.BudgetStore
	ready   *sync.WaitGroup
	release <-chan struct{}
}

func (b *barrierBudget) CheckAndRecordSpend(org, level, key, period, model string, cost, limit, modelLimit float64) (float64, bool, bool) {
	n, allowed, ok := b.BudgetStore.CheckAndRecordSpend(org, level, key, period, model, cost, limit, modelLimit)
	b.ready.Done()
	<-b.release
	return n, allowed, ok
}
func TestAccept_CompensatesOnIdemCollision(t *testing.T) {
	c, b, p := redisFixture(t)
	ready := new(sync.WaitGroup)
	ready.Add(2)
	release := make(chan struct{})
	wrapped := &barrierBudget{b, ready, release}
	s := video.NewRedisStore(c, video.StoreOptions{Prefix: p, Budget: wrapped})
	var wg sync.WaitGroup
	for i := 0; i < 2; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			j := storetest.Job(fmt.Sprintf("video_collision%d", i), "org")
			j.ReservedMicros = 2_000_000
			if _, err := s.Accept(context.Background(), video.AcceptRequest{Job: j, Operation: "submit", IdempotencyKey: "collision", Reservations: []video.BudgetReservation{{Level: "org", Period: "total", Limit: 10}}}); err != nil {
				t.Error(err)
			}
		}(i)
	}
	ready.Wait()
	spend, _, ok := b.GetSpend("org", "org", "", "total")
	if !ok || spend != 4 {
		t.Fatal("both reservations must precede Lua", spend, ok)
	}
	close(release)
	wg.Wait()
	spend, _, ok = b.GetSpend("org", "org", "", "total")
	if !ok || spend != 2 {
		t.Fatal("missing compensation", spend, ok)
	}
}
func TestBudget_ConcurrentReservationsCannotOverspend(t *testing.T) {
	c, b, p := redisFixture(t)
	s := video.NewRedisStore(c, video.StoreOptions{Prefix: p, Budget: b})
	var wg sync.WaitGroup
	accepted := make(chan struct{}, 50)
	for i := 0; i < 50; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			j := storetest.Job(fmt.Sprintf("video_budget%d", i), "org")
			j.ReservedMicros = 1_000_000
			_, err := s.Accept(context.Background(), video.AcceptRequest{Job: j, Operation: "submit", IdempotencyKey: fmt.Sprint(i), Reservations: []video.BudgetReservation{{Level: "org", Period: "total", Limit: 10}}})
			if err == nil {
				accepted <- struct{}{}
			} else if !errors.Is(err, video.ErrBudgetExceeded) {
				t.Error(err)
			}
		}(i)
	}
	wg.Wait()
	close(accepted)
	spend, _, ok := b.GetSpend("org", "org", "", "total")
	if !ok || spend != float64(len(accepted)) || len(accepted) != 10 {
		t.Fatal(spend, len(accepted), ok)
	}
}
func TestAccept_CompensatesWhenLuaFails(t *testing.T) {
	c, b, p := redisFixture(t)
	s := video.NewRedisStore(c, video.StoreOptions{Prefix: p, Budget: b})
	ctx := context.Background()
	if err := c.Redis().Set(ctx, p+"submit", "wrong type", 0).Err(); err != nil {
		t.Fatal(err)
	}
	j := storetest.Job("video_failure", "org")
	j.ReservedMicros = 1_000_000
	_, err := s.Accept(ctx, video.AcceptRequest{Job: j, Operation: "submit", IdempotencyKey: "failed", Reservations: []video.BudgetReservation{{Level: "org", Period: "total", Limit: 10}}})
	if !errors.Is(err, video.ErrStoreUnavailable) {
		t.Fatal(err)
	}
	spend, _, ok := b.GetSpend("org", "org", "", "total")
	if !ok || spend != 0 {
		t.Fatal(spend, ok)
	}
	if n := c.Redis().Exists(ctx, p+"job:"+j.ID).Val(); n != 0 {
		t.Fatal("partial Lua write")
	}
}
func TestAccept_CompensatesEarlierHierarchy(t *testing.T) {
	c, b, p := redisFixture(t)
	s := video.NewRedisStore(c, video.StoreOptions{Prefix: p, Budget: b})
	j := storetest.Job("video_hierarchy", "org")
	j.ReservedMicros = 1_000_000
	_, err := s.Accept(context.Background(), video.AcceptRequest{Job: j, Operation: "submit", IdempotencyKey: "hierarchy", Reservations: []video.BudgetReservation{{Level: "org", Period: "total", Limit: 10}, {Level: "key", Key: "key", Period: "total", Limit: 0.5}}})
	if !errors.Is(err, video.ErrBudgetExceeded) {
		t.Fatal(err)
	}
	spend, _, ok := b.GetSpend("org", "org", "", "total")
	if !ok || spend != 0 {
		t.Fatal(spend, ok)
	}
}
func TestRedisOwnerlessRawHash(t *testing.T) {
	c, b, p := redisFixture(t)
	s := video.NewRedisStore(c, video.StoreOptions{Prefix: p, Budget: b})
	ctx := context.Background()
	j := storetest.Job("video_raw", "org")
	if err := s.Create(j); err != nil {
		t.Fatal(err)
	}
	if err := c.Redis().HSet(ctx, p+"job:"+j.ID, "org_id", "").Err(); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Get(j.ID); !errors.Is(err, video.ErrVideoJobNotFound) {
		t.Fatal(err)
	}
	rows, n, err := s.ListByOrg("org", video.VideoListFilters{Limit: 100})
	if err != nil || n != 0 || len(rows) != 0 {
		t.Fatal(rows, n, err)
	}
	claims, err := s.ClaimSubmit(ctx, "worker", time.Second, 10)
	if err != nil || len(claims) != 0 {
		t.Fatal(claims, err)
	}
}

// A Redis command can succeed while its response is lost. Refunding that
// reservation would make an accepted, billable job appear free.
func TestAccept_LostRedisReplyKeepsReservation(t *testing.T) {
	c, b, p := redisFixture(t)
	hook := &lostAcceptReply{}
	c.Redis().AddHook(hook)
	s := video.NewRedisStore(c, video.StoreOptions{Prefix: p, Budget: b})
	j := storetest.Job("video_lost_reply", "org")
	j.ReservedMicros = 1_000_000
	result, err := s.Accept(context.Background(), video.AcceptRequest{Job: j, Operation: "submit", IdempotencyKey: "lost", Reservations: []video.BudgetReservation{{Level: "org", Period: "total", Limit: 10}}})
	if !hook.fired {
		t.Fatal("fault was not injected")
	}
	if err != nil || result.Job.ID != j.ID {
		t.Fatal(result, err)
	}
	spend, _, ok := b.GetSpend("org", "org", "", "total")
	if !ok || spend != 1 {
		t.Fatal("accepted job lost reservation", spend, ok)
	}
}

type lostAcceptReply struct {
	once  sync.Once
	fired bool
}

func (h *lostAcceptReply) DialHook(next redis.DialHook) redis.DialHook { return next }
func (h *lostAcceptReply) ProcessPipelineHook(next redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return next
}
func (h *lostAcceptReply) ProcessHook(next redis.ProcessHook) redis.ProcessHook {
	return func(ctx context.Context, cmd redis.Cmder) error {
		err := next(ctx, cmd)
		if err == nil && (cmd.Name() == "eval" || cmd.Name() == "evalsha") && len(cmd.Args()) == 15 && fmt.Sprint(cmd.Args()[12]) == "1" {
			h.once.Do(func() { h.fired = true; err = errors.New("test: Redis reply lost after execution") })
		}
		return err
	}
}
func TestRedisRestartPreservesPrivateFields(t *testing.T) {
	c, b, p := redisFixture(t)
	s := video.NewRedisStore(c, video.StoreOptions{Prefix: p, Budget: b})
	j := storetest.Job("video_restart", "org")
	j.KeyID = "key"
	j.RequestCanonical = json.RawMessage(`{"prompt":"private"}`)
	j.CorrelationToken = "correlation"
	j.TraceID = "trace"
	j.TariffRevision = "revision"
	j.ClientMetadata = map[string]string{"m": "v"}
	if err := s.Create(j); err != nil {
		t.Fatal(err)
	}
	fresh := video.NewRedisStore(c, video.StoreOptions{Prefix: p, Budget: b})
	got, err := fresh.Get(j.ID)
	if err != nil || got.KeyID != j.KeyID || string(got.RequestCanonical) != string(j.RequestCanonical) || got.CorrelationToken != j.CorrelationToken || got.TraceID != j.TraceID || got.TariffRevision != j.TariffRevision || got.ClientMetadata["m"] != "v" {
		t.Fatal(got, err)
	}
}

func TestRetention_UnsettledIdempotencyAndGC(t *testing.T) {
	for _, backend := range []string{"memory", "redis"} {
		t.Run(backend, func(t *testing.T) {
			now := time.Now().UTC().Truncate(time.Second)
			opts := video.StoreOptions{IdempotencyTTL: time.Minute, Now: func() time.Time { return now }}
			var s video.Store
			if backend == "redis" {
				c, b, p := redisFixture(t)
				opts.Prefix = p
				opts.Budget = b
				s = video.NewRedisStore(c, opts)
			} else {
				s = video.NewMemoryStoreWithOptions(opts)
			}
			j := storetest.Job("video_retention", "org")
			j.ExpiresAt = now.Add(time.Minute)
			j.ReservedMicros = 1
			req := video.AcceptRequest{Job: j, Operation: "submit", IdempotencyKey: "retained"}
			accepted, err := s.Accept(context.Background(), req)
			if err != nil {
				t.Fatal(err)
			}
			j = accepted.Job
			l, err := s.Lease(context.Background(), j.ID, "worker", time.Minute)
			if err != nil {
				t.Fatal(err)
			}
			j.Status = video.StatusFailed
			j.SettlementState = video.SettlementUnsettled
			j.ReconcileState = video.ReconcilePending
			if err := s.Save(context.Background(), j, l); err != nil {
				t.Fatal(err)
			}
			if err := s.Release(context.Background(), l); err != nil {
				t.Fatal(err)
			}
			now = now.Add(time.Hour)
			if n, err := s.GarbageCollect(); err != nil || n != 0 {
				t.Fatal(n, err)
			}
			req.Job = storetest.Job("video_replay_retention", "org")
			again, err := s.Accept(context.Background(), req)
			if err != nil || again.Job.ID != j.ID || !again.Replay {
				t.Fatal(again, err)
			}
			l, err = s.Lease(context.Background(), j.ID, "settler", time.Minute)
			if err != nil {
				t.Fatal(err)
			}
			j.SettlementState = video.SettlementSettled
			j.ReconcileState = video.ReconcileResolved
			if err := s.Save(context.Background(), j, l); err != nil {
				t.Fatal(err)
			}
			if err := s.Release(context.Background(), l); err != nil {
				t.Fatal(err)
			}
			if n, err := s.GarbageCollect(); err != nil || n != 1 {
				t.Fatal(n, err)
			}
			after, err := s.Accept(context.Background(), req)
			if err != nil || after.Replay || after.Job.ID != req.Job.ID {
				t.Fatal(after, err)
			}
		})
	}
}
func TestRedisUnavailableFailsClosed(t *testing.T) {
	c, b, p := redisFixture(t)
	s := video.NewRedisStore(c, video.StoreOptions{Prefix: p, Budget: b})
	j := storetest.Job("video_unavailable", "org")
	if err := s.Create(j); err != nil {
		t.Fatal(err)
	}
	down, err := redisstate.NewClient(redisstate.Config{Address: os.Getenv("TEST_REDIS_ADDR")})
	if err != nil {
		t.Fatal(err)
	}
	_ = down.Close()
	unavailable := video.NewRedisStore(down, video.StoreOptions{Prefix: p, Budget: b})
	if _, err := unavailable.Get(j.ID); !errors.Is(err, video.ErrStoreUnavailable) {
		t.Fatal(err)
	}
	if _, err := unavailable.Accept(context.Background(), video.AcceptRequest{Job: storetest.Job("video_not_accepted", "org"), Operation: "submit", IdempotencyKey: "outage"}); !errors.Is(err, video.ErrStoreUnavailable) {
		t.Fatal(err)
	}
}

func TestIdempotencyExpiresWithoutGC(t *testing.T) {
	for _, backend := range []string{"memory", "redis"} {
		t.Run(backend, func(t *testing.T) {
			opts := video.StoreOptions{IdempotencyTTL: time.Second}
			var s video.Store
			if backend == "redis" {
				c, b, p := redisFixture(t)
				opts.Prefix = p
				opts.Budget = b
				s = video.NewRedisStore(c, opts)
			} else {
				s = video.NewMemoryStoreWithOptions(opts)
			}
			ctx := context.Background()
			req := video.AcceptRequest{Job: storetest.Job("video_old_binding", "org"), Operation: "submit", IdempotencyKey: "expiring"}
			result, err := s.Accept(ctx, req)
			if err != nil {
				t.Fatal(err)
			}
			old := result.Job
			l, err := s.Lease(ctx, old.ID, "worker", time.Minute)
			if err != nil {
				t.Fatal(err)
			}
			old.Status = video.StatusFailed
			old.SettlementState = video.SettlementReleased
			if err := s.Save(ctx, old, l); err != nil {
				t.Fatal(err)
			}
			if err := s.Release(ctx, l); err != nil {
				t.Fatal(err)
			}
			time.Sleep(1100 * time.Millisecond)
			req.Job = storetest.Job("video_new_binding", "org")
			fresh, err := s.Accept(ctx, req)
			if err != nil || fresh.Replay || fresh.Job.ID != req.Job.ID {
				t.Fatal("settled binding did not expire", fresh, err)
			}
			l, err = s.Lease(ctx, old.ID, "gc", time.Minute)
			if err != nil {
				t.Fatal(err)
			}
			old.ExpiresAt = time.Now().Add(-time.Hour)
			if err := s.Save(ctx, old, l); err != nil {
				t.Fatal(err)
			}
			if err := s.Release(ctx, l); err != nil {
				t.Fatal(err)
			}
			if n, err := s.GarbageCollect(); err != nil || n != 1 {
				t.Fatal(n, err)
			}
			again, err := s.Accept(ctx, req)
			if err != nil || !again.Replay || again.Job.ID != fresh.Job.ID {
				t.Fatal("GC deleted new binding", again, err)
			}
		})
	}
}

func TestRedisHashFieldContract(t *testing.T) {
	c, b, p := redisFixture(t)
	s := video.NewRedisStore(c, video.StoreOptions{Prefix: p, Budget: b})
	j := storetest.Job("video_hash_fields", "org")
	j.Status = video.StatusFailed
	j.Error = &video.VideoError{Code: "provider_timeout", Message: "timed out", Retryable: true}
	j.Usage = &video.VideoUsage{GenerationSeconds: 8}
	if err := s.Create(j); err != nil {
		t.Fatal(err)
	}
	fields, err := c.Redis().HGetAll(context.Background(), p+"job:"+j.ID).Result()
	if err != nil {
		t.Fatal(err)
	}
	for field, want := range map[string]string{"error_code": "provider_timeout", "error_message": "timed out", "error_retryable": "true"} {
		if fields[field] != want {
			t.Fatalf("%s=%q", field, fields[field])
		}
	}
	if fields["usage_json"] == "" {
		t.Fatal("missing usage_json")
	}
}
