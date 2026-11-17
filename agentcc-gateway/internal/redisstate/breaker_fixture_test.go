package redisstate

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/routing/affinity"
	"github.com/futureagi/agentcc-gateway/internal/routing/outcomes"
)

// openBreakerClient is a nil-Redis fixture for unavailable-store tests.
func openBreakerClient(t *testing.T) *Client {
	t.Helper()
	// Use the maximum duration: this nil-Redis fixture must never admit a probe.
	client := &Client{breaker: newCircuitBreaker(1, time.Duration(1<<63-1))}
	client.breaker.recordFailure()
	return client
}

func TestOpenBreakerClientNeverHalfOpens(t *testing.T) {
	cases := []struct {
		name string
		run  func(*Client) bool
	}{
		{
			name: "RateLimiter.Allow",
			run: func(client *Client) bool {
				local := &mockLocalLimiter{allowed: false, remain: 0, resetSec: 30}
				rl := NewRateLimiter(client, local, "test:breaker:rl:")
				allowed, remaining, reset := rl.Allow("key1", 10)
				return !allowed && remaining == 0 && reset == 30 && local.calls == 1
			},
		},
		{
			name: "BudgetStore.Available",
			run: func(client *Client) bool {
				return !NewBudgetStore(client, "test:breaker:budget:").Available()
			},
		},
		{
			name: "BudgetStore.RecordSpend",
			run: func(client *Client) bool {
				bs := NewBudgetStore(client, "test:breaker:budget:")
				total, ok := bs.RecordSpend("org1", "org", "", "monthly", "", 1.0)
				return !ok && total == -1
			},
		},
		{
			name: "ClusterStore.Available",
			run: func(client *Client) bool {
				return !NewClusterStore(client, 30*time.Second, "test:breaker:cluster:").Available()
			},
		},
		{
			name: "CreditStore.Available",
			run: func(client *Client) bool {
				return !NewCreditStore(client, "test:breaker:credits:").Available()
			},
		},
		{
			name: "CreditStore.GetBalance",
			run: func(client *Client) bool {
				balance, ok := NewCreditStore(client, "test:breaker:credits:").GetBalance("key1")
				return !ok && balance == -1
			},
		},
		{
			name: "AgentSignalStore.GetAffinity",
			run: func(client *Client) bool {
				store := NewAgentSignalStore(client, "test:breaker:signals:")
				_, ok, err := store.GetAffinity(context.Background(), "k")
				return !ok && errors.Is(err, ErrCircuitOpen)
			},
		},
		{
			name: "affinity.Store.Set/Get",
			run: func(client *Client) bool {
				store := NewAgentSignalStore(client, "test:breaker:signals:")
				aff := affinity.NewStore(store, 10, newSignalClock().now)
				ctx := context.Background()
				pin := affinity.Value{Model: "vendor/m", RequestID: "req-1"}
				aff.Set(ctx, "org-1", "s1", affinity.ClassMain, pin, time.Hour)
				got, ok := aff.Get(ctx, "org-1", "s1", affinity.ClassMain)
				return ok && got == pin
			},
		},
		{
			name: "outcomes.Store.Remember/Last",
			run: func(client *Client) bool {
				store := NewAgentSignalStore(client, "test:breaker:signals:")
				out := outcomes.NewStore(store, 10, newSignalClock().now)
				ctx := context.Background()
				last := outcomes.Last{Model: "vendor/m"}
				out.Remember(ctx, "org-1", "req-1", last)
				got, ok := out.Last(ctx, "org-1", "req-1")
				return ok && got == last
			},
		},
		{
			name: "ManagedCreditStore.Settle",
			run: func(client *Client) bool {
				store := NewManagedCreditStore(client, "test:breaker:managed:")
				_, err := store.Settle(context.Background(), "org", "r", billing.Settlement{})
				return errors.Is(err, billing.ErrUnavailable)
			},
		},
		{
			name: "ManagedCreditStore.Kind",
			run: func(client *Client) bool {
				return NewManagedCreditStore(client, "test:breaker:managed:").Kind() == billing.KindRedis
			},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			defer func() {
				if recover() != nil {
					t.Errorf("%s: unavailable-client operation panicked", tc.name)
				}
			}()

			// Isolate each case: an earlier half-open probe must not mask it.
			client := openBreakerClient(t)
			client.breaker.openedAt.Store(time.Now().Add(-11 * time.Second).UnixNano())
			if !tc.run(client) {
				t.Errorf("%s: unavailable-client fallback was not preserved", tc.name)
			}
		})
	}
}
