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
//     FI_DEAD_LETTER_FILE)
//
// Health surfaces:
//   - /healthz (HTTP 200 unless writer dead-letter rate > threshold)
//   - /metrics (Prometheus exposition of chwriter.Stats)
//   - Structured logs on stderr (JSON lines)
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/server"
	"gopkg.in/yaml.v3"
)

type rootConfig struct {
	Writer chwriter.Config `yaml:"writer"`
	Server server.Config   `yaml:"server"`
	Admin  struct {
		Addr string `yaml:"addr"` // :9464 default
	} `yaml:"admin"`
}

func main() {
	var configPath string
	flag.StringVar(&configPath, "config", "/etc/fi-collector/config.yaml", "path to YAML config")
	flag.Parse()

	log := slog.New(slog.NewJSONHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelInfo}))

	cfg := loadConfig(log, configPath)
	applyEnvOverrides(&cfg)

	writer, err := chwriter.New(cfg.Writer)
	if err != nil {
		log.Error("chwriter init failed", "err", err)
		os.Exit(1)
	}
	defer writer.Close()

	srv := server.New(cfg.Server, writer)

	// Admin HTTP server (health + metrics). Kept tiny so it can't share
	// blast radius with the OTLP receiver — even if it deadlocks, OTLP
	// keeps ingesting.
	if cfg.Admin.Addr == "" {
		cfg.Admin.Addr = ":9464"
	}
	go runAdmin(cfg.Admin.Addr, writer, log)

	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer cancel()

	log.Info("starting",
		"grpc_addr", cfg.Server.GRPCAddr,
		"http_addr", cfg.Server.HTTPAddr,
		"ch_url", cfg.Writer.URL,
		"admin_addr", cfg.Admin.Addr,
	)
	if err := srv.Run(ctx); err != nil && ctx.Err() == nil {
		log.Error("server exited with error", "err", err)
		os.Exit(1)
	}
	log.Info("shutdown complete", "stats", writer.Snapshot())
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

// applyEnvOverrides — surgical, only the fields ops most often need to
// override at runtime without baking a new image.
func applyEnvOverrides(c *rootConfig) {
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
	if v := os.Getenv("FI_DEAD_LETTER_FILE"); v != "" {
		c.Writer.DeadLetterFile = v
	}
	if v := os.Getenv("FI_ADMIN_ADDR"); v != "" {
		c.Admin.Addr = v
	}
}

// runAdmin serves health + Prometheus-format metrics. Built without
// pulling in github.com/prometheus/client_golang because the surface we
// expose is two counters — handing back text is ~10 lines.
func runAdmin(addr string, w *chwriter.Writer, log *slog.Logger) {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(rw http.ResponseWriter, r *http.Request) {
		s := w.Snapshot()
		// We're healthy as long as we're not dead-lettering catastrophically.
		// Threshold: > 50% of recently-inserted batches dead-lettered.
		denom := s.BatchesInserted + s.BatchesFailed
		if denom > 100 && s.BatchesFailed*2 > denom {
			rw.WriteHeader(503)
			_ = json.NewEncoder(rw).Encode(map[string]any{"status": "unhealthy", "stats": s})
			return
		}
		rw.WriteHeader(200)
		_ = json.NewEncoder(rw).Encode(map[string]any{"status": "ok", "stats": s})
	})
	mux.HandleFunc("/metrics", func(rw http.ResponseWriter, r *http.Request) {
		s := w.Snapshot()
		rw.Header().Set("Content-Type", "text/plain; version=0.0.4")
		fmt.Fprintf(rw, "# TYPE ficollector_batches_inserted_total counter\nficollector_batches_inserted_total %d\n", s.BatchesInserted)
		fmt.Fprintf(rw, "# TYPE ficollector_rows_inserted_total counter\nficollector_rows_inserted_total %d\n", s.RowsInserted)
		fmt.Fprintf(rw, "# TYPE ficollector_batches_retried_total counter\nficollector_batches_retried_total %d\n", s.BatchesRetried)
		fmt.Fprintf(rw, "# TYPE ficollector_rows_dead_lettered_total counter\nficollector_rows_dead_lettered_total %d\n", s.RowsDeadLettered)
		fmt.Fprintf(rw, "# TYPE ficollector_batches_failed_total counter\nficollector_batches_failed_total %d\n", s.BatchesFailed)
		// Curated-dimension (end_users / trace_sessions) best-effort path —
		// tracked separately so it never affects /healthz (which is span-only).
		fmt.Fprintf(rw, "# TYPE ficollector_curated_batches_inserted_total counter\nficollector_curated_batches_inserted_total %d\n", s.CuratedBatchesInserted)
		fmt.Fprintf(rw, "# TYPE ficollector_curated_batches_failed_total counter\nficollector_curated_batches_failed_total %d\n", s.CuratedBatchesFailed)
	})
	srv := &http.Server{Addr: addr, Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Warn("admin server stopped", "err", err)
	}
}
