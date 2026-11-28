package server

import (
	"context"
	"errors"
	"testing"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"github.com/future-agi/future-agi/fi-collector/pkg/normalization"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

type attachmentStagerTest struct {
	candidates []AttachmentCandidate
	calls      int
	err        error
}

func (b *attachmentStagerTest) Stage(_ context.Context, candidates []AttachmentCandidate) error {
	b.calls++
	b.candidates = candidates
	return b.err
}

func attachmentBatch(projects ...string) ptrace.Traces {
	td := ptrace.NewTraces()
	for _, project := range projects {
		resource := td.ResourceSpans().AppendEmpty()
		resource.Resource().Attributes().PutStr("fi.project_id", project)
		// Forged tenant resource attrs must not become candidate authority.
		resource.Resource().Attributes().PutStr("fi.organization_id", "forged")
		span := resource.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
		span.SetTraceID(pcommon.TraceID{1})
		span.SetSpanID(pcommon.SpanID{1})
		attrs := span.Attributes()
		attrs.PutInt(normalization.AttachmentSchema, 1)
		attrs.PutStr(normalization.AttachmentRefs, "[]")
		attrs.PutStr(normalization.AttachmentSession, "12345678-1234-1234-1234-123456789abc")
		attrs.PutStr(normalization.AttachmentNonce, "87654321-4321-4321-4321-cba987654321")
	}
	return td
}

func TestAttachmentStageRequiresAuthAndPerResourceScope(t *testing.T) {
	broker := &attachmentStagerTest{}
	s := (&Server{}).WithAttachmentBindingStager(broker)
	td := attachmentBatch("allowed", "foreign")
	if err := s.stageAttachmentBindings(context.Background(), td); err != nil || broker.calls != 0 {
		t.Fatal("unauthenticated staging")
	}
	result := &auth.ResolveResult{OrgID: "org", WorkspaceID: "workspace", Projects: map[string]string{"project": "allowed"}}
	ctx := auth.WithResolvedContext(context.Background(), result, "synthetic-key-digest")
	if err := s.stageAttachmentBindings(ctx, td); err != nil {
		t.Fatal(err)
	}
	if broker.calls != 1 || len(broker.candidates) != 1 {
		t.Fatal("mixed scope leak")
	}
	c := broker.candidates[0]
	if c.OrganizationID != "org" || c.WorkspaceID != "workspace" || c.ProjectID != "allowed" || c.TraceID != "01000000-0000-0000-0000-000000000000" {
		t.Fatalf("bad candidate: %+v", c)
	}
	broker.err = errors.New("synthetic rejected session")
	if err := s.stageAttachmentBindings(ctx, td); err == nil {
		t.Fatal("forged session accepted")
	}
}

func TestAttachmentStageDefaultOffAndBounded(t *testing.T) {
	if err := (&Server{}).stageAttachmentBindings(context.Background(), attachmentBatch("p")); err != nil {
		t.Fatal(err)
	}
	result := &auth.ResolveResult{OrgID: "org", WorkspaceID: "workspace", Projects: map[string]string{"project": "p"}}
	ctx := auth.WithResolvedContext(context.Background(), result, "synthetic")
	broker := &attachmentStagerTest{}
	s := (&Server{}).WithAttachmentBindingStager(broker)
	projects := make([]string, 257)
	for i := range projects {
		projects[i] = "p"
	}
	if err := s.stageAttachmentBindings(ctx, attachmentBatch(projects...)); err == nil || broker.calls != 0 {
		t.Fatal("unbounded broker batch")
	}
}
