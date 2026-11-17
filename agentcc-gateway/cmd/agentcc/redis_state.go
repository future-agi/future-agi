package main

import (
	"log/slog"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// redisStateMemoryWarning is the start-up warning of a gateway that keeps its
// budgets and rate counters in memory. It names no address, password or URL.
const redisStateMemoryWarning = "Budgets and rate limits are kept in memory: they reset when the gateway restarts and are not shared between replicas. Set AGENTCC_REDIS_ADDRESS to keep them."

// connectRedisState returns the shared Redis client for budgets, rate limits,
// credits and the cluster, connecting with connect (redisstate.NewClient).
// Without one (no Redis configured, or its start-up check failed) it returns
// nil and logs redisStateMemoryWarning once. The check's error is never
// logged: its text can carry the address. A hosted gateway does not fall
// back: its failed check is returned for main to stop on.
func connectRedisState(cfg *config.Config, connect func(redisstate.Config) (*redisstate.Client, error)) (*redisstate.Client, error) {
	if !cfg.Redis.Enabled {
		slog.Warn(redisStateMemoryWarning, "redis", "not configured")
		return nil, nil
	}
	rc, err := connect(redisstate.Config{
		Address:  cfg.Redis.Address,
		Password: cfg.Redis.Password,
		DB:       cfg.Redis.DB,
		PoolSize: cfg.Redis.PoolSize,
		Timeout:  cfg.Redis.Timeout,
	})
	if err != nil {
		if cfg.IsHosted() {
			return nil, err
		}
		slog.Warn(redisStateMemoryWarning, "redis", "start-up check failed")
		return nil, nil
	}
	return rc, nil
}
