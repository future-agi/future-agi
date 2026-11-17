package sessionlease

import (
	"context"
	"time"

	"github.com/redis/go-redis/v9"
)

// New borrows a qualified primary-only client. It does not close that client.
func New(client redis.UniversalClient, p Policy) (*Store, error) {
	if validatePolicy(p) != nil {
		return nil, ErrBinding
	}
	qualified := false
	switch c := client.(type) {
	case *redis.Client:
		if c != nil {
			o := c.Options()
			qualified = o.ContextTimeoutEnabled && o.MaxRetries == 0 && timeouts(o.DialTimeout, o.ReadTimeout, o.WriteTimeout)
		}
	case *redis.ClusterClient:
		if c != nil {
			o := c.Options()
			// go-redis v9.18 retains -1 for cluster node retries, whereas
			// single-client options normalize -1 to 0. The outer cluster
			// redirect loop also retries ambiguous network failures: disable it.
			qualified = o.ContextTimeoutEnabled && o.MaxRetries == -1 && o.MaxRedirects == 0 && !o.ReadOnly && !o.RouteByLatency && !o.RouteRandomly && timeouts(o.DialTimeout, o.ReadTimeout, o.WriteTimeout)
		}
	}
	if !qualified {
		return nil, ErrUnavailable
	}
	return &Store{policy: p, eval: func(ctx context.Context, script string, keys []string, args ...any) (any, error) {
		return client.Eval(ctx, script, keys, args...).Result()
	}}, nil
}
func timeouts(ds ...time.Duration) bool {
	for _, d := range ds {
		if d <= 0 || d > operationBudget {
			return false
		}
	}
	return true
}
func NewSingle(options redis.Options, p Policy) (*Store, error) {
	options.ContextTimeoutEnabled = true
	options.MaxRetries = -1
	options.DialTimeout = operationBudget
	options.ReadTimeout = operationBudget
	options.WriteTimeout = operationBudget
	client := redis.NewClient(&options)
	s, err := New(client, p)
	if err != nil {
		_ = client.Close()
		return nil, err
	}
	s.close = client.Close
	return s, nil
}
func NewCluster(options redis.ClusterOptions, p Policy) (*Store, error) {
	if options.ReadOnly || options.RouteByLatency || options.RouteRandomly {
		return nil, ErrUnavailable
	}
	options.ContextTimeoutEnabled = true
	options.MaxRetries = -1
	// -1 normalizes to zero outer attempts beyond the first; MOVED/ASK and
	// uncertain replies are surfaced, never replayed as lease mutations.
	options.MaxRedirects = -1
	options.DialTimeout = operationBudget
	options.ReadTimeout = operationBudget
	options.WriteTimeout = operationBudget
	client := redis.NewClusterClient(&options)
	s, err := New(client, p)
	if err != nil {
		_ = client.Close()
		return nil, err
	}
	s.close = client.Close
	return s, nil
}
func (s *Store) Close() error {
	if s != nil && s.close != nil && s.close() != nil {
		return ErrUnavailable
	}
	return nil
}
