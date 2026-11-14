package auth

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
)

const (
	// hiddenNamePrefix starts the name of the project an agent's sandbox
	// spans are stored in; the name is the prefix and the agent's id.
	hiddenNamePrefix = "fi-simulation-"
	// projectNameConstraint is the unique index on (name, trace type,
	// organization, workspace) among live projects.
	projectNameConstraint = "unique_project_per_org_type"
)

// defaultProjectConfig is the list-column layout the platform gives every
// project it creates (get_default_project_version_config in
// futureagi/tracer/utils/helper.py), so a project made here does not differ
// from one made there.
const defaultProjectConfig = `[` +
	`{"id":"run_name","name":"Run Name","is_visible":true,"group_by":null,"output_type":null,"reverse_output":null,"annotation_label_type":null,"choices":[null],"settings":null,"choices_map":null,"eval_template_id":null,"target_type":null,"annotators":null,"source_field":null,"parent_eval_id":null},` +
	`{"id":"avg_cost","name":"Avg. Cost","is_visible":true,"group_by":"System Metrics","output_type":null,"reverse_output":null,"annotation_label_type":null,"choices":[null],"settings":null,"choices_map":null,"eval_template_id":null,"target_type":null,"annotators":null,"source_field":null,"parent_eval_id":null},` +
	`{"id":"avg_latency","name":"Avg. Latency","is_visible":true,"group_by":"System Metrics","output_type":null,"reverse_output":null,"annotation_label_type":null,"choices":[null],"settings":null,"choices_map":null,"eval_template_id":null,"target_type":null,"annotators":null,"source_field":null,"parent_eval_id":null},` +
	`{"id":"rank","name":"Rank","is_visible":false,"group_by":null,"output_type":null,"reverse_output":null,"annotation_label_type":null,"choices":[null],"settings":null,"choices_map":null,"eval_template_id":null,"target_type":null,"annotators":null,"source_field":null,"parent_eval_id":null}` +
	`]`

var (
	// ErrSimulationAgentDeleted: the agent is deleted or gone, or belongs to
	// another organization, or its hidden project is deleted.
	ErrSimulationAgentDeleted = errors.New("simulation agent is deleted")
	// ErrSimulationNoWorkspace: the agent has no workspace and the
	// organization has no single active default one.
	ErrSimulationNoWorkspace = errors.New("simulation agent has no workspace")
	// ErrSimulationNameConflict: a project of the hidden name already exists
	// and is not the agent's.
	ErrSimulationNameConflict = errors.New("simulation project name is taken")
)

// SimulationProject is the hidden project of one agent.
type SimulationProject struct {
	ID             string
	OrganizationID string
	WorkspaceID    string
	Name           string
	// Created is true when this call inserted the row.
	Created bool
}

// simulationProjectResolver makes the hidden project of an agent.
type simulationProjectResolver interface {
	EnsureSimulationProject(ctx context.Context, orgID, agentID string) (SimulationProject, error)
}

// One statement, so that two collectors (or the platform) racing on a new
// agent end with one row, and so that a workspace that cannot be found is told
// apart from a deleted agent without a second read. The project is stored in
// the agent's workspace, else the organization's one active default
// workspace; with neither, no row is written, because a project without a
// workspace could never match any key's workspace.
//
// The outer join on tracer_project reads the row as it was before the
// statement. A project that already exists is answered from that row, with its
// own workspace, so a default workspace that changed or went away later cannot
// hide it or move it.
const ensureSimulationProjectQuery = `
	WITH ag AS (
		SELECT a.id, a.organization_id, a.deleted,
		       COALESCE(a.workspace_id, (
		           SELECT w.id FROM accounts_workspace w
		           WHERE w.organization_id = a.organization_id
		             AND w.is_default AND w.is_active AND NOT w.deleted
		       )) AS ws
		FROM simulate_agent_definition a
		WHERE a.id = $1 AND a.organization_id = $2
	), ins AS (
		INSERT INTO tracer_project (
			id, name, organization_id, workspace_id, trace_type, model_type, source,
			tags, metadata, config, session_config, user_id, deleted, created_at, updated_at,
			simulation_agent_id
		)
		SELECT $3::uuid, '` + hiddenNamePrefix + `' || ag.id::text, ag.organization_id, ag.ws,
		       'observe', 'GenerativeLLM', 'simulator',
		       '[]'::jsonb, '{}'::jsonb, '` + defaultProjectConfig + `'::jsonb, '[]'::jsonb,
		       NULL::uuid, false, now(), now(),
		       ag.id
		FROM ag
		WHERE NOT ag.deleted AND ag.ws IS NOT NULL
		ON CONFLICT (simulation_agent_id)
		DO UPDATE SET updated_at = EXCLUDED.updated_at
		WHERE tracer_project.deleted = false
		RETURNING id, name, workspace_id, organization_id, (xmax = 0) AS inserted
	)
	SELECT ag.deleted, ag.ws,
	       p.id, p.name, p.workspace_id, p.organization_id, p.deleted,
	       ins.id, ins.name, ins.workspace_id, ins.organization_id, ins.inserted
	FROM ag
	LEFT JOIN tracer_project p ON p.simulation_agent_id = ag.id
	LEFT JOIN ins ON true`

// EnsureSimulationProject returns the hidden project of an agent of orgID,
// creating it when there is none. It runs on the primary. The errors above
// are conditions that a retry cannot fix; any other error is transient.
func (r *PGResolver) EnsureSimulationProject(ctx context.Context, orgID, agentID string) (SimulationProject, error) {
	project, err := r.ensureSimulationProjectOnce(ctx, orgID, agentID)
	if errors.Is(err, ErrSimulationNameConflict) {
		// ON CONFLICT settles only a clash on the agent. A statement racing
		// another writer of the same agent's project can meet the name first
		// and fail although the winner's row is the agent's own. The winner
		// has committed by then, so a second statement reads its row; a name
		// held by any other project conflicts again.
		project, err = r.ensureSimulationProjectOnce(ctx, orgID, agentID)
	}
	return project, err
}

func (r *PGResolver) ensureSimulationProjectOnce(ctx context.Context, orgID, agentID string) (SimulationProject, error) {
	var agentDeleted bool
	var agentWorkspace *string
	var existingID, existingName, existingWorkspace, existingOrg *string
	var existingDeleted *bool
	var id, name, workspaceID, organizationID *string
	var inserted *bool
	err := r.write.QueryRow(ctx, ensureSimulationProjectQuery, agentID, orgID, uuid.NewString()).Scan(
		&agentDeleted, &agentWorkspace,
		&existingID, &existingName, &existingWorkspace, &existingOrg, &existingDeleted,
		&id, &name, &workspaceID, &organizationID, &inserted)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			// No agent row of this organization at all.
			return SimulationProject{}, ErrSimulationAgentDeleted
		}
		var pgErr *pgconn.PgError
		if errors.As(err, &pgErr) && pgErr.Code == "23505" && pgErr.ConstraintName == projectNameConstraint {
			return SimulationProject{}, ErrSimulationNameConflict
		}
		return SimulationProject{}, fmt.Errorf("ensure simulation project: %w", err)
	}
	switch {
	case agentDeleted:
		return SimulationProject{}, ErrSimulationAgentDeleted
	case existingID != nil:
		// The project that was there before this statement, as stored.
		if existingDeleted != nil && *existingDeleted {
			return SimulationProject{}, ErrSimulationAgentDeleted
		}
		return SimulationProject{
			ID: *existingID, Name: deref(existingName),
			WorkspaceID: deref(existingWorkspace), OrganizationID: deref(existingOrg),
		}, nil
	case agentWorkspace == nil:
		return SimulationProject{}, ErrSimulationNoWorkspace
	case id == nil:
		// The agent is live and no row came back: its hidden project was
		// deleted while this statement ran.
		return SimulationProject{}, ErrSimulationAgentDeleted
	}
	return SimulationProject{
		ID: *id, Name: deref(name),
		WorkspaceID: deref(workspaceID), OrganizationID: deref(organizationID),
		Created: inserted != nil && *inserted,
	}, nil
}

func deref(s *string) string {
	if s == nil {
		return ""
	}
	return *s
}

// isReservedProjectName reports whether name is exactly the hidden-project
// prefix followed by a canonical lowercase UUID. Only that shape is reserved;
// a name that merely starts with the prefix is nobody's.
func isReservedProjectName(name string) bool {
	if len(name) != len(hiddenNamePrefix)+36 || !strings.HasPrefix(name, hiddenNamePrefix) {
		return false
	}
	rest := name[len(hiddenNamePrefix):]
	parsed, err := uuid.Parse(rest)
	return err == nil && parsed.String() == rest
}

// withoutReservedNames returns names less every reserved one.
func withoutReservedNames(names []string) []string {
	kept := make([]string, 0, len(names))
	for _, name := range names {
		if !isReservedProjectName(name) {
			kept = append(kept, name)
		}
	}
	return kept
}
