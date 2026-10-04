package server

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/otel"
	provider "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"github.com/futureagi/agentcc-gateway/internal/providers/video/byteplus"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/secrets"
	"github.com/futureagi/agentcc-gateway/internal/video"
	"github.com/futureagi/agentcc-gateway/internal/video/artifacts"
	"github.com/futureagi/agentcc-gateway/internal/video/lifecycle"
	"github.com/redis/go-redis/v9"
)

func (s *Server) configureVideo(client *redisstate.Client) error {
	cfg := s.cfg.Video
	prefix := s.cfg.Redis.Prefix
	if prefix == "" {
		prefix = "agentcc:"
	}
	budget := redisstate.NewBudgetStore(client, prefix+"budget:")
	opts := video.StoreOptions{Budget: budget, IdempotencyTTL: cfg.Retention.Idempotency}
	if cfg.Store == "memory" && cfg.AllowNonDurableStore {
		s.handlers.videoStore = video.NewMemoryStoreWithOptions(opts)
	} else {
		s.handlers.videoStore = video.NewRedisStore(client, opts)
	}
	if err := secrets.ResolveVideoSecrets(s.cfg); err != nil {
		return fmt.Errorf("video secrets unavailable")
	}
	cfg = s.cfg.Video
	var blobs artifacts.Store
	var err error
	switch cfg.Artifacts.Backend {
	case "disk":
		blobs, err = artifacts.NewDisk(cfg.Artifacts.Disk.Root, cfg.Artifacts.MaxBytes)
	case "s3":
		v := cfg.Artifacts.S3
		blobs, err = artifacts.NewS3(artifacts.S3Config{Bucket: v.Bucket, Prefix: v.Prefix, Region: v.Region, AccessKey: v.AccessKey, SecretKey: v.SecretKey, SSE: v.SSE, MaxBytes: cfg.Artifacts.MaxBytes, Timeout: cfg.Copy.Deadline})
	default:
		return fmt.Errorf("video artifact backend invalid")
	}
	if err != nil {
		return fmt.Errorf("video artifact store unavailable")
	}
	adapters := provider.NewRegistry()
	host := s.cfg.Server.Host
	host = strings.Trim(host, "[]")
	svc, err := lifecycle.New(lifecycle.Options{Config: cfg, Store: s.handlers.videoStore, Redis: client, Budget: budget, Adapters: adapters, Artifacts: blobs, Engine: s.engine, DeniedHosts: []string{host, "localhost"}})
	if err != nil {
		return err
	}
	for name, p := range cfg.Providers {
		if !p.Enabled && p.APIKey == "" && p.CredentialRef == "" {
			continue
		}
		if name != "byteplus" {
			return fmt.Errorf("video adapter unavailable")
		}
		a, e := byteplus.New(byteplus.Config{Provider: p, CorrelationSecret: cfg.CorrelationSecret, FetchTimeout: cfg.Copy.Deadline, MaxDownloadBytes: cfg.Artifacts.MaxBytes, ListLimiter: svc.ListLimit})
		if e != nil {
			return fmt.Errorf("video adapter configuration invalid")
		}
		if e = adapters.Register(a); e != nil {
			return e
		}
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if client != nil {
		if err = svc.InitializeKillSwitch(ctx); err != nil {
			return err
		}
		// CONFIG may be denied by managed Redis. Only a confirmed unsafe policy
		// warns; startup does not require administrative Redis privileges.
		var policy map[string]string
		_ = client.Do(func(c redis.UniversalClient) error {
			var e error
			policy, e = c.ConfigGet(ctx, "maxmemory-policy").Result()
			return e
		})
		if p := policy["maxmemory-policy"]; p != "" && p != "noeviction" {
			slog.Warn("video Redis should use maxmemory-policy noeviction")
		}
	} else if cfg.Store != "memory" {
		return video.ErrStoreUnavailable
	}
	if s.cfg.OTel.Enabled {
		if s.cfg.OTel.Exporter == "otlp" {
			s.videoExporter, err = otel.NewOTLPExporter(otel.OTLPOptions{Endpoint: s.cfg.OTel.Endpoint, ServiceName: s.cfg.OTel.ServiceName, Resource: s.cfg.OTel.Attributes, Headers: s.cfg.OTel.Headers})
			if err != nil {
				return fmt.Errorf("video trace exporter unavailable")
			}
		} else {
			s.videoExporter = otel.NewStdoutExporter(os.Stdout)
		}
	}
	svc.SetTelemetry(s.metricsRegistry, s.videoExporter, s.cfg.OTel.ServiceName)
	s.handlers.videoService = svc
	s.handlers.videoSyncWait = cfg.Submit.SyncWait
	s.videoWorker = svc.NewWorker()
	return nil
}
