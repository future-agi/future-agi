package pipeline

import (
	"context"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// mockPlugin is a test plugin.
type mockPlugin struct {
	name     string
	priority int
	onReq    func(ctx context.Context, rc *models.RequestContext) PluginResult
	onResp   func(ctx context.Context, rc *models.RequestContext) PluginResult
}

func (m *mockPlugin) Name() string  { return m.name }
func (m *mockPlugin) Priority() int { return m.priority }
func (m *mockPlugin) ProcessRequest(ctx context.Context, rc *models.RequestContext) PluginResult {
	if m.onReq != nil {
		return m.onReq(ctx, rc)
	}
	return ResultContinue()
}
func (m *mockPlugin) ProcessResponse(ctx context.Context, rc *models.RequestContext) PluginResult {
	if m.onResp != nil {
		return m.onResp(ctx, rc)
	}
	return ResultContinue()
}

func TestEnginePassThrough(t *testing.T) {
	providerCalled := false
	plugin := &mockPlugin{name: "noop", priority: 1}

	engine := NewEngine(plugin)

	rc := models.AcquireRequestContext()
	defer rc.Release()

	err := engine.Process(context.Background(), rc, nil, func(ctx context.Context, rc *models.RequestContext) error {
		providerCalled = true
		rc.Response = &models.ChatCompletionResponse{ID: "test"}
		return nil
	})

	if err != nil {
		t.Fatalf("Process error: %v", err)
	}
	if !providerCalled {
		t.Error("provider should have been called")
	}
	if rc.Response == nil || rc.Response.ID != "test" {
		t.Error("response should be set")
	}
}

func TestEngineShortCircuit(t *testing.T) {
	providerCalled := false

	cachedResp := &models.ChatCompletionResponse{ID: "cached"}
	cachePlugin := &mockPlugin{
		name:     "cache",
		priority: 1,
		onReq: func(ctx context.Context, rc *models.RequestContext) PluginResult {
			return ResultShortCircuit(cachedResp)
		},
	}

	postPluginRan := false
	tracePlugin := &mockPlugin{
		name:     "trace",
		priority: 2,
		onResp: func(ctx context.Context, rc *models.RequestContext) PluginResult {
			postPluginRan = true
			return ResultContinue()
		},
	}

	engine := NewEngine(cachePlugin, tracePlugin)

	rc := models.AcquireRequestContext()
	defer rc.Release()

	err := engine.Process(context.Background(), rc, nil, func(ctx context.Context, rc *models.RequestContext) error {
		providerCalled = true
		return nil
	})

	if err != nil {
		t.Fatalf("Process error: %v", err)
	}
	if providerCalled {
		t.Error("provider should NOT have been called (short-circuited)")
	}
	if rc.Response != cachedResp {
		t.Error("response should be the cached response")
	}
	if !rc.Flags.ShortCircuited {
		t.Error("ShortCircuited flag should be set")
	}
	if !postPluginRan {
		t.Error("post-plugins should still run after short-circuit")
	}
}

func TestEngineErrorShortCircuit(t *testing.T) {
	providerCalled := false

	authPlugin := &mockPlugin{
		name:     "auth",
		priority: 1,
		onReq: func(ctx context.Context, rc *models.RequestContext) PluginResult {
			return ResultError(models.ErrUnauthorized("bad key"))
		},
	}

	engine := NewEngine(authPlugin)

	rc := models.AcquireRequestContext()
	defer rc.Release()

	err := engine.Process(context.Background(), rc, nil, func(ctx context.Context, rc *models.RequestContext) error {
		providerCalled = true
		return nil
	})

	if err == nil {
		t.Fatal("Process should return error")
	}
	if providerCalled {
		t.Error("provider should NOT have been called")
	}

	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("error should be *APIError, got %T", err)
	}
	if apiErr.Status != 401 {
		t.Errorf("status = %d, want 401", apiErr.Status)
	}
}

func TestEngineProviderError(t *testing.T) {
	postPluginRan := false

	tracePlugin := &mockPlugin{
		name:     "trace",
		priority: 1,
		onResp: func(ctx context.Context, rc *models.RequestContext) PluginResult {
			postPluginRan = true
			return ResultContinue()
		},
	}

	engine := NewEngine(tracePlugin)

	rc := models.AcquireRequestContext()
	defer rc.Release()

	err := engine.Process(context.Background(), rc, nil, func(ctx context.Context, rc *models.RequestContext) error {
		return models.ErrUpstreamProvider(500, "provider broke")
	})

	if err == nil {
		t.Fatal("Process should return error")
	}
	if !postPluginRan {
		t.Error("post-plugins should run even when provider errors")
	}
}

func TestEnginePluginOrdering(t *testing.T) {
	var order []string

	makePlugin := func(name string, prio int) Plugin {
		return &mockPlugin{
			name:     name,
			priority: prio,
			onReq: func(ctx context.Context, rc *models.RequestContext) PluginResult {
				order = append(order, "pre_"+name)
				return ResultContinue()
			},
			onResp: func(ctx context.Context, rc *models.RequestContext) PluginResult {
				order = append(order, "post_"+name)
				return ResultContinue()
			},
		}
	}

	// Register in reverse order to verify sorting.
	engine := NewEngine(makePlugin("c", 30), makePlugin("a", 10), makePlugin("b", 20))

	rc := models.AcquireRequestContext()
	defer rc.Release()

	engine.Process(context.Background(), rc, nil, func(ctx context.Context, rc *models.RequestContext) error {
		order = append(order, "provider")
		return nil
	})

	expected := []string{"pre_a", "pre_b", "pre_c", "provider", "post_a", "post_b", "post_c"}
	if len(order) != len(expected) {
		t.Fatalf("order length = %d, want %d: %v", len(order), len(expected), order)
	}
	for i, v := range expected {
		if order[i] != v {
			t.Errorf("order[%d] = %q, want %q", i, order[i], v)
		}
	}
}

func TestEngineContextCancelled(t *testing.T) {
	engine := NewEngine()

	rc := models.AcquireRequestContext()
	defer rc.Release()

	ctx, cancel := context.WithCancel(context.Background())
	cancel() // Cancel before processing.

	err := engine.Process(ctx, rc, nil, func(ctx context.Context, rc *models.RequestContext) error {
		t.Error("provider should not be called when context is cancelled")
		return nil
	})

	if err == nil {
		t.Fatal("Process should return error for cancelled context")
	}
	if !rc.Flags.Timeout {
		t.Error("Timeout flag should be set")
	}
}
