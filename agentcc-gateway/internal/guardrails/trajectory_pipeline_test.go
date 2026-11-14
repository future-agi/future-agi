package guardrails

import (
	"context"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

// trajectoryCaptureGuardrail records the TrajectoryContext it sees from
// inside Check, which is where a stateful guardrail would read it.
type trajectoryCaptureGuardrail struct {
	name  string
	stage Stage

	mu  sync.Mutex
	got []*TrajectoryContext
}

func (g *trajectoryCaptureGuardrail) Name() string { return g.name }
func (g *trajectoryCaptureGuardrail) Stage() Stage { return g.stage }
func (g *trajectoryCaptureGuardrail) Check(ctx context.Context, _ *CheckInput) *CheckResult {
	tc := TrajectoryContextFromContext(ctx)
	g.mu.Lock()
	g.got = append(g.got, tc)
	g.mu.Unlock()
	return &CheckResult{Pass: true}
}

func (g *trajectoryCaptureGuardrail) seen(t *testing.T) *TrajectoryContext {
	t.Helper()
	g.mu.Lock()
	defer g.mu.Unlock()
	if len(g.got) != 1 {
		t.Fatalf("%s ran %d times, want 1", g.name, len(g.got))
	}
	return g.got[0]
}

func newTrajectoryPipeline() (*pipeline.Engine, *trajectoryCaptureGuardrail, *trajectoryCaptureGuardrail) {
	pre := &trajectoryCaptureGuardrail{name: "trajectory-pre", stage: StagePre}
	post := &trajectoryCaptureGuardrail{name: "trajectory-post", stage: StagePost}
	cfg := config.GuardrailsConfig{Enabled: true, DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: pre.name, Stage: "pre", Mode: "sync", Action: "log", Threshold: 0.5},
			{Name: post.name, Stage: "post", Mode: "sync", Action: "log", Threshold: 0.5},
		}}
	registry := map[string]Guardrail{pre.name: pre, post.name: post}
	return pipeline.NewEngine(NewPlugin(NewEngine(cfg, registry), registry, nil, nil, nil)), pre, post
}

func trajectoryRequest() *models.RequestContext {
	rc := models.AcquireRequestContext()
	rc.RequestID = "req-123"
	rc.TraceID = "trace-456"
	rc.SessionID = "session-789"
	rc.UserID = "user-abc"
	rc.Model = "gpt-4o"
	rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o",
		Messages: []models.Message{{Role: "user", Content: []byte(`"hello"`)}}}
	rc.Metadata["gen_ai.conversation.id"] = "conversation-1"
	rc.Metadata["gen_ai.agent.id"] = "agent-1"
	rc.Metadata["a2a_task_id"] = "task-1"
	rc.Metadata["a2a_context_id"] = "context-2"
	return rc
}

var wantTrajectory = TrajectoryContext{
	RequestID:      "req-123",
	TraceID:        "trace-456",
	SessionID:      "session-789",
	UserID:         "user-abc",
	ConversationID: "conversation-1",
	A2ATaskID:      "task-1",
	A2AContextID:   "context-2",
	AgentID:        "agent-1",
}

func respond(_ context.Context, rc *models.RequestContext) error {
	rc.Response = &models.ChatCompletionResponse{Model: "gpt-4o",
		Choices: []models.Choice{{Message: models.Message{Role: "assistant", Content: []byte(`"hi"`)}}}}
	return nil
}

// A guardrail run by the guardrails plugin sees the request's correlation
// identifiers at both stages of a non-streamed request.
func TestGuardrailCheckSeesTrajectoryContext(t *testing.T) {
	engine, pre, post := newTrajectoryPipeline()
	rc := trajectoryRequest()
	defer rc.Release()

	if err := engine.Process(context.Background(), rc, respond); err != nil {
		t.Fatalf("Process() error = %v", err)
	}
	for _, g := range []*trajectoryCaptureGuardrail{pre, post} {
		got := g.seen(t)
		if got == nil || *got != wantTrajectory {
			t.Errorf("%s saw trajectory context %#v, want %#v", g.name, got, wantTrajectory)
		}
	}
}

// A streamed response is checked by RunPostPlugins after the stream ends, a
// separate call from Process, and the post-stage guardrail still sees it.
func TestStreamedPostGuardrailSeesTrajectoryContext(t *testing.T) {
	engine, pre, post := newTrajectoryPipeline()
	rc := trajectoryRequest()
	defer rc.Release()
	rc.IsStream = true

	if err := engine.Process(context.Background(), rc, respond); err != nil {
		t.Fatalf("Process() error = %v", err)
	}
	if got := pre.seen(t); got == nil || *got != wantTrajectory {
		t.Errorf("pre-stage guardrail saw %#v, want %#v", got, wantTrajectory)
	}
	post.mu.Lock()
	ranEarly := len(post.got)
	post.mu.Unlock()
	if ranEarly != 0 {
		t.Fatalf("post-stage guardrail ran %d times inside Process for a stream", ranEarly)
	}

	if err := engine.RunPostPlugins(context.Background(), rc); err != nil {
		t.Fatalf("RunPostPlugins() error = %v", err)
	}
	if got := post.seen(t); got == nil || *got != wantTrajectory {
		t.Errorf("post-stage guardrail saw %#v, want %#v", got, wantTrajectory)
	}
}
