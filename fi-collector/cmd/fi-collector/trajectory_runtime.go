package main

import (
	"context"
	"crypto/tls"
	"errors"
	"log/slog"
	"net"
	"strings"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/server"
	"github.com/future-agi/future-agi/fi-collector/pkg/trajectory"
	"github.com/redis/go-redis/v9"
)

// newRedisClient builds the collector's Redis client from FI_AUTH_REDIS_ADDR
// and FI_AUTH_REDIS_PASSWORD. The address is either host:port or a
// redis:// / rediss:// URL; rediss:// connects with TLS, as does host:port
// with FI_REDIS_TLS=true (system CA pool, server name from the host). A
// password in the URL wins over FI_AUTH_REDIS_PASSWORD. Errors never include
// the address, which may carry a password.
func newRedisClient(addr, password string, getenv func(string) string) (*redis.Client, error) {
	addr = strings.TrimSpace(addr)
	var opts *redis.Options
	if strings.HasPrefix(addr, "redis://") || strings.HasPrefix(addr, "rediss://") {
		parsed, err := redis.ParseURL(addr)
		if err != nil {
			return nil, errors.New("FI_AUTH_REDIS_ADDR is not a valid redis:// or rediss:// URL (value not shown)")
		}
		opts = parsed
		if opts.Password == "" {
			opts.Password = password
		}
	} else {
		opts = &redis.Options{Addr: addr, Password: password}
	}
	tlsFlag := strings.ToLower(strings.TrimSpace(getenv("FI_REDIS_TLS")))
	switch tlsFlag {
	case "", "false", "0", "no", "off":
	case "true", "1", "yes", "on":
		if opts.TLSConfig == nil {
			host, _, err := net.SplitHostPort(opts.Addr)
			if err != nil {
				host = opts.Addr
			}
			opts.TLSConfig = &tls.Config{MinVersion: tls.VersionTLS12, ServerName: host}
		}
	default:
		return nil, errors.New("FI_REDIS_TLS must be true or false")
	}
	if opts.TLSConfig != nil && opts.TLSConfig.MinVersion < tls.VersionTLS12 {
		opts.TLSConfig.MinVersion = tls.VersionTLS12
	}
	return redis.NewClient(opts), nil
}

// trajectoryOptions wires trajectory dedup only when it can run for some
// project. With dedup off nothing is built: no seen store, no gate, no
// metadata queries and no reads of the gate key, so the collector behaves
// exactly as without the feature. The billing flag alone wires nothing.
func trajectoryOptions(ctx context.Context, log *slog.Logger, cfg trajectory.Config, writer *chwriter.Writer, rdb *redis.Client, authenticator *auth.Authenticator) []server.Option {
	if !cfg.Active() {
		return nil
	}
	seen := trajectory.NewSeenStoreFor(cfg, rdb)
	if authenticator != nil {
		authenticator.OnProjectInvalidate(func(projectID string) {
			seen.InvalidateProject(projectID)
		})
	}
	gateCfg := trajectory.GateConfig{
		ItemsInsertTimeout: cfg.ItemsInsertTimeout,
		Log:                log,
	}
	if writer != nil {
		gateCfg.Prober = writer
		gateCfg.SpansTable = writer.Policy().Table
	}
	if rdb != nil {
		gateCfg.Redis = rdb
	} else {
		log.Warn("trajectory dedup configured without Redis (FI_AUTH_REDIS_ADDR): the dedup gate key cannot be read, so dedup stays off",
			"event", "trajectory_dedup_gate_closed", "reason", "redis_not_configured")
	}
	gate := trajectory.NewGate(gateCfg)
	go gate.Run(ctx)
	return []server.Option{server.WithTrajectory(cfg, seen), server.WithTrajectoryGate(gate)}
}
