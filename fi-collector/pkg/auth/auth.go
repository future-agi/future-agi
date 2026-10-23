// Package auth provides API key authentication, project resolution,
// rate limiting, and usage metering for the fi-collector.
//
// All state is in-process (sync.Map cache + singleflight). PG is queried
// directly — no Django dependency.
package auth

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"strings"
	"time"

	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
	"golang.org/x/sync/singleflight"
)

const (
	revocationChannel        = "fi:auth:revoke"
	projectInvalidateChannel = "fi:project:invalidate"
)

var (
	ErrUnauthenticated = errors.New("invalid or missing API key")
	// ErrProjectResolutionUnavailable must be retried, not acknowledged as a drop.
	ErrProjectResolutionUnavailable = errors.New("project resolution temporarily unavailable")
)

// Authenticator is the top-level auth facade. Safe for concurrent use.
type Authenticator struct {
	cfg  Config
	pg   *PGResolver
	keys interface {
		ValidateKey(context.Context, string, string) (*ResolveResult, error)
	}
	projects projectResolver
	cache    *cache
	rdb      *redis.Client
	sfKey    singleflight.Group // dedup concurrent key lookups
	sfProj   singleflight.Group // dedup concurrent project lookups
	log      *slog.Logger
}

// Resolver provides key and project persistence. Authentication, caching,
// stamping and request middleware remain owned by Authenticator.
type Resolver interface {
	ValidateKey(context.Context, string, string) (*ResolveResult, error)
	projectResolver
}

type projectResolver interface {
	ResolveProjects(context.Context, string, string, []string) (map[string]string, error)
	GetOrCreateProject(context.Context, string, string, string, string) (string, error)
}

// New creates an Authenticator. If cfg.PGWrite is empty, returns nil
// (all interceptor/middleware checks become no-ops).
// rdb is optional — when non-nil, WatchRevocations can be called to enable
// instant cache eviction on key disable/delete.
func New(ctx context.Context, cfg Config, rdb *redis.Client, log *slog.Logger) (*Authenticator, error) {
	if !cfg.IsEnabled() {
		return nil, nil
	}
	cfg.defaults()

	pg, err := NewPGResolver(ctx, cfg)
	if err != nil {
		return nil, fmt.Errorf("auth pg resolver: %w", err)
	}

	a, err := NewWithResolver(cfg, pg, rdb, log)
	if err != nil {
		pg.Close()
		return nil, err
	}
	a.pg = pg
	return a, nil
}

// NewWithResolver constructs the real authentication pipeline with an injected
// persistence implementation. It owns no database pool; callers own resolver's
// lifetime. Tests can exercise cold/warm cache and middleware without a database.
func NewWithResolver(cfg Config, resolver Resolver, rdb *redis.Client, log *slog.Logger) (*Authenticator, error) {
	if resolver == nil {
		return nil, errors.New("auth requires a resolver")
	}
	cfg.defaults()
	if log == nil {
		log = slog.Default()
	}
	return &Authenticator{cfg: cfg, keys: resolver, projects: resolver,
		cache: newCache(cfg.CacheTTL, cfg.WarmTTL), rdb: rdb, log: log}, nil
}

// keyResolver keeps the in-package PG-backed constructors compatible.
func (a *Authenticator) keyResolver() interface {
	ValidateKey(context.Context, string, string) (*ResolveResult, error)
} {
	if a.keys != nil {
		return a.keys
	}
	return a.pg
}

// WatchRevocations evicts cache entries on two Redis channels: fi:auth:revoke
// (payload = cache key) drops the whole entry; fi:project:invalidate
// (payload = project_id) drops that mapping from every entry. Blocks until ctx
// is cancelled; no-op if Redis is unconfigured.
func (a *Authenticator) WatchRevocations(ctx context.Context) {
	if a == nil || a.rdb == nil {
		return
	}
	pubsub := a.rdb.Subscribe(ctx, revocationChannel, projectInvalidateChannel)
	defer pubsub.Close()

	for {
		select {
		case msg, ok := <-pubsub.Channel():
			if !ok {
				return
			}
			switch msg.Channel {
			case revocationChannel:
				a.cache.m.Delete(msg.Payload)
				a.log.Debug("revoked key evicted from cache", "cache_key", msg.Payload[:min(len(msg.Payload), 8)]+"…")
			case projectInvalidateChannel:
				if n := a.cache.evictProjectID(msg.Payload); n > 0 {
					a.log.Debug("deleted project evicted from cache",
						"project_id", msg.Payload, "entries", n)
				}
			}
		case <-ctx.Done():
			return
		}
	}
}

// PGRead returns the read connection pool for direct queries (e.g. metering).
func (a *Authenticator) PGRead() *pgxpool.Pool {
	if a == nil || a.pg == nil {
		return nil
	}
	return a.pg.ReadPool()
}

// Close releases PG pools.
func (a *Authenticator) Close() {
	if a != nil && a.pg != nil {
		a.pg.Close()
	}
}

// Authenticate validates an API key pair and returns the resolve result.
// On cache hit, returns immediately. On miss, queries PG (deduplicated
// by singleflight). Returns ErrUnauthenticated for invalid keys.
func (a *Authenticator) Authenticate(ctx context.Context, apiKey, secretKey string) (*ResolveResult, error) {
	if a == nil {
		return nil, nil // auth disabled
	}

	ck := CacheKey(apiKey, secretKey)

	entry, status := a.cache.get(ck)
	switch status {
	case "fresh":
		return entry.result, nil

	case "warm":
		// Singleflight dedup — 10K concurrent warm hits = 1 PG query, not 10K goroutines
		go a.sfKey.Do(ck+":refresh", func() (any, error) {
			a.refreshKey(context.Background(), apiKey, secretKey)
			return nil, nil
		})
		return entry.result, nil
	}

	// cache miss — resolve from PG
	val, err, _ := a.sfKey.Do(ck, func() (any, error) {
		sfCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 10*time.Second)
		defer cancel()
		return a.keyResolver().ValidateKey(sfCtx, apiKey, secretKey)
	})
	if err != nil {
		return nil, fmt.Errorf("auth resolve: %w", err)
	}

	result, _ := val.(*ResolveResult)
	if result == nil {
		// Don't cache invalid keys — avoids unbounded memory from scanners.
		// Trade-off: invalid keys always hit PG, but singleflight deduplicates.
		return nil, ErrUnauthenticated
	}

	a.cache.putPositive(ck, result)
	return result, nil
}

// ResolveProjectsForKey resolves project names for an already-authenticated key.
// cacheKey is the hashed key from CacheKey(). Uses the cached project map
// first, queries PG for unknown names, and auto-creates projects that don't exist.
func (a *Authenticator) ResolveProjectsForKey(ctx context.Context, cacheKey string, result *ResolveResult, names []string) error {
	if a == nil || result == nil {
		return nil
	}

	for _, name := range names {
		if err := validateProjectName(name, "project_name"); err != nil {
			return err
		}
	}
	if result.OrgID == "" || result.WorkspaceID == "" {
		return errors.New("resolve projects requires authenticated organization and workspace scope")
	}
	missing, scoped := result.MissingProjectsInWorkspace(result.WorkspaceID, names)
	if !scoped {
		return errors.New("resolve projects workspace scope does not match authentication result")
	}
	if len(missing) == 0 {
		return nil
	}
	resolver := a.projects
	if resolver == nil {
		resolver = a.pg
	}
	if resolver == nil {
		return errors.New("resolve projects requires a PostgreSQL project resolver")
	}

	// Batch resolve from PG read pool
	resolved, err := resolver.ResolveProjects(ctx, result.OrgID, result.WorkspaceID, missing)
	if err != nil {
		return fmt.Errorf("%w: %w", ErrProjectResolutionUnavailable, err)
	}

	if !result.SetProjectsInWorkspace(result.WorkspaceID, resolved) ||
		!a.cache.addProjectsScoped(cacheKey, result.OrgID, result.WorkspaceID, resolved) {
		return errors.New("resolve projects authentication cache scope changed during lookup")
	}

	// Auto-create any still-missing projects via write pool
	for _, name := range missing {
		if _, ok := result.GetProjectInWorkspace(result.WorkspaceID, name); ok {
			continue
		}
		sfKey := strings.Join(
			[]string{cacheKey, result.OrgID, result.WorkspaceID, name, "observe"}, "\x00",
		)
		val, err, _ := a.sfProj.Do(sfKey, func() (any, error) {
			return resolver.GetOrCreateProject(ctx, result.OrgID, result.WorkspaceID, name, "observe")
		})
		if err != nil {
			a.log.Warn("project auto-create failed", "name", name, "org", result.OrgID, "err", err)
			// Only deterministic value/check failures are negatively cached.
			// Unique/FK conflicts, context cancellation and unknown errors may
			// recover; do not cache or acknowledge any of their spans.
			var pgErr *pgconn.PgError
			if errors.As(err, &pgErr) && (pgErr.Code == "22001" || pgErr.Code == "23514") {
				result.markFailed(name, time.Now())
				continue
			}
			return fmt.Errorf("%w: %w", ErrProjectResolutionUnavailable, err)
		}
		id := val.(string)
		created := map[string]string{name: id}
		if !result.SetProjectsInWorkspace(result.WorkspaceID, created) ||
			!a.cache.addProjectsScoped(cacheKey, result.OrgID, result.WorkspaceID, created) {
			return errors.New("created project cache scope changed during lookup")
		}
	}

	return nil
}

// refreshKey re-validates a key in the background (warm stale refresh).
func (a *Authenticator) refreshKey(ctx context.Context, apiKey, secretKey string) {
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()

	ck := CacheKey(apiKey, secretKey)

	result, err := a.keyResolver().ValidateKey(ctx, apiKey, secretKey)
	if err != nil {
		a.log.Debug("background key refresh failed", "err", err)
		return
	}
	if result == nil {
		// Key was disabled since last cache — evict it
		a.cache.m.Delete(ck)
		return
	}
	a.cache.putPositive(ck, result)
}
