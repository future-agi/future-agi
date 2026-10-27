package auth

import (
	"context"
	"fmt"
	"strings"

	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

// StampResourceAttrs walks all ResourceSpans in the traces, resolves
// project_name -> project_id from the auth result, and stamps fi.project_id
// + fi.org_id onto each ResourceSpans' resource attributes.
//
// ResourceSpans with an unresolvable project name are dropped.
// Returns the number of dropped ResourceSpans (0 in the happy path).
func StampResourceAttrs(ctx context.Context, a *Authenticator, cacheKey string, traces ptrace.Traces, result *ResolveResult) (int, error) {
	if result == nil {
		return 0, nil
	}

	rss := traces.ResourceSpans()
	if rss.Len() == 0 {
		return 0, nil
	}
	if result.OrgID == "" || result.WorkspaceID == "" {
		return 0, fmt.Errorf("stamp: authenticated organization/workspace scope is incomplete")
	}

	// Fail-fast: every ResourceSpan must carry project_name.
	var missing []int
	nameSet := make(map[string]struct{})
	for i := 0; i < rss.Len(); i++ {
		attrs := rss.At(i).Resource().Attributes()
		pn := getStrAttr(attrs, "project_name")
		if pn == "" {
			missing = append(missing, i)
			continue
		}
		nameSet[pn] = struct{}{}
	}
	if len(missing) > 0 {
		return 0, fmt.Errorf("stamp: %d ResourceSpan(s) have no project_name (indices: %s)",
			len(missing), formatIndices(missing))
	}

	names := make([]string, 0, len(nameSet))
	for n := range nameSet {
		names = append(names, n)
	}

	if err := a.ResolveProjectsForKey(ctx, cacheKey, result, names); err != nil {
		return 0, fmt.Errorf("stamp: resolve projects: %w", err)
	}

	// Stamp resolvable spans, collect unresolvable project names.
	unresolvable := make(map[string]struct{})
	for i := 0; i < rss.Len(); i++ {
		attrs := rss.At(i).Resource().Attributes()
		projectName := getStrAttr(attrs, "project_name")
		projectID, ok := result.GetProjectInWorkspace(result.WorkspaceID, projectName)
		if !ok || projectID == "" {
			unresolvable[projectName] = struct{}{}
			continue
		}

		attrs.PutStr("fi.project_id", projectID)
		attrs.PutStr("fi.org_id", result.OrgID)
		stampObserveProjectType(attrs, result, projectID)
	}

	if len(unresolvable) == 0 {
		return 0, nil
	}

	// Drop all unresolvable spans.
	before := rss.Len()
	rss.RemoveIf(func(rs ptrace.ResourceSpans) bool {
		attrs := rs.Resource().Attributes()
		name := getStrAttr(attrs, "project_name")
		_, drop := unresolvable[name]
		return drop
	})

	return before - rss.Len(), nil
}

// resourceProjectTypeKey is the resource attribute the fi_instrumentation SDK
// sets to the project's type; the converter links user.id to an end user only
// when it reads "observe" there.
const resourceProjectTypeKey = "project_type"

// stampObserveProjectType writes project_type "observe" onto a resource that
// sends none (absent or an empty string) when its stamped project is known to
// be an Observe project. Exporters that cannot set resource attributes, such
// as OpenRouter Broadcast naming its project with X-Project-Name, send no
// project_type, yet their project is an Observe one, and Django links user.id
// on the project's own type. A project_type the sender set is never changed,
// and a project of another or unknown type gets none.
func stampObserveProjectType(attrs pcommon.Map, result *ResolveResult, projectID string) {
	if v, ok := attrs.Get(resourceProjectTypeKey); ok &&
		(v.Type() != pcommon.ValueTypeStr || v.Str() != "") {
		return
	}
	if result.ProjectTypeInWorkspace(result.WorkspaceID, projectID) != ProjectTypeObserve {
		return
	}
	attrs.PutStr(resourceProjectTypeKey, ProjectTypeObserve)
}

// getStrAttr returns the string value of key from attrs, or "" if absent/wrong type.
func getStrAttr(attrs pcommon.Map, key string) string {
	v, ok := attrs.Get(key)
	if !ok || v.Type() != pcommon.ValueTypeStr {
		return ""
	}
	return v.Str()
}

func formatIndices(indices []int) string {
	if len(indices) <= 5 {
		return fmt.Sprintf("%v", indices)
	}
	strs := make([]string, 5)
	for i := 0; i < 5; i++ {
		strs[i] = fmt.Sprintf("%d", indices[i])
	}
	return strings.Join(strs, ", ") + fmt.Sprintf(" ... (%d total)", len(indices))
}
