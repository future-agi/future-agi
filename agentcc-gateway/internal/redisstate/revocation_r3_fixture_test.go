package redisstate

import (
	"context"
	"github.com/redis/go-redis/v9"
)

// The retained r3 fixture injects failures in PUBLISH. Persistence succeeds;
// TestRevocationPersistsBeforePublish separately covers failed persistence.
func (r *revocationResultRedis) SAdd(context.Context, string, ...interface{}) *redis.IntCmd {
	return redis.NewIntResult(1, nil)
}
