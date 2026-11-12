package redisstate

import (
	"context"
	"errors"

	"github.com/redis/go-redis/v9"
)

// Ready makes one bounded, read-only probe with the configured credentials.
// Its deadline/retry policy is isolated from ordinary state operations: hosted
// readiness must not change self-hosted Redis or settlement semantics.
func (c *Client) Ready(ctx context.Context) error {
	base, ok := c.rdb.(*redis.Client)
	if !ok {
		return errors.New("redis readiness unavailable")
	}
	options := *base.Options()
	options.ContextTimeoutEnabled = true
	options.MaxRetries = -1
	options.PoolSize = 1
	options.MinIdleConns = 0
	options.MaxActiveConns = 1
	probe := redis.NewClient(&options)
	defer probe.Close()
	return probe.Ping(ctx).Err()
}
