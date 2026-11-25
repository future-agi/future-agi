package pricing

import (
	"context"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

type projectTypesKey struct{}

// WithProjectTypes snapshots the resolved type of each stamped project once for
// pricing. Nothing is added to the traces; unknown types stay unknown even if
// another request populates the cache before this batch finishes converting.
func WithProjectTypes(ctx context.Context, td ptrace.Traces) context.Context {
	resolved := auth.FromContext(ctx)
	if resolved == nil {
		return ctx
	}
	types := make(map[string]string)
	resources := td.ResourceSpans()
	for i := 0; i < resources.Len(); i++ {
		v, ok := resources.At(i).Resource().Attributes().Get("fi.project_id")
		if !ok || v.Str() == "" {
			continue
		}
		id := v.Str()
		if _, seen := types[id]; !seen {
			types[id] = resolved.ProjectTypeInWorkspace(resolved.WorkspaceID, id)
		}
	}
	return context.WithValue(ctx, projectTypesKey{}, types)
}
