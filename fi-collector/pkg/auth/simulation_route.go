package auth

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"sort"
	"strings"
	"sync"
	"time"

	"go.opentelemetry.io/collector/pdata/ptrace"
)

const (
	keyTypeIngest = "ingest"
	// KeyTypeIngest is the type of a sandbox key.
	KeyTypeIngest = keyTypeIngest

	// resourceMarkKey is written on every routed resource so that downstream
	// conversion treats the spans as simulated; its value is resourceMarkValue.
	resourceMarkKey   = "fi.simulation.origin"
	resourceMarkValue = "simulation"

	// routeLookupTimeout bounds making the hidden project; past it the
	// request is refused as retryable, not dropped.
	routeLookupTimeout = 2 * time.Second

	// dropLogInterval is the least time between two log lines for one drop
	// reason and one agent (or organization, when there is no agent).
	dropLogInterval = time.Minute
)

// The closed set of reasons a sandbox-key request is dropped.
const (
	dropNoAgent        = "no_agent"
	dropTenantMismatch = "tenant_mismatch"
	dropAgentDeleted   = "agent_deleted"
	dropNameConflict   = "project_name_conflict"
	dropColumnMissing  = "column_missing"
)

var dropReasons = [...]string{dropNoAgent, dropTenantMismatch, dropAgentDeleted, dropNameConflict, dropColumnMissing}

func dropReasonIndex(reason string) int {
	for i, r := range dropReasons {
		if r == reason {
			return i
		}
	}
	return -1
}

// SimulationRouter sends the spans of a sandbox key to the hidden project of
// the key's agent. *Authenticator is the implementation; the server holds it
// by this interface.
type SimulationRouter interface {
	RouteSimulation(ctx context.Context, cacheKey string, traces ptrace.Traces, result *ResolveResult) (RouteOutcome, error)
}

var _ SimulationRouter = (*Authenticator)(nil)

// RouteOutcome is what routing did to one request.
type RouteOutcome struct {
	// Dropped is the number of spans removed from the request, by reason.
	Dropped map[string]int
	// Hidden is the id of the hidden project every remaining resource now
	// belongs to; empty when the request was not routed.
	Hidden string
}

// DroppedSpans is the total over all reasons.
func (o RouteOutcome) DroppedSpans() int {
	total := 0
	for _, n := range o.Dropped {
		total += n
	}
	return total
}

// Summary is "reason=count" per reason in a fixed order, for a response
// message.
func (o RouteOutcome) Summary() string {
	reasons := make([]string, 0, len(o.Dropped))
	for reason, n := range o.Dropped {
		if n > 0 {
			reasons = append(reasons, reason)
		}
	}
	sort.Strings(reasons)
	parts := make([]string, len(reasons))
	for i, reason := range reasons {
		parts[i] = fmt.Sprintf("%s=%d", reason, o.Dropped[reason])
	}
	return strings.Join(parts, ",")
}

// RoutingSnapshot is the routing counters since start.
type RoutingSnapshot struct {
	// Dropped counts dropped spans by reason; every reason is present.
	Dropped        map[string]int64
	ColumnsPresent bool
}

// Body is the snapshot as the health endpoint reports it.
func (s RoutingSnapshot) Body() map[string]any {
	body := make(map[string]any, len(s.Dropped)+1)
	for reason, n := range s.Dropped {
		body[reason] = n
	}
	body["columns_present"] = s.ColumnsPresent
	return body
}

// RoutingSnapshot returns the drop counters and the column state.
func (a *Authenticator) RoutingSnapshot() RoutingSnapshot {
	snap := RoutingSnapshot{Dropped: make(map[string]int64, len(dropReasons))}
	for i, reason := range dropReasons {
		snap.Dropped[reason] = 0
		if a != nil {
			snap.Dropped[reason] = a.dropped[i].Load()
		}
	}
	if a != nil {
		snap.ColumnsPresent = a.columns.state() == columnsPresent
	}
	return snap
}

// ErrRoutingNotReady is returned while the routing columns have not been
// checked, or while a cached key must be read again; a retry succeeds.
var ErrRoutingNotReady = errors.New("simulation routing is not ready")

// RouteSimulation stores every span of a sandbox-key request in the hidden
// project of the key's agent, whatever project name or mark the span carries,
// and touches no request of any other key. It reads only the key's cached
// result and Postgres, never a span attribute. A non-nil error means the
// hidden project could not be made and the caller must refuse the request as
// retryable; the traces are then unchanged. Otherwise a request that cannot be
// routed has all of its resources removed and counted in the outcome.
func (a *Authenticator) RouteSimulation(
	ctx context.Context, cacheKey string, traces ptrace.Traces, result *ResolveResult,
) (RouteOutcome, error) {
	var out RouteOutcome
	if a == nil || result == nil || result.KeyType != keyTypeIngest {
		return out, nil
	}
	rss := traces.ResourceSpans()
	if rss.Len() == 0 {
		return out, nil
	}

	switch a.columns.state() {
	case columnsUnknown:
		return out, ErrRoutingNotReady
	case columnsMissing:
		return a.dropAll(traces, result, "", dropColumnMissing, slog.LevelWarn), nil
	}
	if result.columnsMissing {
		// Read before the columns appeared: forget it and have the request
		// retried, so the next one reads the agent and nothing is lost.
		a.forget(cacheKey)
		return out, ErrRoutingNotReady
	}

	agent := result.SimulationAgent
	switch {
	case agent.ID == "":
		return a.dropAll(traces, result, "", dropNoAgent, slog.LevelWarn), nil
	case agent.Deleted:
		return a.dropAll(traces, result, agent.ID, dropAgentDeleted, slog.LevelWarn), nil
	case agent.OrgID != result.OrgID:
		return a.dropAll(traces, result, agent.ID, dropTenantMismatch, slog.LevelWarn), nil
	}

	// The key's project map answers once the project is known. A refusal of
	// the agent is kept on the key's result, so neither a known nor a refused
	// agent costs a statement per request until the key is read again. A
	// failure that a retry may fix is not kept.
	name := hiddenNamePrefix + agent.ID
	projectID, known := result.GetProjectInWorkspace(result.WorkspaceID, name)
	if !known {
		if f, ok := result.storedFinding(); ok {
			return a.dropAll(traces, result, agent.ID, f.reason, f.level), nil
		}
		if a.sim == nil {
			return out, errors.New("simulation routing requires a PostgreSQL project resolver")
		}
		lookupCtx, cancel := context.WithTimeout(ctx, routeLookupTimeout)
		project, err := a.sim.EnsureSimulationProject(lookupCtx, result.OrgID, agent.ID)
		cancel()
		refuse := func(reason string, level slog.Level) (RouteOutcome, error) {
			result.storeFinding(routeFinding{reason: reason, level: level})
			return a.dropAll(traces, result, agent.ID, reason, level), nil
		}
		switch {
		case errors.Is(err, ErrSimulationAgentDeleted):
			return refuse(dropAgentDeleted, slog.LevelWarn)
		case errors.Is(err, ErrSimulationNoWorkspace):
			return refuse(dropTenantMismatch, slog.LevelWarn)
		case errors.Is(err, ErrSimulationNameConflict):
			return refuse(dropNameConflict, slog.LevelError)
		case err != nil:
			return out, fmt.Errorf("make simulation project: %w", err)
		}
		// The project's own workspace and organization are compared, never a
		// recomputed default: an organization whose default workspace changed
		// later must not turn every span of a live key into a mismatch, and a
		// project of another tenant must never take a key's spans.
		if project.WorkspaceID != result.WorkspaceID || project.OrganizationID != result.OrgID {
			return refuse(dropTenantMismatch, slog.LevelWarn)
		}
		if project.Created {
			a.logger().Info("simulation project created",
				"event", "simulation_project_created",
				"agent_definition_id", agent.ID, "project_id", project.ID, "by", "collector")
		}
		name, projectID = project.Name, project.ID
		resolved := map[string]string{name: projectID}
		result.SetProjectsInWorkspace(result.WorkspaceID, resolved)
		// A false answer means the cache entry was replaced meanwhile; this
		// request's own result already holds the name, and the stamp reads it.
		a.cache.addProjectsScoped(cacheKey, result.OrgID, result.WorkspaceID, resolved)
	}

	for i := 0; i < rss.Len(); i++ {
		attrs := rss.At(i).Resource().Attributes()
		attrs.PutStr("project_name", name)
		if _, marked := attrs.Get(resourceMarkKey); !marked {
			attrs.PutStr(resourceMarkKey, resourceMarkValue)
		}
	}
	out.Hidden = projectID
	return out, nil
}

// forget drops a cached key result.
func (a *Authenticator) forget(cacheKey string) {
	if a.cache != nil && cacheKey != "" {
		a.cache.m.Delete(cacheKey)
	}
}

// dropAll removes every resource of the request and counts its spans.
func (a *Authenticator) dropAll(
	traces ptrace.Traces, result *ResolveResult, agentID, reason string, level slog.Level,
) RouteOutcome {
	var out RouteOutcome
	spans := traces.SpanCount()
	traces.ResourceSpans().RemoveIf(func(ptrace.ResourceSpans) bool { return true })
	if spans > 0 {
		out.Dropped = map[string]int{reason: spans}
		a.recordDrop(reason, result.OrgID, agentID, spans, level)
	}
	return out
}

// recordDrop counts dropped spans and logs at most once a minute for one
// reason and one agent; a request with no agent is limited per organization.
func (a *Authenticator) recordDrop(reason, orgID, agentID string, spans int, level slog.Level) {
	if i := dropReasonIndex(reason); i >= 0 {
		a.dropped[i].Add(int64(spans))
	}
	scope := agentID
	if scope == "" {
		scope = "org:" + orgID
	}
	now := time.Now()
	if a.clock != nil {
		now = a.clock()
	}
	if !a.dropLog.Allow(reason+"\x00"+scope, now) {
		return
	}

	var agent any
	if agentID != "" {
		agent = agentID
	}
	a.logger().Log(context.Background(), level, "simulation span dropped",
		"event", "simulation_span_dropped", "reason", reason,
		"organization_id", orgID, "agent_definition_id", agent, "spans", spans)
}

func (a *Authenticator) logger() *slog.Logger {
	if a != nil && a.log != nil {
		return a.log
	}
	return slog.Default()
}

// routeFinding is the refusal of one key's agent, kept for the life of the
// key's cached result.
type routeFinding struct {
	reason string
	level  slog.Level
}

func (r *ResolveResult) storedFinding() (routeFinding, bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	if r.finding == nil {
		return routeFinding{}, false
	}
	return *r.finding, true
}

func (r *ResolveResult) storeFinding(f routeFinding) {
	r.mu.Lock()
	r.finding = &f
	r.mu.Unlock()
}

// LogLimiter lets one log line through per key and minute. The zero value is
// ready to use.
type LogLimiter struct {
	mu sync.Mutex
	at map[string]time.Time
}

// Allow reports whether a line for key may be logged at now, and if so counts
// it.
func (l *LogLimiter) Allow(key string, now time.Time) bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	if last, seen := l.at[key]; seen && now.Sub(last) < dropLogInterval {
		return false
	}
	if l.at == nil {
		l.at = make(map[string]time.Time)
	}
	l.at[key] = now
	return true
}
