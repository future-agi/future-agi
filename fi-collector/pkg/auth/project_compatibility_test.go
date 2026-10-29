package auth

import (
	"context"
	"errors"
	"testing"
	"time"
)

// These spellings are accepted by the app's ProjectSerializer and rename
// serializer (see tracer/tests/test_project_name_collector_compatibility.py).
// Resources are exact names, not the more restrictive header transport.
func TestResourceProjectAppCompatibleNames(t *testing.T) {
	for _, name := range []string{
		"deploy ✈️", "team 👩‍💻", "family 👨‍👩‍👧‍👦", "क्‍ष",
		"check​out", "check­out", "pro͏j", "pro j",
		"research, development", " project ", "a  b", "Café",
	} {
		for _, cached := range []bool{false, true} {
			t.Run(name+map[bool]string{false: "/create", true: "/cached"}[cached], func(t *testing.T) {
				r := &recoveringProjectResolver{}
				a, result := projectTestAuth(r)
				if cached {
					result.SetProjects(map[string]string{name: "project-id"})
				}
				td := namedTraces(name)
				if dropped, err := StampResourceAttrs(context.Background(), a, "key", td, result); err != nil || dropped != 0 {
					t.Fatalf("app-compatible name rejected: dropped=%d err=%v", dropped, err)
				}
				attrs := td.ResourceSpans().At(0).Resource().Attributes()
				if getStrAttr(attrs, "project_name") != name || getStrAttr(attrs, "fi.project_id") != "project-id" {
					t.Fatal("project spelling or mapping lost")
				}
				if cached && (r.lookups != 0 || r.creates != 0) {
					t.Fatal("cached project unexpectedly resolved")
				}
			})
		}
	}
}

func TestResourceProjectRejectsOnlyInvisibleNames(t *testing.T) {
	for _, name := range []string{"", "  ", "​", string([]byte{0xef, 0xbb, 0xbf}), "‍️", "ᅟㅤ", "́⠀"} {
		r := &recoveringProjectResolver{}
		a, result := projectTestAuth(r)
		if _, err := StampResourceAttrs(context.Background(), a, "key", namedTraces(name), result); err == nil {
			t.Errorf("invisible name %q accepted", name)
		}
		if r.lookups != 0 || r.creates != 0 {
			t.Errorf("invisible name %q reached persistence", name)
		}
	}
}

type interruptedProjectResolver struct {
	lookup, create func()
}

func (r *interruptedProjectResolver) ResolveProjects(context.Context, string, string, []string) (map[string]string, error) {
	if r.lookup != nil {
		r.lookup()
		return map[string]string{"new": "persisted-id"}, nil
	}
	return nil, nil
}
func (r *interruptedProjectResolver) GetOrCreateProject(context.Context, string, string, string, string) (string, error) {
	if r.create != nil {
		r.create()
	}
	return "persisted-id", nil
}

// Deterministic cache operations at both persistence boundaries: no timers or
// sleeps. A same-scope refresh is also a replacement of the authenticated result.
func TestProjectCacheChangeIsRetryable(t *testing.T) {
	for _, phase := range []string{"lookup", "create"} {
		for _, change := range []string{"evict", "expire", "expire-without-eviction", "replace", "replace-with-project", "scope"} {
			t.Run(phase+"/"+change, func(t *testing.T) {
				r := &interruptedProjectResolver{}
				a, result := projectTestAuth(r)
				var replacement *ResolveResult
				interrupt := func() {
					switch change {
					case "evict":
						a.cache.m.Delete("key")
					case "expire", "expire-without-eviction":
						a.cache.m.Store("key", &cacheEntry{result: result, fetchedAt: time.Now().Add(-2 * time.Hour)})
						if change == "expire" {
							if _, status := a.cache.get("key"); status != "miss" {
								t.Fatal("test entry did not expire")
							}
						}
					default:
						replacement = &ResolveResult{OrgID: result.OrgID, WorkspaceID: result.WorkspaceID, Projects: map[string]string{}}
						if change == "scope" {
							replacement.WorkspaceID = "other-workspace"
						}
						if change == "replace-with-project" {
							replacement.SetProjects(map[string]string{"new": "replacement-id"})
						}
						a.cache.putPositive("key", replacement)
					}
				}
				if phase == "lookup" {
					r.lookup = interrupt
				} else {
					r.create = interrupt
				}
				td := namedTraces("new")
				_, err := StampResourceAttrs(context.Background(), a, "key", td, result)
				if !errors.Is(err, ErrProjectResolutionUnavailable) {
					t.Errorf("cache %s during %s must be retryable: %v", change, phase, err)
				}
				if td.SpanCount() != 1 || getStrAttr(td.ResourceSpans().At(0).Resource().Attributes(), "fi.project_id") != "" || len(result.failed) != 0 {
					t.Error("failed attempt stamped/dropped spans or negatively cached name")
				}
				if replacement != nil {
					id, ok := replacement.GetProject("new")
					if change == "replace-with-project" {
						if !ok || id != "replacement-id" {
							t.Error("stale resolution overwrote the replacement's project")
						}
					} else if ok {
						t.Error("stale resolution extended the replacement result")
					}
				}
				r.lookup, r.create = nil, nil
				a.cache.putPositive("key", result)
				if dropped, err := StampResourceAttrs(context.Background(), a, "key", td, result); err != nil || dropped != 0 {
					t.Fatalf("retry failed: dropped=%d err=%v", dropped, err)
				}
			})
		}
	}
}
