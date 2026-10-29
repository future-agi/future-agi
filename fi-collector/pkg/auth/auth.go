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
	"sync/atomic"
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

// ProjectAuthChangedError means the authenticated cache entry disappeared or
// was replaced while project persistence was in flight. The attempt must fail
// closed, but retry with fresh authentication rather than permanently drop data.
type ProjectAuthChangedError struct{}

func (*ProjectAuthChangedError) Error() string {
	return "project resolution authentication cache missing or replaced; retry"
}
func (*ProjectAuthChangedError) Unwrap() error { return ErrProjectResolutionUnavailable }

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

	// Simulation routing. sim makes a sandbox key's hidden project; columns is
	// the last known state of the columns routing depends on, checked through
	// checker.
	sim     simulationProjectResolver
	columns *routingColumns
	checker columnChecker

	dropped [len(dropReasons)]atomic.Int64
	dropLog LogLimiter
	clock   func() time.Time // nil: time.Now
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

// typedProjectResolver is a projectResolver that also reads each project's
// trace_type (PGResolver does). With a resolver that does not, the types of
// looked-up projects stay unknown.
type typedProjectResolver interface {
	ResolveProjectsWithTypes(context.Context, string, string, []string) (map[string]ResolvedProject, error)
}

// ProjectTypeObserve is tracer_project.trace_type for an Observe project, the
// type the collector auto-creates projects with.
const ProjectTypeObserve = "observe"

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
	// Simulation routing needs the PostgreSQL resolver itself: it makes a
	// sandbox key's hidden project and checks the routing columns, whose
	// state also picks the key statement.
	a.sim = pg
	a.checker = pg
	a.columns = newRoutingColumns(log)
	pg.columns = a.columns
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

// InvalidateKey removes a hashed CacheKey after revocation or explicit
// invalidation. In-flight key and project lookups cannot repopulate this entry;
// the next request must authenticate again. It is also used by WatchRevocations.
func (a *Authenticator) InvalidateKey(cacheKey string) {
	if a != nil {
		a.cache.invalidateKey(cacheKey)
	}
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
				a.InvalidateKey(msg.Payload)
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
	// At most one revalidation, with the original caller deadline. Repeated
	// invalidation fails closed rather than retrying indefinitely.
	for attempt := 0; attempt < 2; attempt++ {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		entry, status := a.cache.get(ck)
		switch status {
		case "fresh":
			return entry.result, nil
		case "warm":
			go a.sfKey.Do(ck+":refresh", func() (any, error) {
				a.refreshKey(context.Background(), apiKey, secretKey)
				return nil, nil
			})
			return entry.result, nil
		}

		result, err := a.authenticateUncached(ctx, apiKey, secretKey, ck)
		var changed *ProjectAuthChangedError
		if errors.As(err, &changed) {
			continue
		}
		return result, err
	}
	return nil, &ProjectAuthChangedError{}
}

func (a *Authenticator) authenticateUncached(ctx context.Context, apiKey, secretKey, ck string) (*ResolveResult, error) {
	epoch := a.cache.keyGeneration(ck)
	// A request after invalidation must not join a lookup started before it.
	lookup := a.sfKey.DoChan(fmt.Sprintf("%s:%d", ck, epoch), func() (any, error) {
		sfCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 10*time.Second)
		defer cancel()
		result, err := a.keyResolver().ValidateKey(sfCtx, apiKey, secretKey)
		if err != nil {
			return nil, err
		}
		if !a.cache.publishValidated(ck, result, epoch) {
			return nil, &ProjectAuthChangedError{}
		}
		if result == nil {
			return nil, ErrUnauthenticated
		}
		// Publish once inside singleflight, not once per waiter: a late waiter
		// must not overwrite a more recent cache refresh.
		return result, nil
	})
	var resolved singleflight.Result
	select {
	case <-ctx.Done():
		return nil, ctx.Err()
	case resolved = <-lookup:
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if resolved.Err != nil {
		return nil, fmt.Errorf("auth resolve: %w", resolved.Err)
	}
	result, _ := resolved.Val.(*ResolveResult)
	entry, _ := a.cache.peek(ck)
	if entry == nil || entry.result != result {
		return nil, &ProjectAuthChangedError{}
	}
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
	if a.cache.projectEntry(cacheKey, result) == nil {
		return &ProjectAuthChangedError{}
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

	// A sandbox key's hidden name is put in its map by routing alone; looking
	// it up here could bind it to a visible project of the same name.
	if result.KeyType == keyTypeIngest {
		missing = withoutReservedNames(missing)
		if len(missing) == 0 {
			return nil
		}
	}

	// Batch resolve from PG read pool
	resolved, types, err := resolveProjectsTyped(ctx, resolver, result.OrgID, result.WorkspaceID, missing)
	if err != nil {
		return fmt.Errorf("%w: %w", ErrProjectResolutionUnavailable, err)
	}

	if !a.cache.addProjectsScoped(cacheKey, result, resolved) {
		return &ProjectAuthChangedError{}
	}
	a.recordProjectTypes(cacheKey, result, types)

	// Auto-create any still-missing projects via write pool
	for _, name := range missing {
		if _, ok := result.GetProjectInWorkspace(result.WorkspaceID, name); ok {
			continue
		}
		// A hidden-project name that no key's routing has put in the map is
		// never created here: this would make a visible twin of the hidden
		// project. The name stays unresolved and the stamp drops its spans.
		if isReservedProjectName(name) {
			continue
		}
		// Deduplicate only within this authenticated result. A fresh request
		// must not inherit an old in-flight create after same-scope replacement.
		sfKey := strings.Join(
			[]string{cacheKey, fmt.Sprintf("%p", result), result.OrgID, result.WorkspaceID, name, ProjectTypeObserve}, "\x00",
		)
		val, err, _ := a.sfProj.Do(sfKey, func() (any, error) {
			return resolver.GetOrCreateProject(ctx, result.OrgID, result.WorkspaceID, name, ProjectTypeObserve)
		})
		// A stale authentication result must not acknowledge a permanent drop
		// either. Retry under the current key scope before classifying the name.
		if a.cache.projectEntry(cacheKey, result) == nil {
			return &ProjectAuthChangedError{}
		}
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
		if !a.cache.addProjectsScoped(cacheKey, result, created) {
			return &ProjectAuthChangedError{}
		}
		// GetOrCreateProject returns the Observe project of that name, new
		// or existing.
		a.recordProjectTypes(cacheKey, result, map[string]string{id: ProjectTypeObserve})
	}

	return nil
}

// resolveProjectsTyped looks names up through resolver, reading their
// trace_types too when the resolver can. types maps project id → trace_type
// and is nil when the resolver cannot read them.
func resolveProjectsTyped(
	ctx context.Context, resolver projectResolver, orgID, workspaceID string, names []string,
) (ids, types map[string]string, err error) {
	typed, ok := resolver.(typedProjectResolver)
	if !ok {
		ids, err = resolver.ResolveProjects(ctx, orgID, workspaceID, names)
		return ids, nil, err
	}
	found, err := typed.ResolveProjectsWithTypes(ctx, orgID, workspaceID, names)
	if err != nil {
		return nil, nil, err
	}
	ids = make(map[string]string, len(found))
	types = make(map[string]string, len(found))
	for name, p := range found {
		ids[name] = p.ID
		types[p.ID] = p.TraceType
	}
	return ids, types, nil
}

// recordProjectTypes keeps the trace_types of resolved projects on the
// request's result and its cache entry. A type is a hint for the resource
// stamp (StampResourceAttrs), so failing to keep one only leaves that
// project's type unknown; it never fails the request.
func (a *Authenticator) recordProjectTypes(cacheKey string, result *ResolveResult, types map[string]string) {
	if len(types) == 0 {
		return
	}
	a.cache.addProjectTypesScoped(cacheKey, result, types)
}

// refreshKey re-validates a key in the background (warm stale refresh).
func (a *Authenticator) refreshKey(ctx context.Context, apiKey, secretKey string) {
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()

	ck := CacheKey(apiKey, secretKey)
	epoch := a.cache.keyGeneration(ck)

	result, err := a.keyResolver().ValidateKey(ctx, apiKey, secretKey)
	if err != nil {
		a.log.Debug("background key refresh failed", "err", err)
		return
	}
	if result == nil {
		a.cache.invalidateGeneration(ck, epoch)
		return
	}
	a.cache.publishValidated(ck, result, epoch)
}
