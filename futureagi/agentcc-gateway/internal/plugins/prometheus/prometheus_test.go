package prometheus

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/metrics"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

func newTestRC() *models.RequestContext {
	return &models.RequestContext{
		RequestID:     "req-123",
		StartTime:     time.Now().Add(-100 * time.Millisecond),
		Model:         "gpt-4",
		ResolvedModel: "gpt-4-0613",
		Provider:      "openai",
		Metadata:      map[string]string{},
		Timings:       map[string]time.Duration{},
		Errors:        []error{},
		Response: &models.ChatCompletionResponse{
			Usage: &models.Usage{
				PromptTokens:     150,
				CompletionTokens: 50,
			},
		},
	}
}

func TestPlugin_Disabled(t *testing.T) {
	reg := metrics.NewRegistry()
	p := New(reg, false)

	rc := newTestRC()
	result := p.ProcessResponse(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Fatal("disabled plugin should continue")
	}

	out := reg.Render()
	if strings.Contains(out, "agentcc_requests_total") {
		t.Fatal("disabled plugin should not record metrics")
	}
}

func TestPlugin_NilRegistry(t *testing.T) {
	p := New(nil, true)
	rc := newTestRC()
	result := p.ProcessResponse(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Fatal("nil registry should continue")
	}
}

func TestPlugin_NameAndPriority(t *testing.T) {
	p := New(metrics.NewRegistry(), true)
	if p.Name() != "prometheus" {
		t.Fatalf("Name() = %q, want prometheus", p.Name())
	}
	if p.Priority() != 998 {
		t.Fatalf("Priority() = %d, want 998", p.Priority())
	}
}

func TestPlugin_ProcessRequest_NoOp(t *testing.T) {
	p := New(metrics.NewRegistry(), true)
	result := p.ProcessRequest(context.Background(), newTestRC())
	if result.Action != pipeline.Continue {
		t.Fatal("ProcessRequest should be a no-op")
	}
}

func TestPlugin_RequestCounter(t *testing.T) {
	reg := metrics.NewRegistry()
	p := New(reg, true)

	rc := newTestRC()
	p.ProcessResponse(context.Background(), rc)

	out := reg.Render()
	if !strings.Contains(out, `agentcc_requests_total{model="gpt-4-0613",provider="openai",status="ok"} 1`) {
		t.Fatalf("expected request counter:\n%s", out)
	}
}

func TestPlugin_ErrorCounter(t *testing.T) {
	reg := metrics.NewRegistry()
	p := New(reg, true)

	rc := newTestRC()
	rc.Errors = append(rc.Errors, errors.New("timeout"))
	p.ProcessResponse(context.Background(), rc)

	out := reg.Render()
	if !strings.Contains(out, `agentcc_requests_total{model="gpt-4-0613",provider="openai",status="error"} 1`) {
		t.Fatalf("expected error status:\n%s", out)
	}
	if !strings.Contains(out, `agentcc_errors_total{model="gpt-4-0613",provider="openai"} 1`) {
		t.Fatalf("expected error counter:\n%s", out)
	}
}

func TestPlugin_TokenCounters(t *testing.T) {
	reg := metrics.NewRegistry()
	p := New(reg, true)

	rc := newTestRC()
	p.ProcessResponse(context.Background(), rc)

	out := reg.Render()
	if !strings.Contains(out, `agentcc_tokens_input_total{model="gpt-4-0613",provider="openai"} 150`) {
		t.Fatalf("expected input tokens 150:\n%s", out)
	}
	if !strings.Contains(out, `agentcc_tokens_output_total{model="gpt-4-0613",provider="openai"} 50`) {
		t.Fatalf("expected output tokens 50:\n%s", out)
	}
}

func TestPlugin_CostCounter(t *testing.T) {
	reg := metrics.NewRegistry()
	p := New(reg, true)

	rc := newTestRC()
	rc.Metadata["cost"] = "0.005000"
	p.ProcessResponse(context.Background(), rc)

	out := reg.Render()
	// 0.005 * 1_000_000 = 5000 microdollars
	if !strings.Contains(out, `agentcc_cost_microdollars_total{model="gpt-4-0613",provider="openai"} 5000`) {
		t.Fatalf("expected cost 5000 microdollars:\n%s", out)
	}
}

func TestPlugin_CacheHitExact(t *testing.T) {
	reg := metrics.NewRegistry()
	p := New(reg, true)

	rc := newTestRC()
	rc.Metadata["cache_status"] = "hit_exact"
	p.ProcessResponse(context.Background(), rc)

	out := reg.Render()
	if !strings.Contains(out, `agentcc_cache_hits_total{type="exact"} 1`) {
		t.Fatalf("expected exact cache hit:\n%s", out)
	}
}

func TestPlugin_CacheHitSemantic(t *testing.T) {
	reg := metrics.NewRegistry()
	p := New(reg, true)

	rc := newTestRC()
	rc.Metadata["cache_status"] = "hit_semantic"
	p.ProcessResponse(context.Background(), rc)

	out := reg.Render()
	if !strings.Contains(out, `agentcc_cache_hits_total{type="semantic"} 1`) {
		t.Fatalf("expected semantic cache hit:\n%s", out)
	}
}

func TestPlugin_CacheMiss(t *testing.T) {
	reg := metrics.NewRegistry()
	p := New(reg, true)

	rc := newTestRC()
	rc.Metadata["cache_status"] = "miss"
	p.ProcessResponse(context.Background(), rc)

	out := reg.Render()
	if !strings.Contains(out, `agentcc_cache_misses_total 1`) {
		t.Fatalf("expected cache miss:\n%s", out)
	}
}

func TestPlugin_DurationHistogram(t *testing.T) {
	reg := metrics.NewRegistry()
	p := New(reg, true)

	rc := newTestRC()
	p.ProcessResponse(context.Background(), rc)

	out := reg.Render()
	if !strings.Contains(out, "agentcc_request_duration_ms_count") {
		t.Fatalf("expected duration histogram:\n%s", out)
	}
}

func TestPlugin_NilResponse(t *testing.T) {
	reg := metrics.NewRegistry()
	p := New(reg, true)

	rc := newTestRC()
	rc.Response = nil
	p.ProcessResponse(context.Background(), rc)

	out := reg.Render()
	// Should still count the request.
	if !strings.Contains(out, `agentcc_requests_total`) {
		t.Fatalf("should count request even with nil response:\n%s", out)
	}
	// Should NOT have token counters.
	if strings.Contains(out, "agentcc_tokens_input_total") {
		t.Fatalf("should not have token counters with nil response:\n%s", out)
	}
}

func TestPlugin_NilUsage(t *testing.T) {
	reg := metrics.NewRegistry()
	p := New(reg, true)

	rc := newTestRC()
	rc.Response = &models.ChatCompletionResponse{Usage: nil}
	p.ProcessResponse(context.Background(), rc)

	out := reg.Render()
	if !strings.Contains(out, `agentcc_requests_total`) {
		t.Fatalf("should count request:\n%s", out)
	}
	if strings.Contains(out, "agentcc_tokens_input_total") {
		t.Fatalf("should not have tokens with nil usage:\n%s", out)
	}
}

func TestPlugin_FallbackToModel(t *testing.T) {
	reg := metrics.NewRegistry()
	p := New(reg, true)

	rc := newTestRC()
	rc.ResolvedModel = "" // No resolved model.
	p.ProcessResponse(context.Background(), rc)

	out := reg.Render()
	if !strings.Contains(out, `model="gpt-4"`) {
		t.Fatalf("should fall back to Model when ResolvedModel empty:\n%s", out)
	}
}

func TestPlugin_Concurrent(t *testing.T) {
	reg := metrics.NewRegistry()
	p := New(reg, true)
	ctx := context.Background()

	var wg sync.WaitGroup
	for i := 0; i < 50; i++ {
		wg.Add(1)
		go func(n int) {
			defer wg.Done()
			rc := newTestRC()
			rc.RequestID = fmt.Sprintf("req-%d", n)
			p.ProcessResponse(ctx, rc)
		}(i)
	}
	wg.Wait()

	out := reg.Render()
	if !strings.Contains(out, `agentcc_requests_total{model="gpt-4-0613",provider="openai",status="ok"} 50`) {
		t.Fatalf("expected 50 requests after concurrent:\n%s", out)
	}
}
