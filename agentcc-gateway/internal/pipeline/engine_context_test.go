package pipeline

import (
	"context"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

type requestContextCapturePlugin struct {
	preContext  *models.RequestContext
	postContext *models.RequestContext
}

func (p *requestContextCapturePlugin) Name() string  { return "request-context-capture" }
func (p *requestContextCapturePlugin) Priority() int { return 10 }

func (p *requestContextCapturePlugin) ProcessRequest(ctx context.Context, _ *models.RequestContext) PluginResult {
	p.preContext = models.GetRequestContext(ctx)
	return ResultContinue()
}

func (p *requestContextCapturePlugin) ProcessResponse(ctx context.Context, _ *models.RequestContext) PluginResult {
	p.postContext = models.GetRequestContext(ctx)
	return ResultContinue()
}

func TestEngineExposesRequestContextToPrePlugins(t *testing.T) {
	plugin := &requestContextCapturePlugin{}
	engine := NewEngine(plugin)
	rc := models.AcquireRequestContext()
	defer rc.Release()

	// Streaming keeps post-processing separate so pre and post propagation can
	// be asserted independently.
	rc.IsStream = true

	providerSawContext := false
	err := engine.Process(context.Background(), rc, func(ctx context.Context, _ *models.RequestContext) error {
		providerSawContext = models.GetRequestContext(ctx) != nil
		return nil
	})
	if err != nil {
		t.Fatalf("Process() error = %v", err)
	}
	if plugin.preContext != rc {
		t.Fatalf("pre-plugin request context = %p, want %p", plugin.preContext, rc)
	}
	if providerSawContext {
		t.Fatal("provider context unexpectedly contains pipeline RequestContext")
	}
	if plugin.postContext != nil {
		t.Fatal("post-plugin should not run inside Process for streaming request")
	}
}

func TestRunPostPluginsExposesRequestContextWhenCalledDirectly(t *testing.T) {
	plugin := &requestContextCapturePlugin{}
	engine := NewEngine(plugin)
	rc := models.AcquireRequestContext()
	defer rc.Release()

	engine.RunPostPlugins(context.Background(), rc)

	if plugin.postContext != rc {
		t.Fatalf("post-plugin request context = %p, want %p", plugin.postContext, rc)
	}
}

// parallelContextObserver is a read-only post-plugin, the kind the engine runs
// concurrently with other observers.
type parallelContextObserver struct {
	name string
	seen *models.RequestContext
}

func (p *parallelContextObserver) Name() string         { return p.name }
func (p *parallelContextObserver) Priority() int        { return 900 }
func (p *parallelContextObserver) IsPostParallel() bool { return true }
func (p *parallelContextObserver) ProcessRequest(context.Context, *models.RequestContext) PluginResult {
	return ResultContinue()
}

func (p *parallelContextObserver) ProcessResponse(ctx context.Context, _ *models.RequestContext) PluginResult {
	// Read the way a correlation helper does; writing here would be a
	// concurrent map write.
	if rc := models.GetRequestContext(ctx); rc != nil {
		_ = rc.Metadata["gen_ai.conversation.id"]
		_ = rc.SessionID
		p.seen = rc
	}
	return ResultContinue()
}

// Parallel observers get the request context too. Run with -race: they read
// it concurrently and none of them writes.
func TestParallelPostPluginsSeeRequestContext(t *testing.T) {
	a := &parallelContextObserver{name: "observer-a"}
	b := &parallelContextObserver{name: "observer-b"}
	engine := NewEngine(a, b)
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Metadata["gen_ai.conversation.id"] = "conversation-1"

	if err := engine.Process(context.Background(), rc, func(context.Context, *models.RequestContext) error {
		return nil
	}); err != nil {
		t.Fatalf("Process() error = %v", err)
	}
	for _, p := range []*parallelContextObserver{a, b} {
		if p.seen != rc {
			t.Errorf("%s request context = %p, want %p", p.name, p.seen, rc)
		}
	}
}
