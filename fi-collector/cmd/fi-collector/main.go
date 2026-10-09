// Command fi-collector — OTLP gRPC receiver → CH 25.3 spans writer.
//
// Operating modes:
//   - Standalone Docker (`docker-compose.standalone.yml`): runs as its own
//     service in front of a CH 25.3 cluster. The default.
//   - Embedded (planned): exposes a Go-API NewEmbedded() so the Django
//     `web` container can fork this in-process for single-binary deploys.
//     Out of scope for the first cut.
//
// Config priority (later overrides earlier):
//  1. Defaults coded into chwriter.New / server.New
//  2. YAML file path from --config (or /etc/fi-collector/config.yaml)
//  3. Environment overrides (FI_CH_URL, FI_GRPC_ADDR, FI_HTTP_ADDR,
//     FI_GRPC_MAX_RECV_MIB, FI_DEAD_LETTER_FILE, FI_ADMIN_ADDR, ...)
//
// Health surfaces (internal-only admin listener, default 127.0.0.1:9464,
// configurable via admin.addr or FI_ADMIN_ADDR):
//   - /healthz (HTTP 200 unless writer dead-letter rate > threshold)
//   - /metrics (Prometheus text exposition of writer + Go runtime stats)
//   - Structured logs on stderr (JSON lines)
package main

import (
	"context"
	"flag"
	"fmt"
	"log/slog"
	"net/url"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/observedcatalog"
	"github.com/future-agi/future-agi/fi-collector/pkg/pricing"
	"github.com/future-agi/future-agi/fi-collector/pkg/server"
	"github.com/future-agi/future-agi/fi-collector/pkg/traceavailable"
	"github.com/redis/go-redis/v9"
	"gopkg.in/yaml.v3"
)

type adminConfig struct {
	Addr string `yaml:"addr"`
}

type rootConfig struct {
	Writer   chwriter.Config               `yaml:"writer"`
	Server   server.Config                 `yaml:"server"`
	Auth     auth.Config                   `yaml:"auth"`
	Observed observedcatalog.RuntimeConfig `yaml:"observed_catalog"`
	Catalog  struct {
		Mode string `yaml:"mode"`
	} `yaml:"catalog"`
	PropertyCatalog struct {
		Mode string `yaml:"mode"`
	} `yaml:"property_catalog"`
	Admin adminConfig `yaml:"admin"`
}

// defaultAdminAddr is the internal-only admin listener (loopback default).
const defaultAdminAddr = "127.0.0.1:9464"

// resolveAdminAddr returns the configured admin address or the loopback default.
func resolveAdminAddr(cfg rootConfig) string {
	if addr := strings.TrimSpace(cfg.Admin.Addr); addr != "" {
		return addr
	}
	return defaultAdminAddr
}

func main() {
	var configPath string
	flag.StringVar(&configPath, "config", "/etc/fi-collector/config.yaml", "path to YAML config")
	flag.Parse()

	log := slog.New(slog.NewJSONHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelInfo}))

	cfg := loadConfig(log, configPath)
	if err := applyEnvOverrides(log, &cfg); err != nil {
		log.Error("invalid environment override", "err", err)
		os.Exit(1)
	}

	writer, err := chwriter.New(cfg.Writer)
	if err != nil {
		log.Error("chwriter init failed", "err", err)
		os.Exit(1)
	}
	defer writer.Close()

	if !cfg.Auth.IsEnabled() {
		log.Error("PostgreSQL auth configuration is required (auth.pg_write, FI_PG_WRITE, or FI_PG_WRITE_{HOST,PORT,DATABASE,USER}) — without it the collector cannot resolve API keys or project IDs")
		os.Exit(1)
	}

	var rdb *redis.Client
	if cfg.Auth.RedisAddr != "" {
		rdb = redis.NewClient(&redis.Options{Addr: cfg.Auth.RedisAddr, Password: cfg.Auth.RedisPass})
		defer rdb.Close()
	} else {
		log.Warn("FI_AUTH_REDIS_ADDR not set — quota enforcement, usage metering, key-revocation and project-delete cache invalidation are disabled; auth cache entries only expire via TTL")
	}

	authenticator, err := auth.New(context.Background(), cfg.Auth, rdb, log)
	if err != nil {
		log.Error("auth init failed", "err", err)
		os.Exit(1)
	}
	defer authenticator.Close()

	var usageEmitter server.UsageEmitter = server.NoopUsageEmitter{}
	var metering server.Metering = server.NoopMetering{}
	if rdb != nil {
		if cfg.Auth.UsageEventsOn() {
			usageEmitter = auth.NewUsageEmitter(rdb, authenticator.PGRead(), log, cfg.Auth.UsageEventsMaxLen)
		} else {
			log.Info("usage events off (USAGE_EVENTS_ENABLED=false): nothing writes the usage:events stream")
		}
		metering = auth.NewMetering(rdb, authenticator.PGRead(), log)
	}

	priceTable := loadPriceTable(log, os.Getenv("FI_PRICING_JSON"))
	var pricer *pricing.Pricer
	if priceTable != nil {
		var custom *pricing.CustomPricing
		if authenticator != nil && authenticator.PGRead() != nil {
			custom = pricing.NewCustomPricing(authenticator.PGRead(), 24*time.Hour, log)
		}
		pricer = pricing.New(priceTable, custom)
	}

	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer cancel()

	opts := []server.Option{server.WithLogger(log)}
	if pricer != nil {
		opts = append(opts, server.WithPricer(pricer))
	}
	var replayDone chan struct{}
	var finalReplay func(context.Context) (int, error)
	if cfg.Observed.Mode != "disabled" {
		catalog, err := observedcatalog.NewWriter(cfg.Observed.Spool, cfg.Observed.Limits)
		if err != nil {
			log.Error("observed catalog spool init failed", "err", err)
			os.Exit(1)
		}
		defer catalog.Close()
		var publisher observedcatalog.Publisher
		replay := catalog.Replay
		if cfg.Observed.Mode == "kafka" {
			producer, err := observedcatalog.NewProducer(cfg.Observed.Kafka)
			if err != nil {
				log.Error("observed catalog producer init failed", "err", err)
				os.Exit(1)
			}
			defer producer.Close()
			publisher = producer
		} else {
			// direct: no Kafka; replay writes the index with the consumer's sink.
			sink, err := observedcatalog.NewClickHouseSink(cfg.Observed.ClickHouse)
			if err != nil {
				log.Error("observed catalog ClickHouse sink init failed", "err", err)
				os.Exit(1)
			}
			replay, publisher = catalog.ReplayMerged, sink
		}
		opts = append(opts, server.WithPropertyCatalogWriter(catalog))
		replayDone = make(chan struct{})
		go func() {
			defer close(replayDone)
			runObservedReplay(ctx, replay, publisher, cfg.Observed.ReplayInterval, log)
		}()
		finalReplay = func(ctx context.Context) (int, error) { return replay(ctx, publisher) }
	}
	traceNotifications, err := traceavailable.FromEnv(log)
	if err != nil {
		log.Error("Error Feed notification configuration failed", "error", err)
		os.Exit(1)
	}
	if traceNotifications != nil {
		if cfg.Writer.AsyncInsert {
			log.Error("Error Feed stored-root notifications require synchronous ClickHouse inserts")
			os.Exit(1)
		}
		opts = append(opts, server.WithTraceNotifier(traceNotifications))
	}
	srv := server.New(cfg.Server, writer, authenticator, usageEmitter, metering, opts...)

	// Admin HTTP server — internal only, honors admin.addr / FI_ADMIN_ADDR.
	go runAdmin(resolveAdminAddr(cfg), writer, log)

	go authenticator.WatchRevocations(ctx)

	logStarting(log, cfg)
	runErr := srv.Run(ctx)
	if traceNotifications != nil {
		drainCtx, stopDrain := context.WithTimeout(context.Background(), 10*time.Second)
		if err := traceNotifications.Shutdown(drainCtx); err != nil {
			log.Warn("Error Feed notification shutdown left a gap", "error", err)
		}
		stopDrain()
	}
	unexpectedExit := runErr != nil && ctx.Err() == nil
	if unexpectedExit {
		log.Error("server exited with error; draining catalog lifecycle", "err", runErr)
	}
	// Server.Run has completed its final canonical drain and synchronous spool
	// handoff. Unpublished observations remain durable for the next startup.
	cancel()
	if replayDone != nil {
		<-replayDone
		// One bounded attempt at the final drain's observations: a Kubernetes
		// emptyDir spool does not outlive the pod.
		final, stopFinal := context.WithTimeout(context.Background(), 5*time.Second)
		if count, err := finalReplay(final); err != nil {
			log.Warn("observed catalog final replay incomplete; spool retained", "delivered", count, "err", err)
		}
		stopFinal()
	}
	log.Info("shutdown complete", "stats", writer.Snapshot())
	if unexpectedExit {
		os.Exit(1)
	}
}

// loadPriceTable resolves the token-pricing table. FI_PRICING_JSON is
// best-effort: a bad override file must not silently disable pricing for
// every span, so a failed override load falls back to the embedded snapshot
// (with a warn log — pricing still works — rather than an error log) rather
// than returning nil. Only a failure of the embedded snapshot itself
// (near-impossible — it's compiled in) leaves pricing disabled and logs at
// Error.
func loadPriceTable(log *slog.Logger, path string) *pricing.Table {
	table, err := pricing.LoadTable(path)
	if err != nil && path != "" {
		// Pricing still works on this path — the embedded snapshot load
		// below succeeds — so Warn, not Error; Error is reserved for the
		// double-failure case below.
		log.Warn("FI_PRICING_JSON override load failed; falling back to embedded pricing snapshot",
			"env", "FI_PRICING_JSON", "path", path, "err", err)
		table, err = pricing.LoadTable("")
	}
	if err != nil {
		log.Error("pricing table load failed; token-based cost disabled", "err", err)
	}
	if table != nil && table.Skipped > 0 {
		log.Warn("pricing table loaded with skipped entries", "skipped", table.Skipped)
	}
	return table
}

func loadConfig(log *slog.Logger, path string) rootConfig {
	cfg := rootConfig{}
	b, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			log.Warn("config file not found — using defaults + env overrides", "path", path)
			return cfg
		}
		log.Error("read config failed", "path", path, "err", err)
		os.Exit(1)
	}
	if err := yaml.Unmarshal(b, &cfg); err != nil {
		log.Error("parse config failed", "err", err)
		os.Exit(1)
	}
	return cfg
}

// logStarting masks a password in FI_CH_URL (http://user:pass@host works:
// Go's HTTP client sends it as basic auth) so it never reaches the logs.
func logStarting(log *slog.Logger, cfg rootConfig) {
	chURL := "<unparseable>"
	if u, err := url.Parse(cfg.Writer.URL); err == nil {
		chURL = u.Redacted()
	}
	log.Info("starting",
		"grpc_addr", cfg.Server.GRPCAddr,
		"http_addr", cfg.Server.HTTPAddr,
		"admin_addr", resolveAdminAddr(cfg),
		"ch_url", chURL,
	)
}

// applyEnvOverrides — surgical, only the fields ops most often need to
// override at runtime without baking a new image.
func applyEnvOverrides(log *slog.Logger, c *rootConfig) error {
	if v := os.Getenv("FI_CH_URL"); v != "" {
		c.Writer.URL = v
	}
	if v := os.Getenv("FI_CH_DATABASE"); v != "" {
		c.Writer.Database = v
	}
	if v := os.Getenv("FI_CH_USERNAME"); v != "" {
		c.Writer.Username = v
	}
	if v := os.Getenv("FI_CH_PASSWORD"); v != "" {
		c.Writer.Password = v
	}
	if v := os.Getenv("FI_GRPC_ADDR"); v != "" {
		c.Server.GRPCAddr = v
	}
	if v := os.Getenv("FI_HTTP_ADDR"); v != "" {
		// `FI_HTTP_ADDR=disable` (or `off`) turns the OTLP/HTTP listener
		// off entirely. Useful when deploying behind an external HTTP
		// gateway that strips OTLP/HTTP at the edge. The string `disable`
		// is more obvious in compose env lines than an empty value, which
		// docker compose silently swallows.
		switch v {
		case "disable", "off":
			c.Server.HTTPAddr = ""
		default:
			c.Server.HTTPAddr = v
		}
	}
	if v := os.Getenv("FI_GRPC_MAX_RECV_MIB"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			c.Server.GRPCMaxRecvMiB = n
		} else {
			// Silent fallback here would reproduce the silent-loss failure
			// mode this knob exists to fix — an operator must see it.
			log.Warn("ignoring invalid FI_GRPC_MAX_RECV_MIB", "value", v)
		}
	}
	if v := os.Getenv("FI_DEAD_LETTER_FILE"); v != "" {
		c.Writer.DeadLetterFile = v
	}
	if v := os.Getenv("FI_ADMIN_ADDR"); v != "" {
		c.Admin.Addr = v
	}
	// Explicit connection strings keep their TLS/options and take precedence
	// over separate fields. Still reject partial fields so a typo cannot silently
	// select another database via URI/YAML fallback. Errors contain names only.
	for _, endpoint := range []struct {
		prefix string
		target *string
	}{
		{"FI_PG_WRITE", &c.Auth.PGWrite},
		{"FI_PG_READ", &c.Auth.PGRead},
	} {
		fields, err := auth.EndpointFromEnv(os.Getenv, endpoint.prefix)
		if err != nil {
			return err
		}
		if uri := os.Getenv(endpoint.prefix); uri != "" {
			*endpoint.target = uri
		} else if fields != "" {
			*endpoint.target = fields
		}
	}
	if v := os.Getenv("FI_AUTH_REDIS_ADDR"); v != "" {
		c.Auth.RedisAddr = v
	}
	if v := os.Getenv("FI_AUTH_REDIS_PASSWORD"); v != "" {
		c.Auth.RedisPass = v
	}
	// Same variables as the Django emitter (tfc/settings/settings.py).
	if v := os.Getenv("USAGE_EVENTS_ENABLED"); v != "" {
		on := false
		switch strings.ToLower(strings.TrimSpace(v)) {
		case "true", "1", "yes", "on":
			on = true
		}
		c.Auth.UsageEvents = &on
	}
	if v := strings.TrimSpace(os.Getenv("USAGE_EVENTS_MAX_LEN")); v != "" {
		n, err := strconv.ParseInt(v, 10, 64)
		if err != nil || n <= 0 {
			return fmt.Errorf("USAGE_EVENTS_MAX_LEN must be a positive integer")
		}
		c.Auth.UsageEventsMaxLen = n
	}
	if (c.Catalog.Mode != "" && c.Catalog.Mode != "disabled") || (c.PropertyCatalog.Mode != "" && c.PropertyCatalog.Mode != "disabled") {
		return fmt.Errorf("legacy catalog YAML mode is obsolete; configure observed_catalog")
	}
	var err error
	c.Observed, err = observedcatalog.RuntimeFromEnv(c.Observed, os.Getenv)
	if err != nil {
		return err
	}
	if c.Observed.Mode != "disabled" && c.Writer.AsyncInsert {
		return fmt.Errorf("observed catalog requires confirmed canonical inserts; async_insert without wait is unsupported")
	}
	return nil
}
