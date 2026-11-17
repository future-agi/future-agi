package redisstate

import (
	"context"
	"fmt"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/redis/go-redis/v9"
)

type r602PauseReserve struct {
	entered, release chan struct{}
	paused           atomic.Bool
}

func (h *r602PauseReserve) DialHook(next redis.DialHook) redis.DialHook { return next }
func (h *r602PauseReserve) ProcessPipelineHook(next redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return next
}
func (h *r602PauseReserve) ProcessHook(next redis.ProcessHook) redis.ProcessHook {
	return func(ctx context.Context, cmd redis.Cmder) error {
		err := next(ctx, cmd)
		if err == nil && (cmd.Name() == "evalsha" || cmd.Name() == "eval") {
			for _, arg := range cmd.Args() {
				if fmt.Sprint(arg) == "provisional-race" && h.paused.CompareAndSwap(false, true) {
					close(h.entered)
					select {
					case <-h.release:
					case <-ctx.Done():
						return ctx.Err()
					}
					break
				}
			}
		}
		return err
	}
}
func TestR602QuarantineWinsProvisionalReservationRace(t *testing.T) {
	s, rdb, _ := managedTestStore(t)
	mustGrant(t, s, "org-owned", 10000, 1, 0)
	mustGrant(t, s, "org-other", 10000, 1, 0)
	model := "google/gemini-3.5-flash"
	if _, e := s.ReserveInclusive(bg, "org-owned", "req", "req.1", model, 100, time.Hour, billing.ReserveNew); e != nil {
		t.Fatal(e)
	}
	if e := s.BeginInclusive(bg, "org-owned", "req", "req.1"); e != nil {
		t.Fatal(e)
	}
	hook := &r602PauseReserve{entered: make(chan struct{}), release: make(chan struct{})}
	rdb.AddHook(hook)
	result := make(chan error, 1)
	ctx, cancel := context.WithTimeout(bg, 10*time.Second)
	defer cancel()
	go func() {
		_, err := s.ReserveInclusive(ctx, "org-other", "provisional-race", "provisional-race.1", model, 100, time.Hour, billing.ReserveNew)
		result <- err
	}()
	select {
	case <-hook.entered:
	case <-ctx.Done():
		t.Fatal("reservation did not reach financial slot")
	}
	if _, e := s.Settle(bg, "org-owned", "req", ownedRecord("req", 105)); e != nil {
		close(hook.release)
		t.Fatal(e)
	}
	close(hook.release)
	select {
	case err := <-result:
		if err == nil {
			t.Fatal("quarantined provisional hold confirmed")
		}
	case <-ctx.Done():
		t.Fatal("reservation did not finish")
	}
	if a := mustAccount(t, s, "org-other"); a.HeldMicros != 0 || a.SpentMicros != 0 {
		t.Fatalf("provisional hold was not rolled back %+v", a)
	}
	if e := s.BeginInclusive(bg, "org-other", "provisional-race", "provisional-race.1"); e == nil {
		t.Fatal("cancelled provisional hold dispatched")
	}
}
