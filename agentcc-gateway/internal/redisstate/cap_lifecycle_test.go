package redisstate

import (
	"context"
	"errors"
	"net"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

type capLostReply struct{ armed atomic.Bool }

func (h *capLostReply) DialHook(next redis.DialHook) redis.DialHook {
	return func(ctx context.Context, network, addr string) (net.Conn, error) { return next(ctx, network, addr) }
}
func (h *capLostReply) ProcessPipelineHook(next redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return next
}
func (h *capLostReply) ProcessHook(next redis.ProcessHook) redis.ProcessHook {
	return func(ctx context.Context, cmd redis.Cmder) error {
		err := next(ctx, cmd)
		if err == nil && strings.HasPrefix(cmd.Name(), "eval") && h.armed.CompareAndSwap(true, false) {
			err = errors.New("synthetic lost reply after execution")
			cmd.SetErr(err)
		}
		return err
	}
}

func TestCapAmbiguousRepliesAndTerminalConflicts(t *testing.T) {
	s := capTestStore(t)
	hook := &capLostReply{}
	s.store.client.Redis().AddHook(hook)
	buckets := activatedBuckets(t, "key_id:k", 50000, 4000)
	hook.armed.Store(true)
	if _, err := s.Reserve(bg, "g8-org", "2026-10-27", "n", 1, buckets, time.Now().Add(time.Minute)); err == nil {
		t.Fatal("lost reserve reply not injected")
	}
	result, err := s.Reserve(bg, "g8-org", "2026-10-27", "n", 1, buckets, time.Now().Add(time.Minute))
	if err != nil || result.Status != "ok" {
		t.Fatalf("reserve replay: %+v %v", result, err)
	}
	actual := []CapBucket{{Level: "key_id:k", Amount: 3000}}
	hook.armed.Store(true)
	if err := s.Complete(bg, "g8-org", "2026-10-27", "n", actual); err == nil {
		t.Fatal("lost complete reply not injected")
	}
	if err := s.Complete(bg, "g8-org", "2026-10-27", "n", actual); err != nil {
		t.Fatal(err)
	}
	if err := s.Complete(bg, "g8-org", "2026-10-27", "n", []CapBucket{{Level: "key_id:k"}}); err == nil {
		t.Fatal("settle/void conflict accepted")
	}
	keys, _ := s.keys("g8-org", "2026-10-27")
	if got := s.store.client.Redis().HGet(bg, keys[0], "spent:key_id:k").Val(); got != "3000" {
		t.Fatalf("double settlement: %s", got)
	}
	if got := s.store.client.Redis().HGet(bg, keys[0], "held:key_id:k").Val(); got != "0" {
		t.Fatalf("double reserve: %s", got)
	}
}

func TestCapReplicaCrashAndExpiryFence(t *testing.T) {
	s := capTestStore(t)
	buckets := activatedBuckets(t, "agent:a", 5000, 4000)
	result, err := s.Reserve(bg, "g8-org", "2026-10-27", "crashed", 1, buckets, time.Now().Add(time.Hour))
	if err != nil || result.Status != "ok" {
		t.Fatalf("%+v %v", result, err)
	}
	// Independent instance has no process-local state; live work stays held.
	replica := NewCapHoldStore(s.store.client, s.store.prefix)
	result, err = replica.Reserve(bg, "g8-org", "2026-10-27", "next", 1, buckets, time.Now().Add(time.Hour))
	if err != nil || result.Status != "exhausted" || result.Available != 1000 {
		t.Fatalf("lost live hold: %+v %v", result, err)
	}
	keys, _ := s.keys("g8-org", "2026-10-27")
	s.store.client.Redis().ZAdd(bg, keys[2], redis.Z{Member: "crashed", Score: float64(time.Now().Add(-time.Minute).UnixMilli())})
	result, err = replica.Reserve(bg, "g8-org", "2026-10-27", "expired", 1, buckets, time.Now().Add(time.Hour))
	if err != nil || result.Status != "unavailable" {
		t.Fatalf("expired uncertainty admitted: %+v %v", result, err)
	}
	if ttl := s.store.client.Redis().TTL(bg, keys[0]).Val(); ttl != -1 {
		t.Fatalf("authoritative state expires: %s", ttl)
	}
}

func TestCapActualOverEstimateAndMissingBucket(t *testing.T) {
	for _, lost := range []bool{false, true} {
		t.Run(map[bool]string{false: "overshoot", true: "lost-state"}[lost], func(t *testing.T) {
			s := capTestStore(t)
			buckets := activatedBuckets(t, "agent:a", 50000, 4000)
			result, err := s.Reserve(bg, "g8-org", "2026-10-27", "n", 1, buckets, time.Now().Add(time.Minute))
			if err != nil || result.Status != "ok" {
				t.Fatalf("%+v %v", result, err)
			}
			if err := s.Complete(bg, "g8-org", "2026-10-27", "n", []CapBucket{{Level: "agent:a", Amount: 6000}}); err != nil {
				t.Fatal(err)
			}
			keys, _ := s.keys("g8-org", "2026-10-27")
			if got := s.store.client.Redis().HGet(bg, keys[0], "spent:agent:a").Val(); got != "6000" {
				t.Fatalf("actual clipped: %s", got)
			}
			if lost {
				s.store.client.Redis().Del(bg, keys[0])
			}
			result, err = s.Reserve(bg, "g8-org", "2026-10-27", "later", 2, buckets, time.Now().Add(time.Minute))
			if err != nil || result.Status != "unavailable" {
				t.Fatalf("uncertainty not fenced: %+v %v", result, err)
			}
		})
	}
}
