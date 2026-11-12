package sessionlease

import (
	"context"
	"errors"
	"net"
	"sync/atomic"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

func noIODialer(dials *atomic.Int32) func(context.Context, string, string) (net.Conn, error) {
	return func(context.Context, string, string) (net.Conn, error) {
		dials.Add(1)
		return nil, errors.New("synthetic-no-io")
	}
}

func TestConstructorsQualifyWithoutIO(t *testing.T) {
	var dials atomic.Int32
	defer func() {
		if dials.Load() != 0 {
			t.Error("constructor performed I/O")
		}
	}()
	p := fixturePolicy()
	single, err := NewSingle(redis.Options{Addr: "synthetic.invalid:1", Dialer: noIODialer(&dials)}, p)
	if err != nil {
		t.Fatal(err)
	}
	if single == nil || single.close == nil {
		t.Fatal("missing owned single client")
	}
	if err := single.Close(); err != nil {
		t.Fatal(err)
	}
	cluster, err := NewCluster(redis.ClusterOptions{Addrs: []string{"synthetic.invalid:1"}, Dialer: noIODialer(&dials)}, p)
	if err != nil {
		t.Fatalf("qualified cluster rejected: %v", err)
	}
	if cluster == nil || cluster.close == nil {
		t.Fatal("missing owned cluster client")
	}
	if err := cluster.Close(); err != nil {
		t.Fatal(err)
	}
}

func TestBorrowedClusterNormalizationAndQualification(t *testing.T) {
	for _, tc := range []struct {
		name    string
		alter   func(*redis.ClusterOptions)
		allowed bool
	}{
		{"qualified", func(*redis.ClusterOptions) {}, true},
		{"node-retries", func(o *redis.ClusterOptions) { o.MaxRetries = 1 }, false},
		{"ambiguous-redirect-retries", func(o *redis.ClusterOptions) { o.MaxRedirects = 1 }, false},
		{"missing-context", func(o *redis.ClusterOptions) { o.ContextTimeoutEnabled = false }, false},
		{"read-only", func(o *redis.ClusterOptions) { o.ReadOnly = true }, false},
		{"unbounded-timeout", func(o *redis.ClusterOptions) { o.ReadTimeout = time.Second }, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var dials atomic.Int32
			defer func() {
				if dials.Load() != 0 {
					t.Error("qualification performed I/O")
				}
			}()
			options := redis.ClusterOptions{Addrs: []string{"synthetic.invalid:1"}, Dialer: noIODialer(&dials), ContextTimeoutEnabled: true, MaxRetries: -1, MaxRedirects: -1, DialTimeout: operationBudget, ReadTimeout: operationBudget, WriteTimeout: operationBudget}
			tc.alter(&options)
			client := redis.NewClusterClient(&options)
			defer client.Close()
			if tc.allowed && (client.Options().MaxRetries != -1 || client.Options().MaxRedirects != 0) {
				t.Fatal("pinned ClusterOptions normalization changed")
			}
			store, err := New(client, fixturePolicy())
			if tc.allowed {
				if err != nil || store == nil {
					t.Fatalf("qualified borrowed cluster: %v", err)
				}
				if store.close != nil {
					t.Fatal("borrowed client became owned")
				}
			} else if err != ErrUnavailable || store != nil {
				t.Fatalf("unsafe borrowed cluster accepted: %v", err)
			}
		})
	}
}
