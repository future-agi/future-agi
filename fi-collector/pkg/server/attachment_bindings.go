package server

import (
	"context"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"github.com/future-agi/future-agi/fi-collector/pkg/normalization"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

const maxAttachmentCandidates = 256

// AttachmentCandidate is reference-only. Scope comes from authenticated routing,
// not resource attributes supplied by the exporter. IDs are never authorization.
type AttachmentCandidate struct {
	OrganizationID, WorkspaceID, ProjectID string
	TraceID, SpanID                        string
	Projection                             normalization.AttachmentProjection
}

// AttachmentBindingStager is the B02 broker boundary. It resolves the original
// credential identity from the authenticated request context (NOT user_id), then
// checks each item's full scope, registered session/nonce/occurrence and current
// policy/deletion generation. Stage only creates bounded pending associations:
// I03 must observe authoritative live span+root persistence before binding.
// Implementations must never fetch media, log payloads, or treat the first item's
// scope as batch authority. An absent broker means no associations are staged.
type AttachmentBindingStager interface {
	Stage(context.Context, []AttachmentCandidate) error
}

// WithAttachmentBindingStager installs a broker before the server is started.
func (s *Server) WithAttachmentBindingStager(stager AttachmentBindingStager) *Server {
	s.attachmentStager = stager
	return s
}

func (s *Server) stageAttachmentBindings(ctx context.Context, td ptrace.Traces) error {
	if s.attachmentStager == nil {
		return nil
	}
	actor := auth.FromContext(ctx)
	if actor == nil || actor.OrgID == "" || actor.WorkspaceID == "" {
		return nil
	}
	projects, ok := actor.ProjectIDsInWorkspace(actor.WorkspaceID)
	if !ok {
		return normalization.ErrAttachmentReference
	}
	candidates := make([]AttachmentCandidate, 0)
	resources := td.ResourceSpans()
	for i := 0; i < resources.Len(); i++ {
		resource := resources.At(i)
		project := normalization.String(resource.Resource().Attributes(), "fi.project_id")
		if _, allowed := projects[project]; !allowed {
			continue
		}
		scopes := resource.ScopeSpans()
		for j := 0; j < scopes.Len(); j++ {
			spans := scopes.At(j).Spans()
			for k := 0; k < spans.Len(); k++ {
				span := spans.At(k)
				projection, err := normalization.ExtractAttachmentProjection(span.Attributes())
				if err != nil || projection == nil || projection.CaptureSessionID == "" {
					continue
				}
				traceID, err := normalization.CanonicalAttachmentTraceID(span.TraceID().String())
				spanID := span.SpanID().String()
				if err != nil || !normalization.ValidAttachmentSpanID(spanID) {
					continue
				}
				if len(candidates) == maxAttachmentCandidates {
					return normalization.ErrAttachmentReference
				}
				candidates = append(candidates, AttachmentCandidate{
					OrganizationID: actor.OrgID, WorkspaceID: actor.WorkspaceID,
					ProjectID: project, TraceID: traceID, SpanID: spanID, Projection: *projection,
				})
			}
		}
	}
	if len(candidates) == 0 {
		return nil
	}
	return s.attachmentStager.Stage(ctx, candidates)
}
