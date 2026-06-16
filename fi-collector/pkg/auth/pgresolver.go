package auth

import (
	"context"
	"fmt"
	"sync"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// ResolveResult is the cached output of a successful key validation.
// The Projects map is protected by mu for concurrent access.
type ResolveResult struct {
	OrgID       string
	WorkspaceID string
	UserID      string
	KeyType     string // "system", "user", "mcp"
	mu          sync.RWMutex
	Projects    map[string]string // project_name → project_id
}

// GetProject returns the project ID for a given name, thread-safe.
func (r *ResolveResult) GetProject(name string) (string, bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	id, ok := r.Projects[name]
	return id, ok
}

// SetProject sets a single project mapping, thread-safe.
func (r *ResolveResult) SetProject(name, id string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.Projects[name] = id
}

// SetProjects merges multiple project mappings, thread-safe.
func (r *ResolveResult) SetProjects(projects map[string]string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	for name, id := range projects {
		r.Projects[name] = id
	}
}

// MissingProjects returns project names not yet in the map, thread-safe.
func (r *ResolveResult) MissingProjects(names []string) []string {
	r.mu.RLock()
	defer r.mu.RUnlock()
	var missing []string
	for _, name := range names {
		if _, ok := r.Projects[name]; !ok {
			missing = append(missing, name)
		}
	}
	return missing
}

// PGResolver validates API keys and resolves projects directly against PG.
type PGResolver struct {
	read  *pgxpool.Pool
	write *pgxpool.Pool
}

// NewPGResolver opens read and write connection pools.
func NewPGResolver(ctx context.Context, cfg Config) (*PGResolver, error) {
	cfg.defaults()

	readCfg, err := pgxpool.ParseConfig(cfg.PGRead)
	if err != nil {
		return nil, fmt.Errorf("parse pg_read: %w", err)
	}
	readCfg.MaxConns = int32(cfg.PGPoolRead)

	readPool, err := pgxpool.NewWithConfig(ctx, readCfg)
	if err != nil {
		return nil, fmt.Errorf("connect pg_read: %w", err)
	}

	var writePool *pgxpool.Pool
	if cfg.PGWrite == cfg.PGRead {
		writePool = readPool
	} else {
		writeCfg, err := pgxpool.ParseConfig(cfg.PGWrite)
		if err != nil {
			readPool.Close()
			return nil, fmt.Errorf("parse pg_write: %w", err)
		}
		writeCfg.MaxConns = int32(cfg.PGPoolWrite)
		writePool, err = pgxpool.NewWithConfig(ctx, writeCfg)
		if err != nil {
			readPool.Close()
			return nil, fmt.Errorf("connect pg_write: %w", err)
		}
	}

	return &PGResolver{read: readPool, write: writePool}, nil
}

// ReadPool returns the read connection pool.
func (r *PGResolver) ReadPool() *pgxpool.Pool { return r.read }

// Close releases both connection pools.
func (r *PGResolver) Close() {
	if r.write != r.read {
		r.write.Close()
	}
	r.read.Close()
}

// ValidateKey checks an API key pair and returns the associated org context.
// Returns nil, nil if the key is not found (invalid credentials).
func (r *PGResolver) ValidateKey(ctx context.Context, apiKey, secretKey string) (*ResolveResult, error) {
	const q = `
		SELECT k.organization_id, k.workspace_id, k.user_id, k.type
		FROM accounts_orgapikey k
		WHERE k.api_key = $1 AND k.secret_key = $2
		  AND k.enabled = true AND k.deleted = false
		LIMIT 1`

	var orgID, keyType string
	var workspaceID, userID *string

	err := r.read.QueryRow(ctx, q, apiKey, secretKey).Scan(&orgID, &workspaceID, &userID, &keyType)
	if err != nil {
		if err == pgx.ErrNoRows {
			return nil, nil // invalid key
		}
		return nil, fmt.Errorf("validate key: %w", err)
	}

	res := &ResolveResult{
		OrgID:    orgID,
		KeyType:  keyType,
		Projects: make(map[string]string),
	}
	if workspaceID != nil {
		res.WorkspaceID = *workspaceID
	}
	if userID != nil {
		res.UserID = *userID
	}
	return res, nil
}

// ResolveProjects batch-resolves project names to IDs for an org.
// Returns a map of name → id for projects that exist.
func (r *PGResolver) ResolveProjects(ctx context.Context, orgID string, names []string) (map[string]string, error) {
	if len(names) == 0 {
		return nil, nil
	}

	const q = `
		SELECT id, name FROM tracer_project
		WHERE organization_id = $1 AND name = ANY($2) AND deleted = false`

	rows, err := r.read.Query(ctx, q, orgID, names)
	if err != nil {
		return nil, fmt.Errorf("resolve projects: %w", err)
	}
	defer rows.Close()

	result := make(map[string]string, len(names))
	for rows.Next() {
		var id, name string
		if err := rows.Scan(&id, &name); err != nil {
			return nil, fmt.Errorf("scan project: %w", err)
		}
		result[name] = id
	}
	return result, rows.Err()
}

// GetOrCreateProject creates a project if it doesn't exist (via write pool)
// and returns its ID. Mirrors the Django get_or_create_project() in
// tracer/utils/otel.py — same defaults, same unique constraint.
func (r *PGResolver) GetOrCreateProject(ctx context.Context, orgID, workspaceID, name, traceType string) (string, error) {
	if traceType == "" {
		traceType = "observe"
	}

	// If no workspace provided, resolve the org's default workspace (same as Django).
	if workspaceID == "" {
		const wsQ = `SELECT id FROM accounts_workspace
			WHERE organization_id = $1 AND is_default = true AND is_active = true AND deleted = false
			LIMIT 1`
		err := r.read.QueryRow(ctx, wsQ, orgID).Scan(&workspaceID)
		if err == pgx.ErrNoRows {
			return "", fmt.Errorf("org %s has no default workspace", orgID)
		}
		if err != nil {
			return "", fmt.Errorf("resolve default workspace: %w", err)
		}
	}

	newID := uuid.New().String()

	// DO UPDATE SET updated_at = now() (no-op touch) ensures RETURNING id fires
	// even on conflict, giving us the existing row's ID without a second query.
	// DO NOTHING would return no row, forcing a fallback SELECT that can't
	// scope by workspace_id + trace_type and may return the wrong project when
	// an org has two projects with the same name in different workspaces.
	const insertQ = `
		INSERT INTO tracer_project (
			id, name, organization_id, workspace_id, trace_type,
			model_type, source, tags, deleted, created_at, updated_at
		) VALUES ($1, $2, $3, $4, $5, 'GenerativeLLM', 'prototype', '[]', false, now(), now())
		ON CONFLICT (name, trace_type, organization_id, workspace_id) WHERE NOT deleted
		DO UPDATE SET updated_at = EXCLUDED.updated_at
		RETURNING id`

	var wsPtr *string
	if workspaceID != "" {
		wsPtr = &workspaceID
	}

	var returnedID string
	err := r.write.QueryRow(ctx, insertQ, newID, name, orgID, wsPtr, traceType).Scan(&returnedID)
	if err != nil {
		return "", fmt.Errorf("create project: %w", err)
	}
	return returnedID, nil
}
