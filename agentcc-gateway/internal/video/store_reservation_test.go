package video_test

import (
	"context"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"testing"
	"time"

	video "github.com/futureagi/agentcc-gateway/internal/video"
	"github.com/futureagi/agentcc-gateway/internal/video/storetest"
	"github.com/redis/go-redis/v9"
)

// Crash after Redis commits a reservation, before the acceptor can save any
// in-process evidence. Recovery must refund only the scopes actually charged.
func TestAccept_RecoversOrphanReservations(t *testing.T) {
	for _, charges := range []int{1, 2} {
		t.Run(fmt.Sprint(charges), func(t *testing.T) {
			c, budget, prefix := redisFixture(t)
			now := time.Now().UTC()
			s := video.NewRedisStore(c, video.StoreOptions{Prefix: prefix, Budget: budget, Now: func() time.Time { return now }})
			hook := &reservationCrashHook{prefix: prefix + "budget:", remaining: charges}
			c.Redis().AddHook(hook)
			j := storetest.Job(video.NewID(), "org")
			j.ReservedMicros = 2_000_000
			func() {
				defer func() {
					if recover() != "reservation-crash" {
						t.Fatal("failpoint not reached")
					}
				}()
				_, _ = s.Accept(context.Background(), video.AcceptRequest{Job: j, Operation: "submit", IdempotencyKey: "orphan", Reservations: []video.BudgetReservation{{Level: "org", Period: "total", Limit: 10}, {Level: "key", Key: "key1", Period: "total", Limit: 10}}})
			}()
			now = now.Add(3 * time.Minute)
			for i := 0; i < 2; i++ {
				if _, err := s.GarbageCollect(); err != nil {
					t.Fatal(err)
				}
			}
			for _, scope := range []struct{ level, key string }{{"org", ""}, {"key", "key1"}} {
				spent, _, ok := budget.GetSpend("org", scope.level, scope.key, "total")
				if !ok || spent != 0 {
					t.Fatalf("orphan %s spend=%v ok=%v", scope.level, spent, ok)
				}
			}
		})
	}
}

type reservationCrashHook struct {
	prefix    string
	remaining int
	after     func()
	create    bool
}

func (h *reservationCrashHook) DialHook(next redis.DialHook) redis.DialHook { return next }
func (h *reservationCrashHook) ProcessPipelineHook(next redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return next
}
func (h *reservationCrashHook) ProcessHook(next redis.ProcessHook) redis.ProcessHook {
	return func(ctx context.Context, cmd redis.Cmder) error {
		err := next(ctx, cmd)
		a := cmd.Args()
		if err == nil && h.create && (cmd.Name() == "eval" || cmd.Name() == "evalsha") && len(a) == 19 && fmt.Sprint(a[2]) == "11" && fmt.Sprint(a[15]) == "1" {
			h.create = false
			panic("reservation-crash")
		}
		if err == nil && h.remaining > 0 && (cmd.Name() == "eval" || cmd.Name() == "evalsha") && len(a) > 5 && strings.HasPrefix(fmt.Sprint(a[3]), h.prefix) {
			n, _ := strconv.Atoi(fmt.Sprint(a[2]))
			cost, _ := strconv.ParseInt(fmt.Sprint(a[3+n]), 10, 64)
			if cost > 0 {
				h.remaining--
				if h.remaining == 0 {
					if h.after != nil {
						h.after()
						return err
					}
					panic("reservation-crash")
				}
			}
		}
		return err
	}
}

func TestAccept_OrphanRecoveryPreservesCommittedJob(t *testing.T) {
	c, b, p := redisFixture(t)
	now := time.Now().UTC()
	s := video.NewRedisStore(c, video.StoreOptions{Prefix: p, Budget: b, Now: func() time.Time { return now }})
	c.Redis().AddHook(&reservationCrashHook{create: true})
	j := storetest.Job(video.NewID(), "org")
	j.ReservedMicros = 2_000_000
	func() {
		defer func() {
			if recover() != "reservation-crash" {
				t.Fatal("failpoint not reached")
			}
		}()
		_, _ = s.Accept(context.Background(), video.AcceptRequest{Job: j, Operation: "submit", IdempotencyKey: "committed", Reservations: []video.BudgetReservation{{Level: "org", Period: "total", Limit: 10}}})
	}()
	now = now.Add(3 * time.Minute)
	if _, err := s.GarbageCollect(); err != nil {
		t.Fatal(err)
	}
	if _, err := s.GetAccounting(context.Background(), j.ID); err != nil {
		t.Fatal(err)
	}
	spent, _, ok := b.GetSpend("org", "org", "", "total")
	if !ok || spent != 2 {
		t.Fatalf("committed reservation spend=%v ok=%v", spent, ok)
	}
}

func TestAccept_OrphanRecoveryFencesPausedAcceptor(t *testing.T) {
	c, budget, prefix := redisFixture(t)
	now := time.Now().UTC()
	s := video.NewRedisStore(c, video.StoreOptions{Prefix: prefix, Budget: budget, Now: func() time.Time { return now }})
	paused, resume := make(chan struct{}), make(chan struct{})
	c.Redis().AddHook(&reservationCrashHook{prefix: prefix + "budget:", remaining: 1, after: func() { close(paused); <-resume }})
	j := storetest.Job(video.NewID(), "org")
	j.ReservedMicros = 2_000_000
	done := make(chan error, 1)
	go func() {
		_, err := s.Accept(context.Background(), video.AcceptRequest{Job: j, Operation: "submit", IdempotencyKey: "paused", Reservations: []video.BudgetReservation{{Level: "org", Period: "total", Limit: 10}, {Level: "key", Key: "key1", Period: "total", Limit: 10}}})
		done <- err
	}()
	<-paused
	now = now.Add(3 * time.Minute)
	_, err := s.GarbageCollect()
	close(resume)
	if err != nil {
		t.Fatal(err)
	}
	if err = <-done; !errors.Is(err, video.ErrStoreUnavailable) {
		t.Fatal(err)
	}
	if _, err = s.GetAccounting(context.Background(), j.ID); !errors.Is(err, video.ErrVideoJobNotFound) {
		t.Fatal(err)
	}
	for _, scope := range []struct{ level, key string }{{"org", ""}, {"key", "key1"}} {
		spent, _, ok := budget.GetSpend("org", scope.level, scope.key, "total")
		if !ok || spent != 0 {
			t.Fatalf("spend=%v ok=%v", spent, ok)
		}
	}
}
