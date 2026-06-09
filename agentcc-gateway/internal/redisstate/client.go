package redisstate

import (
	"context"
	"fmt"
	"log/slog"
	"sync/atomic"
	"time"

	"github.com/redis/go-redis/v9"
)

// Client wraps go-redis with a circuit breaker for failproof Redis access.
// When the circuit is open, all operations return errors immediately so
// callers can fall back to in-memory state without blocking.
type Client struct {
	rdb     redis.UniversalClient
	breaker *circuitBreaker
}

// Config holds Redis connection parameters.
type Config struct {
	Address  string
	Password string
	DB       int
	PoolSize int
	Timeout  time.Duration
}

// NewClient creates a Redis client with circuit breaker.
// Returns an error only if the initial ping fails.
func NewClient(cfg Config) (*Client, error) {
	if cfg.Address == "" {
		return nil, fmt.Errorf("redis address is empty")
	}
	if cfg.PoolSize <= 0 {
		cfg.PoolSize = 10
	}
	if cfg.Timeout <= 0 {
		cfg.Timeout = 2 * time.Second
	}

	rdb := redis.NewClient(&redis.Options{
		Addr:         cfg.Address,
		Password:     cfg.Password,
		DB:           cfg.DB,
		PoolSize:     cfg.PoolSize,
		DialTimeout:  cfg.Timeout,
		ReadTimeout:  cfg.Timeout,
		WriteTimeout: cfg.Timeout,
	})

	ctx, cancel := context.WithTimeout(context.Background(), cfg.Timeout)
	defer cancel()
	if err := rdb.Ping(ctx).Err(); err != nil {
		rdb.Close()
		return nil, fmt.Errorf("redis ping: %w", err)
	}

	return &Client{
		rdb:     rdb,
		breaker: newCircuitBreaker(3, 15*time.Second),
	}, nil
}

// Redis returns the underlying go-redis client for direct use.
func (c *Client) Redis() redis.UniversalClient { return c.rdb }

// Available returns true if the circuit breaker is not open.
func (c *Client) Available() bool { return c.breaker.allow() }

// Do executes fn, recording success/failure in the circuit breaker.
// Returns ErrCircuitOpen if the breaker is open.
func (c *Client) Do(fn func(rdb redis.UniversalClient) error) error {
	if !c.breaker.allow() {
		return ErrCircuitOpen
	}
	err := fn(c.rdb)
	if err != nil {
		c.breaker.recordFailure()
	} else {
		c.breaker.recordSuccess()
	}
	return err
}

// Close closes the underlying Redis connection.
func (c *Client) Close() error { return c.rdb.Close() }

// ErrCircuitOpen is returned when the circuit breaker is open.
var ErrCircuitOpen = fmt.Errorf("redis circuit breaker open")

// ---------------------------------------------------------------------------
// Circuit breaker: closed -> open -> half-open -> closed
// ---------------------------------------------------------------------------

type circuitBreaker struct {
	failures  atomic.Int64
	threshold int64
	cooldown  time.Duration
	openedAt  atomic.Int64 // unix nano; 0 = closed
}

func newCircuitBreaker(threshold int, cooldown time.Duration) *circuitBreaker {
	return &circuitBreaker{
		threshold: int64(threshold),
		cooldown:  cooldown,
	}
}

func (cb *circuitBreaker) allow() bool {
	if cb.failures.Load() < cb.threshold {
		return true // closed
	}
	opened := cb.openedAt.Load()
	if opened == 0 {
		// Race: failures crossed threshold but openedAt not yet written.
		// Set it now so the half-open timer starts; return false this time.
		cb.openedAt.CompareAndSwap(0, time.Now().UnixNano())
		return false
	}
	if time.Since(time.Unix(0, opened)) >= cb.cooldown {
		// Half-open: allow exactly ONE probe using CAS.
		// If another goroutine already advanced openedAt, this CAS fails
		// and the second goroutine is rejected — preventing double probes.
		if cb.openedAt.CompareAndSwap(opened, time.Now().UnixNano()) {
			return true
		}
		return false // another goroutine already took the probe slot
	}
	return false // open
}

func (cb *circuitBreaker) recordSuccess() {
	if cb.failures.Load() > 0 {
		cb.failures.Store(0)
		cb.openedAt.Store(0)
		slog.Info("redis circuit breaker closed")
	}
}

func (cb *circuitBreaker) recordFailure() {
	n := cb.failures.Add(1)
	if n == cb.threshold {
		cb.openedAt.Store(time.Now().UnixNano())
		slog.Warn("redis circuit breaker opened", "consecutive_failures", n, "cooldown", cb.cooldown)
	}
}
