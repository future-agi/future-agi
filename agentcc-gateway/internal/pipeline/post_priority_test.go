package pipeline

import (
	"context"
	"net/http"
	"slices"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

type latePostPlugin struct {
	mockPlugin
	post int
}

func (p *latePostPlugin) PostPriority() int { return p.post }

// A plugin with a PostPriority keeps its place among the request-side checks
// and moves among the response-side work: budgets check before guardrails and
// record after the cost plugin.
func TestPostPriorityMovesOnlyTheResponseSideWork(t *testing.T) {
	var pre, post []string
	plugin := func(name string, priority int) mockPlugin {
		return mockPlugin{name: name, priority: priority,
			onReq: func(context.Context, *models.RequestContext) PluginResult {
				pre = append(pre, name)
				return ResultContinue()
			},
			onResp: func(context.Context, *models.RequestContext) PluginResult {
				post = append(post, name)
				return ResultContinue()
			},
		}
	}
	budget := &latePostPlugin{mockPlugin: plugin("budget", 40), post: 505}
	guardrails, cost, credits := plugin("guardrails", 50), plugin("cost", 500), plugin("credits", 510)
	engine := NewEngine(&credits, &cost, &guardrails, budget)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	if err := engine.Process(context.Background(), rc, func(context.Context, *models.RequestContext) error { return nil }); err != nil {
		t.Fatal(err)
	}
	if want := []string{"budget", "guardrails", "cost", "credits"}; !slices.Equal(pre, want) {
		t.Errorf("request side ran %v, want %v", pre, want)
	}
	if want := []string{"guardrails", "cost", "budget", "credits"}; !slices.Equal(post, want) {
		t.Errorf("response side ran %v, want %v", post, want)
	}
}

// A response-side block, as an output guardrail's, refuses a non-streamed
// response: Process returns it for the handler to write, and the plugins after
// it see the request as failed. A streamed response is already sent, so
// Process does not run the response side for it.
func TestProcessReturnsAResponseSideBlock(t *testing.T) {
	block := &models.APIError{Status: http.StatusForbidden, Code: "content_blocked", Message: "blocked"}
	var errorsSeenAfter int
	guardrail := &mockPlugin{name: "guardrails", priority: 50,
		onResp: func(context.Context, *models.RequestContext) PluginResult { return ResultError(block) }}
	logger := &mockPlugin{name: "logging", priority: 900,
		onResp: func(_ context.Context, rc *models.RequestContext) PluginResult {
			errorsSeenAfter = len(rc.Errors)
			return ResultContinue()
		}}
	engine := NewEngine(guardrail, logger)

	for _, stream := range []bool{false, true} {
		rc := models.AcquireRequestContext()
		rc.IsStream = stream
		errorsSeenAfter = -1
		err := engine.Process(context.Background(), rc, func(context.Context, *models.RequestContext) error { return nil })
		if stream {
			if err != nil || errorsSeenAfter != -1 {
				t.Errorf("stream: err = %v, response side ran = %v; want nil and not run", err, errorsSeenAfter != -1)
			}
		} else if err != block || errorsSeenAfter != 1 {
			t.Errorf("non-streamed: err = %v, errors seen after the block = %d; want the block and 1", err, errorsSeenAfter)
		}
		rc.Release()
	}
}
