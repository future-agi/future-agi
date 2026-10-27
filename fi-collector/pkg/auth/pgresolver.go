package auth

import (
	"context"
	"errors"
	"fmt"
	"sync"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
)

// ResolveResult is the cached output of a successful key validation.
// The Projects map is protected by mu for concurrent access.
type ResolveResult struct {
	OrgID       string
	WorkspaceID string
	UserID      string
	KeyType     string // "system", "user", "mcp", "ingest"
	// SimulationAgent is the agent an "ingest" key names; its ID is empty for
	// every other key and for an ingest key made for no agent. It is set once
	// before the result is cached and never changed.
	SimulationAgent SimulationAgent
	// columnsMissing marks an ingest key read without the agent column, so
	// routing cannot know its agent.
	columnsMissing bool
	mu             sync.RWMutex
	Projects       map[string]string // project_name → project_id
	// projectTypes maps a project_id to its trace_type ("observe",
	// "experiment") for the projects whose type resolution learned. A
	// project missing here has an unknown type.
	projectTypes map[string]string
	// finding is why routing refused this key's agent. It is kept under mu
	// for as long as this result is cached, so a refused agent costs no
	// database statement per request; a new result starts without one.
	finding *routeFinding
}

// SimulationAgent is what key validation reads about the agent of a sandbox
// key. A key whose agent row is gone has Deleted set and no OrgID.
type SimulationAgent struct {
	ID      string
	OrgID   string
	Deleted bool
}

// GetProject returns the project ID for a given name, thread-safe.
func (r *ResolveResult) GetProject(name string) (string, bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	id, ok := r.Projects[name]
	return id, ok
}

// GetProjectInWorkspace refuses to read a project mapping through any scope
// other than the immutable workspace carried by this authentication result.
func (r *ResolveResult) GetProjectInWorkspace(workspaceID, name string) (string, bool) {
	if r == nil || workspaceID == "" || workspaceID != r.WorkspaceID {
		return "", false
	}
	return r.GetProject(name)
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

// SetProjectsInWorkspace merges only when the caller proves the same
// workspace scope as the authentication result.
func (r *ResolveResult) SetProjectsInWorkspace(workspaceID string, projects map[string]string) bool {
	if r == nil || workspaceID == "" || workspaceID != r.WorkspaceID {
		return false
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.Projects == nil {
		r.Projects = make(map[string]string, len(projects))
	}
	for name, id := range projects {
		r.Projects[name] = id
	}
	return true
}

// SetProjectTypesInWorkspace records trace_types by project id, under the
// same workspace-scope proof as SetProjectsInWorkspace. Empty types are
// skipped.
func (r *ResolveResult) SetProjectTypesInWorkspace(workspaceID string, types map[string]string) bool {
	if r == nil || workspaceID == "" || workspaceID != r.WorkspaceID {
		return false
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	for id, traceType := range types {
		if id == "" || traceType == "" {
			continue
		}
		if r.projectTypes == nil {
			r.projectTypes = make(map[string]string, len(types))
		}
		r.projectTypes[id] = traceType
	}
	return true
}

// ProjectTypeInWorkspace returns the recorded trace_type of projectID, or ""
// when it is unknown or the workspace is not this result's.
func (r *ResolveResult) ProjectTypeInWorkspace(workspaceID, projectID string) string {
	if r == nil || workspaceID == "" || workspaceID != r.WorkspaceID {
		return ""
	}
	r.mu.RLock()
	defer r.mu.RUnlock()
	return r.projectTypes[projectID]
}

// MissingProjectsInWorkspace prevents a cached name from one workspace from
// satisfying a lookup for another workspace.
func (r *ResolveResult) MissingProjectsInWorkspace(workspaceID string, names []string) ([]string, bool) {
	if r == nil || workspaceID == "" || workspaceID != r.WorkspaceID {
		return nil, false
	}
	return r.MissingProjects(names), true
}

// ProjectIDsInWorkspace snapshots the ids of the visible projects that were
// resolved through this authentication result. The server uses it as an
// in-memory sidecar proof before handing canonical rows to the optional
// property writer. A project under a reserved hidden name is left out.
func (r *ResolveResult) ProjectIDsInWorkspace(workspaceID string) (map[string]struct{}, bool) {
	return r.projectIDsInWorkspace(workspaceID, false)
}

// HiddenProjectIDsInWorkspace snapshots the ids of the projects resolved under
// a reserved hidden name, which never feed the catalog or the notifier.
func (r *ResolveResult) HiddenProjectIDsInWorkspace(workspaceID string) (map[string]struct{}, bool) {
	return r.projectIDsInWorkspace(workspaceID, true)
}

func (r *ResolveResult) projectIDsInWorkspace(workspaceID string, hidden bool) (map[string]struct{}, bool) {
	if r == nil || workspaceID == "" || workspaceID != r.WorkspaceID {
		return nil, false
	}
	r.mu.RLock()
	defer r.mu.RUnlock()
	result := make(map[string]struct{}, len(r.Projects))
	for name, id := range r.Projects {
		if id != "" && isReservedProjectName(name) == hidden {
			result[id] = struct{}{}
		}
	}
	return result, true
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

// DeleteProjectByID removes every name mapped to projectID, thread-safe.
// Matching by id (not name) stays correct across orgs and same-name recreates.
func (r *ResolveResult) DeleteProjectByID(projectID string) bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	removed := false
	for name, id := range r.Projects {
		if id == projectID {
			delete(r.Projects, name)
			removed = true
		}
	}
	delete(r.projectTypes, projectID)
	return removed
}

// PGResolver validates API keys and resolves projects directly against PG.
type PGResolver struct {
	read  *pgxpool.Pool
	write *pgxpool.Pool
	// columns picks the key statement; nil behaves as "not checked yet", which
	// reads keys without the agent column.
	columns *routingColumns
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

const validateKeyQuery = `
		SELECT k.organization_id, k.workspace_id, k.user_id, k.type
		FROM accounts_orgapikey k
		WHERE k.api_key = $1 AND k.secret_key = $2
		  AND k.enabled = true AND k.deleted = false
		LIMIT 1`

// validateKeyWithAgentQuery also reads the agent of a sandbox key. The join is
// a left join so that a key whose agent row was deleted still validates and is
// told apart from an invalid key.
const validateKeyWithAgentQuery = `
		SELECT k.organization_id, k.workspace_id, k.user_id, k.type,
		       k.simulation_agent_id, a.organization_id, a.deleted
		FROM accounts_orgapikey k
		LEFT JOIN simulate_agent_definition a
		  ON a.id = k.simulation_agent_id AND k.type = 'ingest'
		WHERE k.api_key = $1 AND k.secret_key = $2
		  AND k.enabled = true AND k.deleted = false
		LIMIT 1`

// ValidateKey checks an API key pair and returns the associated org context.
// Returns nil, nil if the key is not found (invalid credentials).
func (r *PGResolver) ValidateKey(ctx context.Context, apiKey, secretKey string) (*ResolveResult, error) {
	withAgent := r.columns.state() == columnsPresent
	res, err := r.validateKey(ctx, apiKey, secretKey, withAgent)
	if withAgent && isUndefinedColumn(err) {
		// A migration was rolled back since the last check: do not fail every
		// uncached key until the next one.
		r.columns.set(columnsMissing)
		return r.validateKey(ctx, apiKey, secretKey, false)
	}
	return res, err
}

func isUndefinedColumn(err error) bool {
	var pgErr *pgconn.PgError
	return errors.As(err, &pgErr) && pgErr.Code == "42703"
}

func (r *PGResolver) validateKey(ctx context.Context, apiKey, secretKey string, withAgent bool) (*ResolveResult, error) {
	var orgID, keyType string
	var workspaceID, userID *string
	var agentID, agentOrgID *string
	var agentDeleted *bool

	var err error
	if withAgent {
		err = r.read.QueryRow(ctx, validateKeyWithAgentQuery, apiKey, secretKey).Scan(
			&orgID, &workspaceID, &userID, &keyType, &agentID, &agentOrgID, &agentDeleted)
	} else {
		err = r.read.QueryRow(ctx, validateKeyQuery, apiKey, secretKey).Scan(&orgID, &workspaceID, &userID, &keyType)
	}
	if err != nil {
		if err == pgx.ErrNoRows {
			return nil, nil // invalid key
		}
		return nil, fmt.Errorf("validate key: %w", err)
	}

	effectiveWorkspace := ""
	if workspaceID != nil {
		effectiveWorkspace = *workspaceID
	}
	if effectiveWorkspace == "" {
		effectiveWorkspace, err = r.resolveDefaultWorkspace(ctx, orgID)
		if err != nil {
			return nil, fmt.Errorf("validate key workspace: %w", err)
		}
	}
	res := &ResolveResult{
		OrgID: orgID, WorkspaceID: effectiveWorkspace,
		KeyType: keyType, Projects: make(map[string]string),
	}
	if userID != nil {
		res.UserID = *userID
	}
	if keyType == keyTypeIngest {
		if !withAgent {
			res.columnsMissing = true
		} else if agentID != nil && *agentID != "" {
			res.SimulationAgent = SimulationAgent{ID: *agentID}
			if agentOrgID != nil {
				res.SimulationAgent.OrgID = *agentOrgID
			}
			// No agent row (hard-deleted) reads as deleted.
			res.SimulationAgent.Deleted = agentOrgID == nil || agentDeleted == nil || *agentDeleted
		}
	}
	return res, nil
}

const resolveProjectsQuery = `
		SELECT id, name, trace_type FROM tracer_project
		WHERE organization_id = $1 AND workspace_id = $2
		  AND name = ANY($3) AND deleted = false`

// ResolvedProject is the project a name resolved to and its trace_type
// ("observe", "experiment").
type ResolvedProject struct {
	ID        string
	TraceType string
}

// ResolveProjects batch-resolves project names to IDs for one exact
// organization/workspace. A workspace-less lookup is always refused.
// Returns a map of name → id for projects that exist.
func (r *PGResolver) ResolveProjects(
	ctx context.Context, orgID, workspaceID string, names []string,
) (map[string]string, error) {
	typed, err := r.ResolveProjectsWithTypes(ctx, orgID, workspaceID, names)
	if err != nil || typed == nil {
		return nil, err
	}
	result := make(map[string]string, len(typed))
	for name, p := range typed {
		result[name] = p.ID
	}
	return result, nil
}

// ResolveProjectsWithTypes is ResolveProjects plus each project's
// trace_type, read in the same statement.
func (r *PGResolver) ResolveProjectsWithTypes(
	ctx context.Context, orgID, workspaceID string, names []string,
) (map[string]ResolvedProject, error) {
	if len(names) == 0 {
		return nil, nil
	}
	if orgID == "" || workspaceID == "" {
		return nil, fmt.Errorf("resolve projects requires organization and workspace scope")
	}

	rows, err := r.read.Query(ctx, resolveProjectsQuery, orgID, workspaceID, names)
	if err != nil {
		return nil, fmt.Errorf("resolve projects: %w", err)
	}
	defer rows.Close()

	result := make(map[string]ResolvedProject, len(names))
	for rows.Next() {
		var id, name, traceType string
		if err := rows.Scan(&id, &name, &traceType); err != nil {
			return nil, fmt.Errorf("scan project: %w", err)
		}
		result[name] = ResolvedProject{ID: id, TraceType: traceType}
	}
	return result, rows.Err()
}

func (r *PGResolver) resolveDefaultWorkspace(ctx context.Context, orgID string) (string, error) {
	if r == nil || r.read == nil || ctx == nil || orgID == "" {
		return "", fmt.Errorf("default workspace resolution requires a resolver context")
	}
	const query = `SELECT id FROM accounts_workspace
		WHERE organization_id = $1 AND is_default = true AND is_active = true AND deleted = false
		ORDER BY id LIMIT 2`
	rows, err := r.read.Query(ctx, query, orgID)
	if err != nil {
		return "", fmt.Errorf("resolve default workspace: %w", err)
	}
	defer rows.Close()
	ids := make([]string, 0, 2)
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			return "", fmt.Errorf("scan default workspace: %w", err)
		}
		ids = append(ids, id)
	}
	if err := rows.Err(); err != nil {
		return "", err
	}
	return requireExactlyOneDefaultWorkspace(orgID, ids)
}

func requireExactlyOneDefaultWorkspace(orgID string, ids []string) (string, error) {
	if orgID == "" || len(ids) != 1 || ids[0] == "" {
		return "", fmt.Errorf("org %s requires exactly one active default workspace", orgID)
	}
	return ids[0], nil
}

// GetOrCreateProject creates a project if it doesn't exist (via write pool)
// and returns its ID. Mirrors the Django get_or_create_project() in
// tracer/utils/otel.py — same defaults, same unique constraint.
func (r *PGResolver) GetOrCreateProject(ctx context.Context, orgID, workspaceID, name, traceType string) (string, error) {
	if traceType == "" {
		traceType = ProjectTypeObserve
	}

	// Legacy null-workspace callers resolve the one unambiguous active default
	// workspace. Zero or multiple defaults fail closed.
	if workspaceID == "" {
		var err error
		workspaceID, err = r.resolveDefaultWorkspace(ctx, orgID)
		if err != nil {
			return "", err
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
