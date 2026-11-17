package redisstate

import (
	"context"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/billing/inclusive"
	"github.com/redis/go-redis/v9"
)

// ReconcileAll discovers durable per-org indexes, not just the best-effort
// outbox hint. A crash after the terminal Lua transaction cannot strand work.
func (s *ManagedCreditStore) ReconcileAll(ctx context.Context, g inclusive.GenerationClient, f inclusive.Refund) error {
	seen := map[string]bool{}
	var first error
	for _, suffix := range []string{":recovery", ":pending"} {
		var cursor uint64
		for {
			var keys []string
			err := s.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
				var e error
				keys, cursor, e = rdb.Scan(ctx, cursor, s.prefix+"mc:{*}"+suffix, 100).Result()
				return e
			})
			if err != nil {
				return err
			}
			for _, key := range keys {
				org := strings.TrimSuffix(strings.TrimPrefix(key, s.prefix+"mc:{"), "}"+suffix)
				if seen[org] {
					continue
				}
				seen[org] = true
				if err := s.ReconcileOrg(ctx, org, g, f); err != nil && first == nil {
					first = err
				}
			}
			if cursor == 0 {
				break
			}
			if ctx.Err() != nil {
				return ctx.Err()
			}
		}
	}
	return first
}
