package cache

import (
	"bytes"
	"compress/gzip"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"slices"
	"strings"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// RedisBackend stores cached responses in Redis: one node, a Redis Cluster,
// or a master found through Redis Sentinel, over TLS when the config asks
// for it.
type RedisBackend struct {
	rdb       redis.UniversalClient
	keyPrefix string
	compress  bool
	timeout   time.Duration
}

// NewRedisBackend creates a cache on one Redis node, without TLS.
func NewRedisBackend(address, password string, db int, keyPrefix string, compress bool, poolSize int, timeout time.Duration) (*RedisBackend, error) {
	return NewRedisBackendFromConfig(config.RedisCacheConfig{
		Address:   address,
		Password:  password,
		DB:        db,
		KeyPrefix: keyPrefix,
		Compress:  compress,
		PoolSize:  poolSize,
		Timeout:   timeout,
	})
}

// NewRedisBackendFromConfig creates the cache the cache.redis block
// describes:
//
//   - mode "" or "single": the node at address (else the first of
//     addresses);
//   - mode "cluster": a Redis Cluster found from addresses (and address);
//     cluster has database 0 only, so a db other than 0 is refused;
//   - mode "sentinel": address is the master set's name (the name in the
//     sentinels' "sentinel monitor <name> ..."), addresses lists the
//     sentinels; the password is the master's, and the sentinels are asked
//     without one.
//
// With tls: true every connection (each node, each sentinel, the master) is
// TLS 1.2 or later, verified against the system's roots for the host of the
// address it dials. A connection that cannot be made over TLS fails, and the
// error is returned: it is never retried in plaintext.
func NewRedisBackendFromConfig(cfg config.RedisCacheConfig) (*RedisBackend, error) {
	return newRedisBackend(cfg, nil)
}

// newRedisBackend is NewRedisBackendFromConfig with the CA pool TLS verifies
// against (nil: the system's roots).
func newRedisBackend(cfg config.RedisCacheConfig, roots *x509.CertPool) (*RedisBackend, error) {
	poolSize := cfg.PoolSize
	if poolSize <= 0 {
		poolSize = 10
	}
	timeout := cfg.Timeout
	if timeout <= 0 {
		timeout = 5 * time.Second
	}
	var tlsCfg *tls.Config
	if cfg.TLS {
		// ServerName stays empty: crypto/tls takes it from the host of each
		// address dialled, so every node and sentinel is verified by its own
		// name.
		tlsCfg = &tls.Config{MinVersion: tls.VersionTLS12, RootCAs: roots}
	}

	mode := strings.ToLower(strings.TrimSpace(cfg.Mode))
	var rdb redis.UniversalClient
	switch mode {
	case "", "single":
		mode = "single"
		addr := cfg.Address
		if addr == "" && len(cfg.Addresses) > 0 {
			addr = cfg.Addresses[0]
			if len(cfg.Addresses) > 1 {
				slog.Warn("redis cache: mode single uses only the first of addresses; set mode cluster or sentinel to use them all",
					"address", addr, "ignored", len(cfg.Addresses)-1)
			}
		}
		if addr == "" {
			return nil, errors.New("address is empty")
		}
		rdb = redis.NewClient(&redis.Options{
			Addr:            addr,
			Password:        cfg.Password,
			DB:              cfg.DB,
			PoolSize:        poolSize,
			DialTimeout:     timeout,
			ReadTimeout:     timeout,
			WriteTimeout:    timeout,
			TLSConfig:       tlsCfg,
			DisableIdentity: true,
		})

	case "cluster":
		seeds := slices.Clone(cfg.Addresses)
		if cfg.Address != "" && !slices.Contains(seeds, cfg.Address) {
			seeds = append(seeds, cfg.Address)
		}
		if len(seeds) == 0 {
			return nil, errors.New("mode cluster needs addresses: the cluster's nodes")
		}
		if cfg.DB != 0 {
			return nil, fmt.Errorf("mode cluster has database 0 only; db %d cannot be used", cfg.DB)
		}
		rdb = redis.NewClusterClient(&redis.ClusterOptions{
			Addrs:           seeds,
			Password:        cfg.Password,
			PoolSize:        poolSize,
			DialTimeout:     timeout,
			ReadTimeout:     timeout,
			WriteTimeout:    timeout,
			TLSConfig:       tlsCfg,
			DisableIdentity: true,
		})

	case "sentinel":
		master := strings.TrimSpace(cfg.Address)
		if master == "" {
			return nil, errors.New("mode sentinel needs address: the master set's name (as in the sentinels' \"sentinel monitor <name> ...\"), and addresses: the sentinels")
		}
		if _, _, err := net.SplitHostPort(master); err == nil {
			return nil, fmt.Errorf("mode sentinel reads address as the master set's name, not a host:port (%q); list the sentinels under addresses", master)
		}
		if len(cfg.Addresses) == 0 {
			return nil, errors.New("mode sentinel needs addresses: the sentinels")
		}
		rdb = redis.NewFailoverClient(&redis.FailoverOptions{
			MasterName:      master,
			SentinelAddrs:   slices.Clone(cfg.Addresses),
			Password:        cfg.Password,
			DB:              cfg.DB,
			PoolSize:        poolSize,
			DialTimeout:     timeout,
			ReadTimeout:     timeout,
			WriteTimeout:    timeout,
			TLSConfig:       tlsCfg,
			DisableIdentity: true,
		})

	default:
		return nil, fmt.Errorf("unknown mode %q (supported: single, cluster, sentinel)", cfg.Mode)
	}

	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	if err := rdb.Ping(ctx).Err(); err != nil {
		_ = rdb.Close()
		if cfg.TLS {
			return nil, fmt.Errorf("mode %s over TLS (never plaintext): %w", mode, err)
		}
		return nil, fmt.Errorf("mode %s: %w", mode, err)
	}

	return &RedisBackend{
		rdb:       rdb,
		keyPrefix: cfg.KeyPrefix,
		compress:  cfg.Compress,
		timeout:   timeout,
	}, nil
}

func (r *RedisBackend) Get(key string) (*models.ChatCompletionResponse, bool) {
	ctx, cancel := context.WithTimeout(context.Background(), r.timeout)
	defer cancel()
	body, err := r.rdb.Get(ctx, r.keyPrefix+key).Bytes()
	if err != nil {
		if !errors.Is(err, redis.Nil) {
			slog.Warn("redis cache get error", "error", err)
		}
		return nil, false
	}

	if r.compress {
		gr, err := gzip.NewReader(bytes.NewReader(body))
		if err != nil {
			return nil, false
		}
		body, err = io.ReadAll(gr)
		gr.Close()
		if err != nil {
			return nil, false
		}
	}

	var resp models.ChatCompletionResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		return nil, false
	}
	return &resp, true
}

func (r *RedisBackend) Set(key string, resp *models.ChatCompletionResponse, ttl time.Duration) {
	if ttl <= 0 || resp == nil {
		return
	}

	data, err := json.Marshal(resp)
	if err != nil {
		return
	}

	body := data
	if r.compress {
		var buf bytes.Buffer
		gw := gzip.NewWriter(&buf)
		gw.Write(data)
		gw.Close()
		body = buf.Bytes()
	}

	// Whole seconds (SET ... EX), at least one.
	ttlSec := int64(ttl.Seconds())
	if ttlSec <= 0 {
		ttlSec = 1
	}

	ctx, cancel := context.WithTimeout(context.Background(), r.timeout)
	defer cancel()
	if err := r.rdb.Set(ctx, r.keyPrefix+key, body, time.Duration(ttlSec)*time.Second).Err(); err != nil {
		slog.Warn("redis cache set error", "key", key, "error", err)
	}
}

func (r *RedisBackend) Delete(key string) {
	ctx, cancel := context.WithTimeout(context.Background(), r.timeout)
	defer cancel()
	r.rdb.Del(ctx, r.keyPrefix+key)
}

// Close closes every connection.
func (r *RedisBackend) Close() {
	_ = r.rdb.Close()
}
