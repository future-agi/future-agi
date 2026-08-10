package guardrails

import (
	"context"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestTrajectoryContextFromContext(t *testing.T) {
	rc := models.AcquireRequestContext()
	defer rc.Release()

	rc.RequestID = "req-123"
	rc.TraceID = "trace-456"
	rc.SessionID = "session-789"
	rc.UserID = "user-abc"
	rc.Metadata["a2a_task_id"] = "task-1"
	rc.Metadata["a2a_context_id"] = "context-2"
	rc.Metadata["agent_id"] = "legacy-agent"
	rc.Metadata["gen_ai.agent.id"] = "otel-agent"

	ctx := models.WithRequestContext(context.Background(), rc)
	got := TrajectoryContextFromContext(ctx)
	if got == nil {
		t.Fatal("expected trajectory context")
	}

	if got.RequestID != "req-123" {
		t.Errorf("RequestID = %q, want %q", got.RequestID, "req-123")
	}
	if got.TraceID != "trace-456" {
		t.Errorf("TraceID = %q, want %q", got.TraceID, "trace-456")
	}
	if got.SessionID != "session-789" {
		t.Errorf("SessionID = %q, want %q", got.SessionID, "session-789")
	}
	if got.UserID != "user-abc" {
		t.Errorf("UserID = %q, want %q", got.UserID, "user-abc")
	}
	if got.A2ATaskID != "task-1" {
		t.Errorf("A2ATaskID = %q, want %q", got.A2ATaskID, "task-1")
	}
	if got.A2AContextID != "context-2" {
		t.Errorf("A2AContextID = %q, want %q", got.A2AContextID, "context-2")
	}
	if got.AgentID != "otel-agent" {
		t.Errorf("AgentID = %q, want standardized metadata value %q", got.AgentID, "otel-agent")
	}
}

func TestTrajectoryContextFromContextFallsBackToLegacyAgentID(t *testing.T) {
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Metadata["agent_id"] = "legacy-agent"

	ctx := models.WithRequestContext(context.Background(), rc)
	got := TrajectoryContextFromContext(ctx)
	if got == nil {
		t.Fatal("expected trajectory context")
	}
	if got.AgentID != "legacy-agent" {
		t.Errorf("AgentID = %q, want %q", got.AgentID, "legacy-agent")
	}
}

func TestTrajectoryContextFromContextWithoutRequestContext(t *testing.T) {
	if got := TrajectoryContextFromContext(context.Background()); got != nil {
		t.Fatalf("expected nil trajectory context, got %#v", got)
	}
}
