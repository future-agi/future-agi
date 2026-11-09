package auth

import (
	"context"
	"errors"
	"fmt"
	"reflect"
	"sort"
	"strconv"
	"sync"
	"testing"
	"time"

	"go.opentelemetry.io/collector/pdata/ptrace"
)

type composeProjectResolver struct {
	found                    map[string]string
	reads, creates           int
	org, workspace           string
	created                  []string
	beforeRead, beforeCreate func()
}

func (r *composeProjectResolver) ResolveProjects(_ context.Context, org, workspace string, names []string) (map[string]string, error) {
	r.reads++
	r.org, r.workspace = org, workspace
	if r.beforeRead != nil {
		r.beforeRead()
	}
	out := map[string]string{}
	for _, n := range names {
		if id, ok := r.found[n]; ok {
			out[n] = id
		}
	}
	return out, nil
}
func (r *composeProjectResolver) GetOrCreateProject(_ context.Context, _, _, name, _ string) (string, error) {
	r.creates++
	r.created = append(r.created, name)
	if r.beforeCreate != nil {
		r.beforeCreate()
	}
	return "project", nil
}
func composeProjectTraces(names []string) ptrace.Traces {
	traces := ptrace.NewTraces()
	for _, n := range names {
		rs := traces.ResourceSpans().AppendEmpty()
		rs.Resource().Attributes().PutStr("project_name", n)
		rs.Resource().Attributes().PutStr("preserved", "original")
		rs.ScopeSpans().AppendEmpty().Spans().AppendEmpty().SetName("span")
	}
	return traces
}

type composeTypedProjects struct{ *composeProjectResolver }

func (r composeTypedProjects) ResolveProjectsWithTypes(ctx context.Context, org, workspace string, names []string) (map[string]ResolvedProject, error) {
	ids, err := r.ResolveProjects(ctx, org, workspace, names)
	out := make(map[string]ResolvedProject, len(ids))
	for name, id := range ids {
		out[name] = ResolvedProject{ID: id, TraceType: ProjectTypeObserve}
	}
	return out, err
}

type composeHiddenLookup func(context.Context, string, string) (SimulationProject, error)

func (f composeHiddenLookup) EnsureSimulationProject(ctx context.Context, org, agent string) (SimulationProject, error) {
	return f(ctx, org, agent)
}

func TestComposeGrantExpiryDuringScopedProjectRecording(t *testing.T) {
	for _, operation := range []string{"typed", "expected", "create", "hidden", "cached"} {
		for _, change := range []string{"expiry", "revocation", "replacement", "live"} {
			t.Run(operation+"/"+change, func(t *testing.T) {
				fields := sandboxFields(time.Now())
				if change == "expiry" {
					fields[8] = strconv.FormatInt(time.Now().Unix()+1, 10)
				}
				claims, err := VerifyCollectorGrant("fit_"+fields[9], signedGrantFields(fields, goldenPepper), mustPeppers(t, goldenPepper), []string{"k1"}, time.Now())
				if err != nil {
					t.Fatal(err)
				}
				result := sandboxResult()
				result.Grant = claims
				a, _ := newRouter(t, nil)
				key := "grant:" + claims.ID
				a.cache.putPositive(key, result)
				entered, release := make(chan struct{}), make(chan struct{})
				var once sync.Once
				defer once.Do(func() { close(release) })
				pause := func() { close(entered); <-release }
				r := &composeProjectResolver{found: map[string]string{}}
				if operation == "create" {
					r.beforeCreate = pause
				} else {
					r.beforeRead = pause
					r.found["selected"] = routeProdID
				}
				a.projects = composeTypedProjects{r}
				hiddenCalls := 0
				a.sim = composeHiddenLookup(func(_ context.Context, org, agent string) (SimulationProject, error) {
					hiddenCalls++
					if org != result.OrgID || agent != routeAgent {
						t.Error("hidden lookup escaped authenticated scope")
					}
					pause()
					return hiddenProject(), nil
				})
				if operation == "cached" {
					result.SetProject("selected", routeProdID)
				}
				traces := composeProjectTraces([]string{"selected"})
				ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
				defer cancel()
				type outcome struct {
					dropped int
					err     error
					routed  RouteOutcome
				}
				done := make(chan outcome, 1)
				go func() {
					var out RouteOutcome
					if operation == "cached" {
						pause()
					}
					if operation == "hidden" {
						var err error
						out, err = a.RouteSimulation(ctx, key, traces, result)
						if err != nil {
							done <- outcome{err: err}
							return
						}
					}
					var expected *ExpectedTarget
					if operation == "expected" {
						expected = &ExpectedTarget{result.OrgID, result.WorkspaceID, routeProdID}
					}
					n, err := StampResourceAttrsExpected(ctx, a, key, traces, result, expected)
					done <- outcome{n, err, out}
				}()
				composeWait(t, entered)
				var replacement *ResolveResult
				switch change {
				case "expiry":
					timer := time.NewTimer(time.Until(time.Unix(claims.Exp, 0)))
					defer timer.Stop()
					<-timer.C
				case "revocation":
					a.evictCredential("grant:" + claims.ID)
				case "replacement":
					replacement = sandboxResult()
					replacement.Grant = claims
					a.InvalidateKey(key)
					a.cache.putPositive(key, replacement)
				}
				once.Do(func() { close(release) })
				var got outcome
				select {
				case got = <-done:
				case <-ctx.Done():
					t.Fatal("scoped operation did not settle under original deadline")
				}
				if got.dropped != 0 || traces.SpanCount() != 1 {
					t.Fatal("auth change permanently dropped spans")
				}
				if operation == "hidden" {
					if hiddenCalls != 1 || got.routed.Hidden != routeHiddenID {
						t.Fatal("hidden lookup did not complete")
					}
					if id, ok := result.GetProject(routeHiddenKey); !ok || id != routeHiddenID {
						t.Fatal("request-local hidden mapping lost")
					}
				} else if operation != "cached" {
					if r.reads != 1 || r.org != result.OrgID || r.workspace != result.WorkspaceID {
						t.Fatal("scoped typed lookup not reached")
					}
				}
				if operation == "create" && r.creates != 1 {
					t.Fatal("create barrier not reached")
				}
				if change == "live" {
					if got.err != nil || a.cache.projectEntry(key, result) == nil {
						t.Fatalf("live same-result control refused: %v", got.err)
					}
					if operation == "typed" || operation == "expected" {
						if id, ok := result.GetProject("selected"); !ok || id != routeProdID || result.ProjectTypeInWorkspace(result.WorkspaceID, id) != ProjectTypeObserve {
							t.Fatal("a9045a4f33 project/type not recorded on the same result")
						}
					}
					return
				}
				if operation == "expected" {
					if !errors.Is(got.err, ErrExpectedTargetMismatch) {
						t.Fatalf("expected target lost denial: %v", got.err)
					}
				} else {
					var changed *ProjectAuthChangedError
					if !errors.As(got.err, &changed) || !errors.Is(got.err, ErrProjectResolutionUnavailable) {
						t.Fatalf("ordinary project change not retryable: %v", got.err)
					}
				}
				for _, name := range []string{"fi.project_id", "fi.org_id", "project_type"} {
					if _, ok := traces.ResourceSpans().At(0).Resource().Attributes().Get(name); ok {
						t.Fatal("stale project stamped")
					}
				}
				if a.cache.projectEntry(key, result) != nil {
					t.Fatal("expired/revoked/replaced project entry usable")
				}
				if replacement != nil {
					if e := a.cache.projectEntry(key, replacement); e == nil || len(replacement.Projects) != 0 || len(replacement.failed) != 0 || replacement.ProjectTypeInWorkspace(replacement.WorkspaceID, routeProdID) != "" {
						t.Fatal("old operation polluted replacement")
					}
				}
			})
		}
	}
}

func TestComposeExpectedTargetBulkNamesNeverCreate(t *testing.T) {
	var unknown []string
	for i := 0; i < MaxNewProjectsPerRequest+1; i++ {
		unknown = append(unknown, fmt.Sprintf("name-%02d", i))
	}
	for _, mode := range []string{"unknown", "cached-plus-unknown", "matching-before-unknown", "all-aliases", "ordinary"} {
		t.Run(mode, func(t *testing.T) {
			names := append([]string(nil), unknown...)
			result := &ResolveResult{OrgID: "org", WorkspaceID: "workspace", Principal: PrincipalPersonal, KeyType: "user", Projects: map[string]string{}}
			resolver := &composeProjectResolver{found: map[string]string{}}
			if mode == "cached-plus-unknown" {
				names = append([]string{"cached"}, names...)
				result.Projects["cached"] = "project"
			}
			if mode == "matching-before-unknown" {
				names = append([]string{"aa-match"}, names...)
				resolver.found["aa-match"] = "project"
			}
			if mode == "all-aliases" {
				for _, n := range names {
					resolver.found[n] = "project"
				}
			}
			a := &Authenticator{cache: newCache(time.Second, 0), projects: resolver}
			a.cache.putPositive("key", result)
			traces := composeProjectTraces(names)
			before := traces.ResourceSpans().Len()
			spans := traces.SpanCount()
			var target *ExpectedTarget
			if mode != "ordinary" {
				target = &ExpectedTarget{"org", "workspace", "project"}
			}
			dropped, err := StampResourceAttrsExpected(context.Background(), a, "key", traces, result, target)
			if mode == "ordinary" {
				if !errors.Is(err, ErrTooManyNewProjects) || resolver.creates != MaxNewProjectsPerRequest || dropped != 0 {
					t.Fatalf("ordinary creation cap changed: creates=%d err=%v", resolver.creates, err)
				}
				want := append([]string(nil), names...)
				sort.Strings(want)
				if !reflect.DeepEqual(resolver.created, want[:MaxNewProjectsPerRequest]) {
					t.Fatal("ordinary first-16 order changed")
				}
				if n, e := StampResourceAttrs(context.Background(), a, "key", traces, result); n != 0 || e != nil || resolver.creates != len(names) {
					t.Fatalf("ordinary retry did not progress: %v", e)
				}
				return
			}
			if resolver.creates != 0 || resolver.reads != 1 || resolver.org != "org" || resolver.workspace != "workspace" || len(result.failed) != 0 {
				t.Fatalf("expected lookup used ordinary creation or wrong scope: %+v", resolver)
			}
			if mode == "all-aliases" {
				if err != nil || dropped != 0 {
					t.Fatalf("read-only aliases refused by creation cap: %v", err)
				}
			} else if !errors.Is(err, ErrExpectedTargetMismatch) || errors.Is(err, ErrTooManyNewProjects) || errors.Is(err, ErrProjectResolutionUnavailable) || dropped != 0 {
				t.Fatalf("wrong whole-export denial: %v", err)
			}
			if traces.ResourceSpans().Len() != before || traces.SpanCount() != spans {
				t.Fatal("expected target changed the resource/span inventory")
			}
			for i := 0; i < before; i++ {
				attrs := traces.ResourceSpans().At(i).Resource().Attributes()
				if getStrAttr(attrs, "project_name") != names[i] || getStrAttr(attrs, "preserved") != "original" {
					t.Fatal("input attributes changed")
				}
				if mode == "all-aliases" {
					if getStrAttr(attrs, "fi.project_id") != "project" || getStrAttr(attrs, "fi.org_id") != "org" {
						t.Fatal("positive alias control not stamped")
					}
				} else {
					for _, k := range []string{"fi.project_id", "fi.org_id", "project_type"} {
						if _, ok := attrs.Get(k); ok {
							t.Fatal("denied export partially stamped")
						}
					}
				}
			}
			if mode == "unknown" && len(result.Projects) != 0 {
				t.Fatal("unknown expected projects entered the authenticated map")
			}
		})
	}
}
