package auth

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

const (
	routeOrg       = "11111111-1111-4111-8111-111111111111"
	routeWorkspace = "22222222-2222-4222-8222-222222222222"
	routeAgent     = "33333333-3333-4333-8333-333333333333"
	routeHiddenID  = "44444444-4444-4444-8444-444444444444"
	routeProdID    = "55555555-5555-4555-8555-555555555555"
	routeHiddenKey = hiddenNamePrefix + routeAgent
)

// routeSim answers EnsureSimulationProject from a script and records calls.
type routeSim struct {
	mu        sync.Mutex
	project   SimulationProject
	err       error
	calls     int
	orgs      []string
	agents    []string
	deadlines []time.Duration
}

func (s *routeSim) EnsureSimulationProject(ctx context.Context, orgID, agentID string) (SimulationProject, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.calls++
	s.orgs = append(s.orgs, orgID)
	s.agents = append(s.agents, agentID)
	if d, ok := ctx.Deadline(); ok {
		s.deadlines = append(s.deadlines, time.Until(d))
	}
	if s.err != nil {
		return SimulationProject{}, s.err
	}
	return s.project, nil
}

func (s *routeSim) callCount() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.calls
}

func hiddenProject() SimulationProject {
	return SimulationProject{
		ID: routeHiddenID, OrganizationID: routeOrg, WorkspaceID: routeWorkspace, Name: routeHiddenKey, Created: true,
	}
}

// newRouter is an Authenticator that routes with sim, with the columns present.
func newRouter(t *testing.T, sim *routeSim) (*Authenticator, *bytes.Buffer) {
	t.Helper()
	var logs bytes.Buffer
	log := slog.New(slog.NewJSONHandler(&logs, nil))
	a := &Authenticator{cache: newCache(5*time.Minute, time.Hour), log: log}
	if sim != nil {
		a.sim = sim
	}
	a.columns = newRoutingColumns(log)
	a.columns.set(columnsPresent)
	logs.Reset()
	return a, &logs
}

func sandboxResult() *ResolveResult {
	return &ResolveResult{
		OrgID: routeOrg, WorkspaceID: routeWorkspace, KeyType: keyTypeIngest,
		SimulationAgent: SimulationAgent{ID: routeAgent, OrgID: routeOrg},
		Projects:        map[string]string{},
	}
}

// routeTraces builds one resource per name, each with two spans of one trace.
func routeTraces(names ...string) ptrace.Traces {
	traces := ptrace.NewTraces()
	for i, name := range names {
		rs := traces.ResourceSpans().AppendEmpty()
		if name != "" {
			rs.Resource().Attributes().PutStr("project_name", name)
		}
		spans := rs.ScopeSpans().AppendEmpty().Spans()
		for j := 0; j < 2; j++ {
			sp := spans.AppendEmpty()
			sp.SetName("span")
			sp.SetTraceID(pcommon.TraceID([16]byte{0xaa, byte(i)}))
			sp.SetSpanID(pcommon.SpanID([8]byte{byte(i), byte(j) + 1}))
			if i == 0 {
				sp.Attributes().PutStr("fi.simulator.call_execution_id", "call-1")
			}
		}
	}
	return traces
}

func resourceStr(t *testing.T, traces ptrace.Traces, i int, key string) (string, bool) {
	t.Helper()
	v, ok := traces.ResourceSpans().At(i).Resource().Attributes().Get(key)
	if !ok {
		return "", false
	}
	return v.Str(), true
}

func TestSandboxKeyRoutesEverySpanToTheKeysAgent(t *testing.T) {
	sim := &routeSim{project: hiddenProject()}
	a, _ := newRouter(t, sim)
	result := sandboxResult()
	a.cache.putPositive("ck", result)

	// A marked span, an unmarked span of another name, a resource with no name,
	// and a resource that already names the hidden project; one carries the
	// resource key already, with a value of its own.
	traces := routeTraces("customer-prod", "other-name", "", routeHiddenKey)
	traces.ResourceSpans().At(1).Resource().Attributes().PutStr(resourceMarkKey, "custom")
	before := traces.SpanCount()

	out, err := a.RouteSimulation(context.Background(), "ck", traces, result)
	if err != nil {
		t.Fatal(err)
	}
	if out.Hidden != routeHiddenID || out.DroppedSpans() != 0 {
		t.Fatalf("outcome = %+v", out)
	}
	if traces.SpanCount() != before {
		t.Fatalf("spans = %d, want %d", traces.SpanCount(), before)
	}
	for i := 0; i < traces.ResourceSpans().Len(); i++ {
		if got, _ := resourceStr(t, traces, i, "project_name"); got != routeHiddenKey {
			t.Errorf("resource %d project_name = %q", i, got)
		}
		want := resourceMarkValue
		if i == 1 {
			want = "custom"
		}
		if got, ok := resourceStr(t, traces, i, resourceMarkKey); !ok || got != want {
			t.Errorf("resource %d %s = %q, %v; want %q", i, resourceMarkKey, got, ok, want)
		}
	}
	// No span or other attribute was touched.
	spans := traces.ResourceSpans().At(0).ScopeSpans().At(0).Spans()
	if v, ok := spans.At(0).Attributes().Get("fi.simulator.call_execution_id"); !ok || v.Str() != "call-1" {
		t.Error("span attribute changed")
	}
	if id, ok := result.GetProject(routeHiddenKey); !ok || id != routeHiddenID {
		t.Errorf("key map = %q, %v", id, ok)
	}
	if sim.callCount() != 1 || sim.orgs[0] != routeOrg || sim.agents[0] != routeAgent {
		t.Fatalf("project made %d times for %v/%v", sim.callCount(), sim.orgs, sim.agents)
	}

	// The project is known now: the next request costs no statement.
	next := routeTraces("anything")
	if out, err := a.RouteSimulation(context.Background(), "ck", next, result); err != nil || out.Hidden != routeHiddenID {
		t.Fatalf("second request: %+v %v", out, err)
	}
	if sim.callCount() != 1 {
		t.Fatalf("project made again: %d calls", sim.callCount())
	}
}

func TestAnyOtherKeyIsNeverTouched(t *testing.T) {
	for _, keyType := range []string{"system", "user", "mcp"} {
		t.Run(keyType, func(t *testing.T) {
			sim := &routeSim{project: hiddenProject()}
			a, logs := newRouter(t, sim)
			// A column state that would drop a sandbox key must not matter.
			a.columns.set(columnsMissing)
			logs.Reset()
			result := &ResolveResult{
				OrgID: routeOrg, WorkspaceID: routeWorkspace, KeyType: keyType,
				SimulationAgent: SimulationAgent{ID: routeAgent, OrgID: routeOrg},
				Projects:        map[string]string{},
			}
			traces := routeTraces(routeHiddenKey, "customer-prod")
			traces.ResourceSpans().At(1).Resource().Attributes().PutStr(resourceMarkKey, "simulation")
			out, err := a.RouteSimulation(context.Background(), "ck", traces, result)
			if err != nil || out.Hidden != "" || out.DroppedSpans() != 0 {
				t.Fatalf("outcome = %+v, err = %v", out, err)
			}
			if got, _ := resourceStr(t, traces, 0, "project_name"); got != routeHiddenKey {
				t.Errorf("hidden name rewritten to %q", got)
			}
			if got, _ := resourceStr(t, traces, 1, "project_name"); got != "customer-prod" {
				t.Errorf("project_name rewritten to %q", got)
			}
			if _, ok := resourceStr(t, traces, 0, resourceMarkKey); ok {
				t.Error("resource key added to another key's request")
			}
			if sim.callCount() != 0 || len(result.Projects) != 0 || logs.Len() != 0 {
				t.Fatalf("calls=%d projects=%v logs=%s", sim.callCount(), result.Projects, logs.String())
			}
		})
	}
	var none *Authenticator
	traces := routeTraces("x")
	if out, err := none.RouteSimulation(context.Background(), "ck", traces, sandboxResult()); err != nil || out.Hidden != "" || traces.ResourceSpans().Len() != 1 {
		t.Fatalf("nil authenticator routed: %+v %v", out, err)
	}
}

func TestKeyWithoutAgentDropsEverything(t *testing.T) {
	sim := &routeSim{project: hiddenProject()}
	a, logs := newRouter(t, sim)
	result := sandboxResult()
	result.SimulationAgent = SimulationAgent{}
	traces := routeTraces("customer-prod", "other")

	out, err := a.RouteSimulation(context.Background(), "ck", traces, result)
	if err != nil {
		t.Fatal(err)
	}
	if out.Hidden != "" || out.Dropped[dropNoAgent] != 4 || out.DroppedSpans() != 4 {
		t.Fatalf("outcome = %+v", out)
	}
	if traces.ResourceSpans().Len() != 0 || sim.callCount() != 0 {
		t.Fatalf("resources left = %d, project made %d times", traces.ResourceSpans().Len(), sim.callCount())
	}
	snap := a.RoutingSnapshot()
	if snap.Dropped[dropNoAgent] != 4 || snap.Dropped[dropColumnMissing] != 0 || !snap.ColumnsPresent {
		t.Fatalf("snapshot = %+v", snap)
	}
	line := logs.String()
	for _, want := range []string{`"event":"simulation_span_dropped"`, `"reason":"no_agent"`, `"organization_id":"` + routeOrg + `"`, `"agent_definition_id":null`, `"spans":4`, `"level":"WARN"`} {
		if !strings.Contains(line, want) {
			t.Errorf("log lacks %s: %s", want, line)
		}
	}
	for _, leaked := range []string{"customer-prod", "other"} {
		if strings.Contains(line, leaked) {
			t.Errorf("log carries a project name: %s", line)
		}
	}
}

func TestDropLogIsLimitedToOnceAMinutePerReasonAndAgent(t *testing.T) {
	a, logs := newRouter(t, &routeSim{project: hiddenProject()})
	now := time.Date(2026, 10, 15, 12, 0, 0, 0, time.UTC)
	a.clock = func() time.Time { return now }
	lines := func() int { return strings.Count(logs.String(), "simulation_span_dropped") }

	drop := func(result *ResolveResult) {
		if _, err := a.RouteSimulation(context.Background(), "ck", routeTraces("x"), result); err != nil {
			t.Fatal(err)
		}
	}
	noAgent := sandboxResult()
	noAgent.SimulationAgent = SimulationAgent{}
	drop(noAgent)
	drop(noAgent)
	if lines() != 1 {
		t.Fatalf("lines = %d, want 1 inside the minute", lines())
	}
	otherOrg := sandboxResult()
	otherOrg.SimulationAgent = SimulationAgent{}
	otherOrg.OrgID = "66666666-6666-4666-8666-666666666666"
	drop(otherOrg)
	if lines() != 2 {
		t.Fatalf("lines = %d, want one per organization when there is no agent", lines())
	}
	deleted := sandboxResult()
	deleted.SimulationAgent.Deleted = true
	drop(deleted)
	if lines() != 3 {
		t.Fatalf("lines = %d, want one per reason", lines())
	}
	now = now.Add(61 * time.Second)
	drop(noAgent)
	if lines() != 4 {
		t.Fatalf("lines = %d, want a new line after a minute", lines())
	}
	// Every drop is counted whether or not it is logged.
	if got := a.RoutingSnapshot().Dropped[dropNoAgent]; got != 8 {
		t.Fatalf("no_agent counted %d spans, want 8", got)
	}
}

func TestAgentOfAnotherWorkspaceOrOrganizationIsDropped(t *testing.T) {
	t.Run("agent of another organization", func(t *testing.T) {
		sim := &routeSim{project: hiddenProject()}
		a, _ := newRouter(t, sim)
		result := sandboxResult()
		result.SimulationAgent.OrgID = "66666666-6666-4666-8666-666666666666"
		traces := routeTraces("x")
		out, err := a.RouteSimulation(context.Background(), "ck", traces, result)
		if err != nil || out.Dropped[dropTenantMismatch] != 2 || traces.ResourceSpans().Len() != 0 {
			t.Fatalf("outcome = %+v, err = %v", out, err)
		}
		if sim.callCount() != 0 {
			t.Fatal("a project was made for an agent of another organization")
		}
	})
	t.Run("hidden project of another workspace", func(t *testing.T) {
		sim := &routeSim{project: SimulationProject{
			ID: routeHiddenID, Name: routeHiddenKey, OrganizationID: routeOrg,
			WorkspaceID: "77777777-7777-4777-8777-777777777777",
		}}
		a, _ := newRouter(t, sim)
		result := sandboxResult()
		a.cache.putPositive("ck", result)
		traces := routeTraces("x")
		out, err := a.RouteSimulation(context.Background(), "ck", traces, result)
		if err != nil || out.Dropped[dropTenantMismatch] != 2 || traces.ResourceSpans().Len() != 0 {
			t.Fatalf("outcome = %+v, err = %v", out, err)
		}
		if _, ok := result.GetProject(routeHiddenKey); ok {
			t.Fatal("a project of another workspace entered the key's map")
		}
	})
	t.Run("hidden project of another organization", func(t *testing.T) {
		sim := &routeSim{project: SimulationProject{
			ID: routeHiddenID, Name: routeHiddenKey, WorkspaceID: routeWorkspace,
			OrganizationID: "88888888-8888-4888-8888-888888888888",
		}}
		a, _ := newRouter(t, sim)
		result := sandboxResult()
		a.cache.putPositive("ck", result)
		traces := routeTraces("x")
		out, err := a.RouteSimulation(context.Background(), "ck", traces, result)
		if err != nil || out.Dropped[dropTenantMismatch] != 2 || traces.ResourceSpans().Len() != 0 {
			t.Fatalf("outcome = %+v, err = %v", out, err)
		}
		if _, ok := result.GetProject(routeHiddenKey); ok {
			t.Fatal("a project of another organization entered the key's map")
		}
	})
	t.Run("agent without a workspace to store in", func(t *testing.T) {
		sim := &routeSim{err: ErrSimulationNoWorkspace}
		a, logs := newRouter(t, sim)
		traces := routeTraces("x")
		out, err := a.RouteSimulation(context.Background(), "ck", traces, sandboxResult())
		if err != nil || out.Dropped[dropTenantMismatch] != 2 {
			t.Fatalf("outcome = %+v, err = %v", out, err)
		}
		if !strings.Contains(logs.String(), `"level":"WARN"`) || strings.Contains(logs.String(), `"level":"ERROR"`) {
			t.Fatalf("not logged at warning: %s", logs.String())
		}
	})
}

func TestDeletedAgentIsDropped(t *testing.T) {
	cases := map[string]struct {
		agent SimulationAgent
		err   error
		calls int
	}{
		"soft-deleted":                     {agent: SimulationAgent{ID: routeAgent, OrgID: routeOrg, Deleted: true}},
		"hard-deleted, key still names it": {agent: SimulationAgent{ID: routeAgent, Deleted: true}},
		"hidden project deleted":           {agent: SimulationAgent{ID: routeAgent, OrgID: routeOrg}, err: ErrSimulationAgentDeleted, calls: 1},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			sim := &routeSim{project: hiddenProject(), err: tc.err}
			a, _ := newRouter(t, sim)
			result := sandboxResult()
			result.SimulationAgent = tc.agent
			traces := routeTraces("x")
			out, err := a.RouteSimulation(context.Background(), "ck", traces, result)
			if err != nil {
				t.Fatalf("a deleted agent is dropped, not an error: %v", err)
			}
			if out.Dropped[dropAgentDeleted] != 2 || out.Hidden != "" || traces.ResourceSpans().Len() != 0 {
				t.Fatalf("outcome = %+v", out)
			}
			if sim.callCount() != tc.calls {
				t.Fatalf("project made %d times, want %d", sim.callCount(), tc.calls)
			}
		})
	}
}

func TestTenantCheckComparesTheHiddenProjectsWorkspace(t *testing.T) {
	// The organization's default workspace moved after the project was made.
	// The project's own workspace is still the key's, so the key still routes.
	sim := &routeSim{project: SimulationProject{
		ID: routeHiddenID, Name: routeHiddenKey, OrganizationID: routeOrg, WorkspaceID: routeWorkspace,
	}}
	a, _ := newRouter(t, sim)
	result := sandboxResult()
	a.cache.putPositive("ck", result)
	traces := routeTraces("x")
	out, err := a.RouteSimulation(context.Background(), "ck", traces, result)
	if err != nil || out.Hidden != routeHiddenID || out.DroppedSpans() != 0 {
		t.Fatalf("outcome = %+v, err = %v", out, err)
	}
	if sim.orgs[0] != routeOrg || len(sim.agents) != 1 {
		t.Fatalf("only the organization and the agent are passed on: %v %v", sim.orgs, sim.agents)
	}
}

func TestHiddenProjectInsertErrorRefusesTheRequestAndStoresNothing(t *testing.T) {
	boom := errors.New("connection reset")
	sim := &routeSim{err: boom}
	a, logs := newRouter(t, sim)
	result := sandboxResult()
	a.cache.putPositive("ck", result)
	traces := routeTraces("customer-prod")

	out, err := a.RouteSimulation(context.Background(), "ck", traces, result)
	if !errors.Is(err, boom) {
		t.Fatalf("err = %v", err)
	}
	if out.Hidden != "" || out.DroppedSpans() != 0 {
		t.Fatalf("outcome = %+v", out)
	}
	// The request is unchanged and nothing was learned.
	if traces.ResourceSpans().Len() != 1 || traces.SpanCount() != 2 {
		t.Fatal("the request was changed")
	}
	if got, _ := resourceStr(t, traces, 0, "project_name"); got != "customer-prod" {
		t.Fatalf("project_name = %q", got)
	}
	if _, ok := resourceStr(t, traces, 0, resourceMarkKey); ok {
		t.Fatal("resource key set on a refused request")
	}
	if len(result.Projects) != 0 {
		t.Fatalf("key map = %v", result.Projects)
	}
	snap := a.RoutingSnapshot()
	for reason, n := range snap.Dropped {
		if n != 0 {
			t.Errorf("%s counted %d on a refused request", reason, n)
		}
	}
	if logs.Len() != 0 {
		t.Errorf("a transient refusal logged as a drop: %s", logs.String())
	}
}

func TestHiddenProjectIsMadeWithinTheLookupTimeout(t *testing.T) {
	sim := &routeSim{project: hiddenProject()}
	a, _ := newRouter(t, sim)
	if _, err := a.RouteSimulation(context.Background(), "ck", routeTraces("x"), sandboxResult()); err != nil {
		t.Fatal(err)
	}
	if len(sim.deadlines) != 1 || sim.deadlines[0] <= 0 || sim.deadlines[0] > routeLookupTimeout {
		t.Fatalf("deadlines = %v, want one within %v", sim.deadlines, routeLookupTimeout)
	}
}

func TestNameConflictIsCountedAsAFindingNotRetried(t *testing.T) {
	sim := &routeSim{err: ErrSimulationNameConflict}
	a, logs := newRouter(t, sim)
	traces := routeTraces("x")
	out, err := a.RouteSimulation(context.Background(), "ck", traces, sandboxResult())
	if err != nil {
		t.Fatalf("a name conflict must not be a retryable error: %v", err)
	}
	if out.Dropped[dropNameConflict] != 2 || traces.ResourceSpans().Len() != 0 {
		t.Fatalf("outcome = %+v", out)
	}
	if sim.callCount() != 1 {
		t.Fatalf("the insert ran %d times in one request", sim.callCount())
	}
	line := logs.String()
	if !strings.Contains(line, `"level":"ERROR"`) || !strings.Contains(line, `"reason":"project_name_conflict"`) ||
		!strings.Contains(line, `"agent_definition_id":"`+routeAgent+`"`) {
		t.Fatalf("log = %s", line)
	}
	if a.RoutingSnapshot().Dropped[dropNameConflict] != 2 {
		t.Fatalf("snapshot = %+v", a.RoutingSnapshot())
	}
}

func TestRefusalOfTheAgentIsRememberedForTheLifeOfTheKeyResult(t *testing.T) {
	otherWorkspace := hiddenProject()
	otherWorkspace.WorkspaceID = "77777777-7777-4777-8777-777777777777"
	cases := map[string]struct {
		project SimulationProject
		err     error
		reason  string
	}{
		"name conflict":                {err: ErrSimulationNameConflict, reason: dropNameConflict},
		"hidden project deleted":       {err: ErrSimulationAgentDeleted, reason: dropAgentDeleted},
		"no workspace to store in":     {err: ErrSimulationNoWorkspace, reason: dropTenantMismatch},
		"project of another workspace": {project: otherWorkspace, reason: dropTenantMismatch},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			sim := &routeSim{project: tc.project, err: tc.err}
			a, _ := newRouter(t, sim)
			result := sandboxResult()
			a.cache.putPositive("ck", result)
			for i := 0; i < 2; i++ {
				traces := routeTraces("x")
				out, err := a.RouteSimulation(context.Background(), "ck", traces, result)
				if err != nil || out.Dropped[tc.reason] != 2 || traces.ResourceSpans().Len() != 0 {
					t.Fatalf("request %d: outcome = %+v, err = %v", i, out, err)
				}
			}
			if sim.callCount() != 1 {
				t.Fatalf("the statement ran %d times for two requests of one key result", sim.callCount())
			}
			if got := a.RoutingSnapshot().Dropped[tc.reason]; got != 4 {
				t.Fatalf("%s counted %d spans, want 4", tc.reason, got)
			}
			// The key is read again after a refresh, and so is the agent.
			if _, err := a.RouteSimulation(context.Background(), "ck", routeTraces("x"), sandboxResult()); err != nil {
				t.Fatal(err)
			}
			if sim.callCount() != 2 {
				t.Fatalf("a new key result made %d calls in all, want 2", sim.callCount())
			}
		})
	}

	t.Run("a failure that a retry may fix is not remembered", func(t *testing.T) {
		boom := errors.New("connection reset")
		sim := &routeSim{err: boom}
		a, _ := newRouter(t, sim)
		result := sandboxResult()
		for i := 0; i < 2; i++ {
			if _, err := a.RouteSimulation(context.Background(), "ck", routeTraces("x"), result); !errors.Is(err, boom) {
				t.Fatalf("request %d: err = %v", i, err)
			}
		}
		if sim.callCount() != 2 {
			t.Fatalf("the statement ran %d times for two refused requests, want 2", sim.callCount())
		}
		sim.mu.Lock()
		sim.err = nil
		sim.project = hiddenProject()
		sim.mu.Unlock()
		out, err := a.RouteSimulation(context.Background(), "ck", routeTraces("x"), result)
		if err != nil || out.Hidden != routeHiddenID {
			t.Fatalf("outcome = %+v, err = %v", out, err)
		}
	})
}

func TestProjectCreatedIsLoggedOnlyWhenARowWasInserted(t *testing.T) {
	sim := &routeSim{project: hiddenProject()}
	a, logs := newRouter(t, sim)
	if _, err := a.RouteSimulation(context.Background(), "ck", routeTraces("x"), sandboxResult()); err != nil {
		t.Fatal(err)
	}
	line := logs.String()
	for _, want := range []string{`"event":"simulation_project_created"`, `"agent_definition_id":"` + routeAgent + `"`, `"project_id":"` + routeHiddenID + `"`, `"by":"collector"`, `"level":"INFO"`} {
		if !strings.Contains(line, want) {
			t.Errorf("log lacks %s: %s", want, line)
		}
	}
	logs.Reset()
	sim.project.Created = false
	if _, err := a.RouteSimulation(context.Background(), "ck", routeTraces("x"), sandboxResult()); err != nil {
		t.Fatal(err)
	}
	if logs.Len() != 0 {
		t.Fatalf("logged for an existing row: %s", logs.String())
	}
}

// failingProjectResolver fails the test when stamping tries to look a name up
// or create it.
type failingProjectResolver struct{ t *testing.T }

func (r failingProjectResolver) ResolveProjects(context.Context, string, string, []string) (map[string]string, error) {
	r.t.Error("ResolveProjects called")
	return nil, nil
}

func (r failingProjectResolver) GetOrCreateProject(context.Context, string, string, string, string) (string, error) {
	r.t.Error("GetOrCreateProject called")
	return "", nil
}

func TestStampFindsTheRoutedNameInTheKeyMap(t *testing.T) {
	sim := &routeSim{project: hiddenProject()}
	a, _ := newRouter(t, sim)
	a.projects = failingProjectResolver{t}
	result := sandboxResult()
	a.cache.putPositive("ck", result)
	traces := routeTraces("customer-prod", "other")

	if _, err := a.RouteSimulation(context.Background(), "ck", traces, result); err != nil {
		t.Fatal(err)
	}
	dropped, err := StampResourceAttrs(context.Background(), a, "ck", traces, result)
	if err != nil || dropped != 0 {
		t.Fatalf("stamp dropped %d, err = %v", dropped, err)
	}
	for i := 0; i < traces.ResourceSpans().Len(); i++ {
		if got, _ := resourceStr(t, traces, i, "fi.project_id"); got != routeHiddenID {
			t.Errorf("resource %d fi.project_id = %q", i, got)
		}
		if got, _ := resourceStr(t, traces, i, "fi.org_id"); got != routeOrg {
			t.Errorf("resource %d fi.org_id = %q", i, got)
		}
	}
}

func TestStampFindsTheRoutedNameInTheRequestsOwnResult(t *testing.T) {
	cases := map[string]func(a *Authenticator, result *ResolveResult){
		// The key's cache entry is gone, as when it expires between the read
		// and the routing.
		"the key is no longer cached": func(*Authenticator, *ResolveResult) {},
		// The entry was replaced by a fresh read of the same key, so the
		// cache merge lands in a result this request does not hold.
		"the cache entry holds another result": func(a *Authenticator, _ *ResolveResult) {
			a.cache.putPositive("ck", sandboxResult())
		},
	}
	for name, arrange := range cases {
		t.Run(name, func(t *testing.T) {
			sim := &routeSim{project: hiddenProject()}
			a, _ := newRouter(t, sim)
			a.projects = failingProjectResolver{t}
			result := sandboxResult()
			arrange(a, result)
			traces := routeTraces("customer-prod", "other")
			before := traces.SpanCount()

			out, err := a.RouteSimulation(context.Background(), "ck", traces, result)
			if err != nil || out.Hidden != routeHiddenID {
				t.Fatalf("route: %+v, %v", out, err)
			}
			// The stamp reads the same result, so the spans stay with the
			// hidden project instead of being dropped or stored under a
			// project made from the reserved name.
			dropped, err := StampResourceAttrs(context.Background(), a, "ck", traces, result)
			if err != nil || dropped != 0 {
				t.Fatalf("stamp dropped %d, err = %v", dropped, err)
			}
			if traces.SpanCount() != before {
				t.Fatalf("spans = %d, want %d", traces.SpanCount(), before)
			}
			for i := 0; i < traces.ResourceSpans().Len(); i++ {
				if got, _ := resourceStr(t, traces, i, "fi.project_id"); got != routeHiddenID {
					t.Errorf("resource %d fi.project_id = %q", i, got)
				}
			}
			if id, ok := result.GetProjectInWorkspace(routeWorkspace, routeHiddenKey); !ok || id != routeHiddenID {
				t.Errorf("the request's result maps the hidden name to %q, %v", id, ok)
			}
		})
	}
}

// countingProjectResolver answers lookups from a map and counts creations.
type countingProjectResolver struct {
	found   map[string]string
	created []string
}

func (r *countingProjectResolver) ResolveProjects(_ context.Context, _, _ string, names []string) (map[string]string, error) {
	out := map[string]string{}
	for _, name := range names {
		if id, ok := r.found[name]; ok {
			out[name] = id
		}
	}
	return out, nil
}

func (r *countingProjectResolver) GetOrCreateProject(_ context.Context, _, _, name, _ string) (string, error) {
	r.created = append(r.created, name)
	return routeProdID, nil
}

func TestReservedNameIsNeverCreated(t *testing.T) {
	resolver := &countingProjectResolver{found: map[string]string{}}
	a := &Authenticator{cache: newCache(time.Minute, time.Hour), projects: resolver, log: slog.Default()}
	result := &ResolveResult{OrgID: routeOrg, WorkspaceID: routeWorkspace, KeyType: "system", Projects: map[string]string{}}
	a.cache.putPositive("ck", result)

	// A request of another key that names a hidden project the replica has not
	// shown yet, next to an ordinary name and a name that only starts with the
	// prefix.
	traces := routeTraces(routeHiddenKey, "customer-prod", hiddenNamePrefix+"not-an-agent-id")
	dropped, err := StampResourceAttrs(context.Background(), a, "ck", traces, result)
	if err != nil {
		t.Fatal(err)
	}
	if dropped != 1 {
		t.Fatalf("dropped %d resources, want the reserved name's one", dropped)
	}
	sort.Strings(resolver.created)
	want := []string{"customer-prod", hiddenNamePrefix + "not-an-agent-id"}
	if strings.Join(resolver.created, ",") != strings.Join(want, ",") {
		t.Fatalf("created %v, want %v", resolver.created, want)
	}
	for i := 0; i < traces.ResourceSpans().Len(); i++ {
		if got, _ := resourceStr(t, traces, i, "project_name"); got == routeHiddenKey {
			t.Fatal("the reserved name's resource was kept")
		}
	}
}

// namesRecorder records the names a batch lookup was asked for.
type namesRecorder struct {
	countingProjectResolver
	asked [][]string
}

func (r *namesRecorder) ResolveProjects(ctx context.Context, org, ws string, names []string) (map[string]string, error) {
	r.asked = append(r.asked, append([]string(nil), names...))
	return r.countingProjectResolver.ResolveProjects(ctx, org, ws, names)
}

func TestSandboxKeyNeverLooksUpAReservedNameInTheReplica(t *testing.T) {
	// A visible project of the hidden name exists. Only routing may put the
	// name in a sandbox key's map; a lookup would bind it to that project.
	found := map[string]string{routeHiddenKey: routeProdID, "customer-prod": routeProdID}

	t.Run("sandbox key", func(t *testing.T) {
		resolver := &namesRecorder{countingProjectResolver: countingProjectResolver{found: found}}
		a := &Authenticator{cache: newCache(time.Minute, time.Hour), projects: resolver, log: slog.Default()}
		result := sandboxResult()
		a.cache.putPositive("ck", result)
		if err := a.ResolveProjectsForKey(context.Background(), "ck", result, []string{routeHiddenKey, "customer-prod"}); err != nil {
			t.Fatal(err)
		}
		if len(resolver.asked) != 1 || strings.Join(resolver.asked[0], ",") != "customer-prod" {
			t.Fatalf("lookups = %v, want only the ordinary name", resolver.asked)
		}
		if _, ok := result.GetProject(routeHiddenKey); ok {
			t.Fatal("the reserved name was bound by a lookup")
		}

		only := sandboxResult()
		a.cache.putPositive("ck2", only)
		if err := a.ResolveProjectsForKey(context.Background(), "ck2", only, []string{routeHiddenKey}); err != nil {
			t.Fatal(err)
		}
		if len(resolver.asked) != 1 {
			t.Fatalf("a lookup of nothing was sent: %v", resolver.asked)
		}
	})

	t.Run("another key is unchanged", func(t *testing.T) {
		resolver := &namesRecorder{countingProjectResolver: countingProjectResolver{found: found}}
		a := &Authenticator{cache: newCache(time.Minute, time.Hour), projects: resolver, log: slog.Default()}
		result := &ResolveResult{OrgID: routeOrg, WorkspaceID: routeWorkspace, KeyType: "system", Projects: map[string]string{}}
		a.cache.putPositive("ck", result)
		if err := a.ResolveProjectsForKey(context.Background(), "ck", result, []string{routeHiddenKey, "customer-prod"}); err != nil {
			t.Fatal(err)
		}
		if len(resolver.asked) != 1 || len(resolver.asked[0]) != 2 {
			t.Fatalf("lookups = %v, want both names", resolver.asked)
		}
		if id, ok := result.GetProject(routeHiddenKey); !ok || id != routeProdID {
			t.Fatalf("reserved name = %q, %v", id, ok)
		}
	})
}

func TestIsReservedProjectName(t *testing.T) {
	// routeAgent has no hex letters, so upper-casing it would change nothing.
	lettered := "abcdef01-2345-4678-89ab-cdef01234567"
	upper := hiddenNamePrefix + strings.ToUpper(lettered)
	cases := map[string]bool{
		routeHiddenKey:                       true,
		hiddenNamePrefix + lettered:          true,
		hiddenNamePrefix + "not-an-agent-id": false,
		hiddenNamePrefix:                     false,
		upper:                                false,
		hiddenNamePrefix + strings.ReplaceAll(routeAgent, "-", ""): false,
		routeHiddenKey + "x":          false,
		" " + routeHiddenKey:          false,
		"FI-SIMULATION-" + routeAgent: false,
		routeAgent:                    false,
		"":                            false,
	}
	for name, want := range cases {
		if got := isReservedProjectName(name); got != want {
			t.Errorf("isReservedProjectName(%q) = %v, want %v", name, got, want)
		}
	}
}

func TestMissingColumnDropsSandboxKeyRequestsOnly(t *testing.T) {
	sim := &routeSim{project: hiddenProject()}
	a, logs := newRouter(t, sim)

	t.Run("missing", func(t *testing.T) {
		a.columns.set(columnsMissing)
		if !strings.Contains(logs.String(), `"event":"simulation_routing_disabled"`) ||
			!strings.Contains(logs.String(), `"reason":"column_missing"`) || !strings.Contains(logs.String(), `"level":"ERROR"`) {
			t.Fatalf("no disabled log: %s", logs.String())
		}
		traces := routeTraces("x")
		out, err := a.RouteSimulation(context.Background(), "ck", traces, sandboxResult())
		if err != nil || out.Dropped[dropColumnMissing] != 2 || traces.ResourceSpans().Len() != 0 {
			t.Fatalf("outcome = %+v, err = %v", out, err)
		}
		if a.RoutingSnapshot().ColumnsPresent {
			t.Fatal("snapshot says the columns are present")
		}
		// Every other key is served as before.
		other := routeTraces("customer-prod")
		system := &ResolveResult{OrgID: routeOrg, WorkspaceID: routeWorkspace, KeyType: "system", Projects: map[string]string{}}
		if out, err := a.RouteSimulation(context.Background(), "ck2", other, system); err != nil || out.DroppedSpans() != 0 || other.ResourceSpans().Len() != 1 {
			t.Fatalf("another key's request was touched: %+v %v", out, err)
		}
		if sim.callCount() != 0 {
			t.Fatal("a project was made while a column is missing")
		}
	})

	t.Run("a key read without the agent column is refused and forgotten", func(t *testing.T) {
		a.columns.set(columnsPresent)
		stale := sandboxResult()
		stale.SimulationAgent = SimulationAgent{}
		stale.columnsMissing = true
		a.cache.putPositive("stale", stale)
		before := a.RoutingSnapshot().Dropped[dropColumnMissing]
		traces := routeTraces("x")
		out, err := a.RouteSimulation(context.Background(), "stale", traces, stale)
		if !errors.Is(err, ErrRoutingNotReady) {
			t.Fatalf("err = %v, want a retryable refusal", err)
		}
		if out.DroppedSpans() != 0 || traces.ResourceSpans().Len() != 1 || traces.SpanCount() != 2 {
			t.Fatalf("the refused request was changed or dropped: %+v", out)
		}
		if got := a.RoutingSnapshot().Dropped[dropColumnMissing]; got != before {
			t.Fatalf("column_missing counted %d, was %d: a request that a retry stores was lost", got, before)
		}
		if _, status := a.cache.get("stale"); status != "miss" {
			t.Fatal("the stale key result stayed cached")
		}
	})

	t.Run("not checked yet", func(t *testing.T) {
		fresh, _ := newRouter(t, &routeSim{project: hiddenProject()})
		fresh.columns = newRoutingColumns(slog.Default())
		traces := routeTraces("x")
		_, err := fresh.RouteSimulation(context.Background(), "ck", traces, sandboxResult())
		if err == nil {
			t.Fatal("a request of a sandbox key was routed before any check")
		}
		if traces.ResourceSpans().Len() != 1 {
			t.Fatal("the refused request was changed")
		}
		other := routeTraces("customer-prod")
		system := &ResolveResult{OrgID: routeOrg, WorkspaceID: routeWorkspace, KeyType: "user", Projects: map[string]string{}}
		if _, err := fresh.RouteSimulation(context.Background(), "ck2", other, system); err != nil {
			t.Fatalf("another key's request was refused: %v", err)
		}
	})
}

type fakeChecker struct {
	present bool
	err     error
}

func (c *fakeChecker) RoutingColumnsPresent(context.Context) (bool, error) { return c.present, c.err }

func TestColumnCheckKeepsTheLastStateOnError(t *testing.T) {
	a, _ := newRouter(t, nil)
	a.columns = newRoutingColumns(slog.Default())
	checker := &fakeChecker{present: true}
	a.checker = checker

	a.CheckRoutingColumns(context.Background())
	if a.columns.state() != columnsPresent {
		t.Fatalf("state = %v after a check that found both", a.columns.state())
	}
	checker.err = errors.New("connection refused")
	a.CheckRoutingColumns(context.Background())
	if a.columns.state() != columnsPresent {
		t.Fatal("a failed check changed the state")
	}
	checker.err, checker.present = nil, false
	a.CheckRoutingColumns(context.Background())
	if a.columns.state() != columnsMissing {
		t.Fatalf("state = %v after a check that found one missing", a.columns.state())
	}

	// A first check that fails leaves the state unknown.
	b, _ := newRouter(t, nil)
	b.columns = newRoutingColumns(slog.Default())
	b.checker = &fakeChecker{err: errors.New("connection refused")}
	b.CheckRoutingColumns(context.Background())
	if b.columns.state() != columnsUnknown {
		t.Fatalf("state = %v after a failed first check", b.columns.state())
	}
}

// scriptedChecker fails its first check and succeeds afterwards.
type scriptedChecker struct {
	mu    sync.Mutex
	calls int
}

func (c *scriptedChecker) RoutingColumnsPresent(context.Context) (bool, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.calls++
	if c.calls == 1 {
		return false, errors.New("connection refused")
	}
	return true, nil
}

func (c *scriptedChecker) callCount() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.calls
}

func TestWatchRetriesQuicklyWhileTheColumnStateIsUnknown(t *testing.T) {
	previous := columnRetryInterval
	columnRetryInterval = 10 * time.Millisecond
	a, _ := newRouter(t, nil)
	a.columns = newRoutingColumns(slog.Default())
	checker := &scriptedChecker{}
	a.checker = checker

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		a.WatchRoutingColumns(ctx)
	}()
	defer func() {
		cancel()
		<-done
		columnRetryInterval = previous
	}()

	// The regular wait is a minute; only the short retry can get here in time.
	deadline := time.Now().Add(5 * time.Second)
	for a.columns.state() != columnsPresent {
		if time.Now().After(deadline) {
			t.Fatalf("state %v after %d checks: the failed first check was not retried", a.columns.state(), checker.callCount())
		}
		time.Sleep(5 * time.Millisecond)
	}
	if checker.callCount() < 2 {
		t.Fatalf("%d checks", checker.callCount())
	}
}

func TestUnmarkedSpanOfAnotherKeyKeepsItsOwnProject(t *testing.T) {
	// A sandbox request and a request of a system key carry spans of one trace.
	// Only the sandbox key's is moved; the other stays in the project its name
	// says, whatever the sandbox key routed.
	sim := &routeSim{project: hiddenProject()}
	a, _ := newRouter(t, sim)
	resolver := &countingProjectResolver{found: map[string]string{"customer-prod": routeProdID}}
	a.projects = resolver

	sandbox := sandboxResult()
	a.cache.putPositive("sandbox", sandbox)
	sandboxTraces := routeTraces("customer-prod")
	if out, err := a.RouteSimulation(context.Background(), "sandbox", sandboxTraces, sandbox); err != nil || out.Hidden != routeHiddenID {
		t.Fatalf("sandbox request: %+v %v", out, err)
	}

	system := &ResolveResult{OrgID: routeOrg, WorkspaceID: routeWorkspace, KeyType: "system", Projects: map[string]string{}}
	a.cache.putPositive("system", system)
	systemTraces := routeTraces("customer-prod")
	if out, err := a.RouteSimulation(context.Background(), "system", systemTraces, system); err != nil || out.Hidden != "" {
		t.Fatalf("system request: %+v %v", out, err)
	}
	if dropped, err := StampResourceAttrs(context.Background(), a, "system", systemTraces, system); err != nil || dropped != 0 {
		t.Fatalf("stamp: %d %v", dropped, err)
	}
	if got, _ := resourceStr(t, systemTraces, 0, "fi.project_id"); got != routeProdID {
		t.Fatalf("the unmarked span of another key is in project %q, want its own %q", got, routeProdID)
	}
	if sandboxTraces.ResourceSpans().At(0).ScopeSpans().At(0).Spans().At(0).TraceID() !=
		systemTraces.ResourceSpans().At(0).ScopeSpans().At(0).Spans().At(0).TraceID() {
		t.Fatal("the two requests are not spans of one trace")
	}
}

func TestRoutingSnapshotAndOutcomeReporting(t *testing.T) {
	a, _ := newRouter(t, nil)
	body := a.RoutingSnapshot().Body()
	for _, reason := range dropReasons {
		if n, ok := body[reason].(int64); !ok || n != 0 {
			t.Errorf("body[%s] = %v", reason, body[reason])
		}
	}
	if present, ok := body["columns_present"].(bool); !ok || !present {
		t.Errorf("columns_present = %v", body["columns_present"])
	}
	if _, err := json.Marshal(body); err != nil {
		t.Fatal(err)
	}
	var none *Authenticator
	snap := none.RoutingSnapshot()
	if snap.ColumnsPresent || len(snap.Dropped) != len(dropReasons) {
		t.Fatalf("nil snapshot = %+v", snap)
	}
	out := RouteOutcome{Dropped: map[string]int{dropNoAgent: 2, dropAgentDeleted: 3, dropNameConflict: 0}}
	if out.DroppedSpans() != 5 || out.Summary() != "agent_deleted=3,no_agent=2" {
		t.Fatalf("outcome = %d %q", out.DroppedSpans(), out.Summary())
	}
}

// expectedProjectConfig is the list-column layout of a new project as the
// platform builds it: one entry per FieldConfig, with the defaults of its
// dataclass. Written out here, not taken from the constant under test.
const expectedProjectConfig = `[
	{"id": "run_name", "name": "Run Name", "is_visible": true, "group_by": null, "output_type": null,
	 "reverse_output": null, "annotation_label_type": null, "choices": [null], "settings": null,
	 "choices_map": null, "eval_template_id": null, "target_type": null, "annotators": null, "source_field": null, "parent_eval_id": null},
	{"id": "avg_cost", "name": "Avg. Cost", "is_visible": true, "group_by": "System Metrics", "output_type": null,
	 "reverse_output": null, "annotation_label_type": null, "choices": [null], "settings": null,
	 "choices_map": null, "eval_template_id": null, "target_type": null, "annotators": null, "source_field": null, "parent_eval_id": null},
	{"id": "avg_latency", "name": "Avg. Latency", "is_visible": true, "group_by": "System Metrics", "output_type": null,
	 "reverse_output": null, "annotation_label_type": null, "choices": [null], "settings": null,
	 "choices_map": null, "eval_template_id": null, "target_type": null, "annotators": null, "source_field": null, "parent_eval_id": null},
	{"id": "rank", "name": "Rank", "is_visible": false, "group_by": null, "output_type": null,
	 "reverse_output": null, "annotation_label_type": null, "choices": [null], "settings": null,
	 "choices_map": null, "eval_template_id": null, "target_type": null, "annotators": null, "source_field": null, "parent_eval_id": null}
]`

// The inserted row must carry the same list-column layout as the platform's
// own project. This reads the platform source when it is checked out.
func TestDefaultProjectConfigMatchesThePlatformsLayout(t *testing.T) {
	var entries []map[string]any
	if err := json.Unmarshal([]byte(defaultProjectConfig), &entries); err != nil {
		t.Fatalf("defaultProjectConfig is not JSON: %v", err)
	}
	var expected []map[string]any
	if err := json.Unmarshal([]byte(expectedProjectConfig), &expected); err != nil {
		t.Fatalf("expectedProjectConfig is not JSON: %v", err)
	}
	if !reflect.DeepEqual(entries, expected) {
		t.Fatalf("defaultProjectConfig = %v, the platform builds %v", entries, expected)
	}
	ids := make([]string, len(entries))
	for i, entry := range entries {
		ids[i], _ = entry["id"].(string)
	}
	if strings.Join(ids, ",") != "run_name,avg_cost,avg_latency,rank" {
		t.Fatalf("entry ids = %v", ids)
	}

	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "futureagi", "tracer", "utils", "helper.py"))
	if err != nil {
		t.Skip("platform source not checked out")
	}
	source := string(raw)
	class := regexp.MustCompile(`(?m)^class FieldConfig:\n((?:[ \t]+.*\n|\n)*)`).FindStringSubmatch(source)
	if class == nil {
		t.Fatal("FieldConfig not found in the platform source")
	}
	var fields []string
	for _, m := range regexp.MustCompile(`(?m)^    (\w+):`).FindAllStringSubmatch(class[1], -1) {
		fields = append(fields, m[1])
	}
	sort.Strings(fields)
	for _, entry := range entries {
		keys := make([]string, 0, len(entry))
		for k := range entry {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		if strings.Join(keys, ",") != strings.Join(fields, ",") {
			t.Fatalf("entry %v has fields %v, the platform's FieldConfig has %v", entry["id"], keys, fields)
		}
	}
	fn := regexp.MustCompile(`(?s)def get_default_project_version_config\(\):(.*?)\n\n\n`).FindStringSubmatch(source)
	if fn == nil {
		t.Fatal("get_default_project_version_config not found in the platform source")
	}
	var pythonIDs []string
	for _, m := range regexp.MustCompile(`FieldConfig\(\s*id="(\w+)"`).FindAllStringSubmatch(fn[1], -1) {
		pythonIDs = append(pythonIDs, m[1])
	}
	if strings.Join(pythonIDs, ",") != strings.Join(ids, ",") {
		t.Fatalf("platform default config ids %v, collector %v", pythonIDs, ids)
	}
}
