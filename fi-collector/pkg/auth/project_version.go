package auth

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"go.opentelemetry.io/collector/pdata/pcommon"
)

// ErrProjectVersionIgnored leaves a resource unversioned: its requested version
// is invalid, deleted, or outside the authenticated project scope. It carries
// no request values, including when logged.
var ErrProjectVersionIgnored = errors.New("project version ignored")

type projectVersionResolver interface {
	GetOrCreateProjectVersion(context.Context, string, string, string, string, string) (string, error)
}

// stampProjectVersion accepts only a primary-backed resolution, never a
// producer's fi.project_version_id. The caller has already cleared that key.
func (a *Authenticator) stampProjectVersion(ctx context.Context, cacheKey string, result *ResolveResult, projectID string, attrs pcommon.Map) error {
	id := getStrAttr(attrs, "project_version_id")
	name := getStrAttr(attrs, "project_version_name")
	if a == nil || (id == "" && name == "") || result.ProjectTypeInWorkspace(result.WorkspaceID, projectID) == ProjectTypeObserve {
		return nil
	}
	if a.cache.projectEntry(cacheKey, result) == nil {
		return &ProjectAuthChangedError{}
	}
	resolver, ok := a.projects.(projectVersionResolver)
	if !ok && a.pg != nil {
		resolver = a.pg
	}
	if resolver == nil {
		return fmt.Errorf("%w: project version resolver unavailable", ErrProjectResolutionUnavailable)
	}
	resolved, err := resolver.GetOrCreateProjectVersion(ctx, result.OrgID, result.WorkspaceID, projectID, id, name)
	// Revocation/replacement while the database was in flight must not publish
	// a stamp (or acknowledge an ignored version) through the old auth result.
	currentProject, present := result.GetProjectInWorkspace(result.WorkspaceID, getStrAttr(attrs, "project_name"))
	if a.cache.projectEntry(cacheKey, result) == nil || !present || currentProject != projectID {
		return &ProjectAuthChangedError{}
	}
	if errors.Is(err, ErrProjectVersionIgnored) {
		a.log.Warn("project version ignored")
		return nil
	}
	if err != nil {
		return fmt.Errorf("%w: resolve project version: %w", ErrProjectResolutionUnavailable, err)
	}
	if resolved != "" {
		attrs.PutStr("fi.project_version_id", resolved)
	}
	return nil
}

// GetOrCreateProjectVersion mirrors the legacy ingestion writer using the
// existing PGResolver primary pool (as EnsureSimulationProject does). The
// parent row lock is shared with Django's select_for_update, serializing both
// UUID creation and name-only reuse across processes. Read committed is
// explicit: a waiter must see versions committed by the previous lock holder.
func (r *PGResolver) GetOrCreateProjectVersion(ctx context.Context, orgID, workspaceID, projectID, id, name string) (string, error) {
	if id == "" && name == "" {
		return "", nil
	}
	if id != "" {
		parsed, err := uuid.Parse(id)
		if err != nil || parsed == uuid.Nil {
			return "", ErrProjectVersionIgnored
		}
		id = parsed.String()
	}
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	tx, err := r.write.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.ReadCommitted})
	if err != nil {
		return "", err
	}
	defer tx.Rollback(ctx)

	var traceType string
	err = tx.QueryRow(ctx, `SELECT trace_type FROM tracer_project
 WHERE id=$1 AND organization_id=$2 AND workspace_id=$3 AND NOT deleted
 FOR UPDATE`, projectID, orgID, workspaceID).Scan(&traceType)
	if errors.Is(err, pgx.ErrNoRows) {
		return "", ErrProjectVersionIgnored
	}
	if err != nil {
		return "", err
	}
	// Prototype projects use trace_type experiment and source prototype.
	// Never trust the producer's project_type, nor version Observe projects.
	if traceType != "experiment" {
		return "", nil
	}
	if id != "" {
		existing, err := projectVersionByID(ctx, tx, projectID, id)
		if !errors.Is(err, pgx.ErrNoRows) {
			return existing, err
		}
	} else {
		// Deliberate deviation from legacy create-per-call: names are not
		// unique, so choose the most recently created live exact-name match.
		var existing string
		err := tx.QueryRow(ctx, `SELECT id::text FROM tracer_project_version
 WHERE project_id=$1 AND name=$2 AND NOT deleted
 ORDER BY created_at DESC, id DESC LIMIT 1`, projectID, name).Scan(&existing)
		if err == nil {
			return existing, nil
		}
		if !errors.Is(err, pgx.ErrNoRows) {
			return "", err
		}
		id = uuid.NewString()
	}
	if !utf8.ValidString(name) || utf8.RuneCountInString(name) > 255 || strings.ContainsRune(name, 0) {
		return "", ErrProjectVersionIgnored
	}

	// Match _get_project_version_number, including legacy custom prefixes.
	var previous string
	err = tx.QueryRow(ctx, `SELECT version FROM tracer_project_version
 WHERE project_id=$1 AND NOT deleted ORDER BY created_at DESC, id DESC LIMIT 1`, projectID).Scan(&previous)
	if err != nil && !errors.Is(err, pgx.ErrNoRows) {
		return "", err
	}
	version, err := nextProjectVersionNumber(previous)
	if err != nil {
		return "", err
	}
	if name == "" {
		name = "Version " + version
	}
	config, err := defaultVersionTraceConfig()
	if err != nil {
		return "", err
	}
	// eval_tags processing creates custom eval configs in Django. Do not copy
	// raw tags as though they were processed, or introduce a second eval writer.
	// Use wall-clock timestamps after acquiring the lock: transaction-start
	// now() can sort a late lock holder before the version it just incremented.
	var created string
	err = tx.QueryRow(ctx, `INSERT INTO tracer_project_version
 (id, project_id, name, version, start_time, metadata, eval_tags, config, created_at, updated_at, deleted)
 VALUES ($1, $2, $3, $4, clock_timestamp(), '{}'::jsonb, '[]'::jsonb, $5::jsonb, clock_timestamp(), clock_timestamp(), false)
 ON CONFLICT (id) DO NOTHING RETURNING id::text`, id, projectID, name, version, string(config)).Scan(&created)
	if errors.Is(err, pgx.ErrNoRows) {
		// Another project's transaction may have won the same UUID while we
		// held only our own parent lock. A new statement sees that winner;
		// never adopt it without checking its project and deletion state.
		return projectVersionByID(ctx, tx, projectID, id)
	}
	if err != nil {
		return "", err
	}
	if err := tx.Commit(ctx); err != nil {
		return "", err
	}
	return created, nil
}

func projectVersionByID(ctx context.Context, tx pgx.Tx, projectID, id string) (string, error) {
	var owner string
	var deleted bool
	// Look up the UUID globally to distinguish absence from an existing row
	// belonging to another project, without returning that row's values.
	err := tx.QueryRow(ctx, `SELECT project_id::text, deleted FROM tracer_project_version WHERE id=$1`, id).Scan(&owner, &deleted)
	if err != nil {
		return "", err
	}
	if owner != projectID || deleted {
		return "", ErrProjectVersionIgnored
	}
	return id, nil
}

// nextProjectVersionNumber follows the legacy helper's vN/custom_vN grammar.
func nextProjectVersionNumber(previous string) (string, error) {
	version := "v1"
	if previous != "" {
		if number, err := strconv.ParseInt(strings.ReplaceAll(previous, "v", ""), 10, 64); err == nil {
			version = fmt.Sprintf("v%d", number+1)
		} else if parts := strings.Split(previous, "_v"); len(parts) > 1 {
			number, err := strconv.ParseInt(parts[1], 10, 64)
			if err != nil {
				return "", ErrProjectVersionIgnored
			}
			version = fmt.Sprintf("%s_v%d", parts[0], number+1)
		} else {
			version = previous + "_v2"
		}
	}
	if len(version) > 10 { // ProjectVersion.version is varchar(10).
		return "", ErrProjectVersionIgnored
	}
	return version, nil
}

// defaultVersionTraceConfig mirrors get_default_trace_config, not the
// project-list get_default_project_version_config used by simulation_project.go.
// Both use the platform's FieldConfig shape; versions need trace-list columns.
func defaultVersionTraceConfig() ([]byte, error) {
	fields := []struct {
		id, name string
		visible  bool
	}{
		{"trace_name", "Trace Name", true},
		{"input", "Input", true},
		{"output", "Output", true},
		{"start_time", "Timestamp", true},
		{"status", "Status", true},
		{"latency", "Latency", true},
		{"total_tokens", "Tokens", true},
		{"cost", "Total Cost", true},
		{"model", "Model", true},
		{"tags", "Tags", true},
		{"user_id", "User Id", true},
		{"trace_id", "Trace Id", false},
		{"prompt_tokens", "Prompt Tokens", false},
		{"completion_tokens", "Completion Tokens", false},
		{"provider", "Provider", false},
		{"session_id", "Session Id", false},
	}
	config := make([]map[string]any, 0, len(fields))
	for _, field := range fields {
		canonical := field.id
		switch canonical {
		case "user_id":
			canonical = "user"
		case "session_id":
			canonical = "session"
		}
		config = append(config, map[string]any{
			"id": field.id, "name": field.name, "is_visible": field.visible,
			"group_by": nil, "output_type": nil, "reverse_output": nil,
			"annotation_label_type": nil, "choices": []any{nil}, "settings": nil,
			"choices_map": nil, "eval_template_id": nil, "target_type": nil,
			"annotators": nil, "source_field": nil, "parent_eval_id": nil,
			"property_id":   "system_attribute:traces:" + canonical,
			"property_kind": "system_attribute", "property_source": "traces",
		})
	}
	return json.Marshal(config)
}
