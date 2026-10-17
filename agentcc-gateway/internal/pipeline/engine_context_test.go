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
