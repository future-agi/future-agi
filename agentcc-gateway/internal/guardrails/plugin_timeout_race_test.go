package guardrails

import (
	"context"
	"encoding/json"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/requestview"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A check that rewrites the request (pii-detection's mask, redact or hash)
// and outlives its timeout never writes the request the gateway sends:
// rc.Request, or the request view of a body passed through, which
// ApplyChecks writes into the body. The check runs on a copy of the
// request's messages, and the copy is written back only from a check that
// finished in time. Its rewrite is not applied, so the request is refused
// (403 remediation_unavailable) rather than sent without it, as when a
// rewrite cannot be written, with fail_open on or off. A rewriting rule in
// async mode runs on a copy too, which is never written back. Run with -race.

// lateRewriter rewrites every message's content and every tool call's
// arguments after its context ends, and only once the test lets it
// (release), so it writes while the test reads the request.
type lateRewriter struct {
	name    string
	release chan struct{}
	done    chan struct{}
}

func newLateRewriter(name string) *lateRewriter {
	return &lateRewriter{name: name, release: make(chan struct{}), done: make(chan struct{})}
}

func (g *lateRewriter) Name() string          { return g.name }
func (g *lateRewriter) Stage() Stage          { return StagePre }
func (g *lateRewriter) RewritesRequest() bool { return true }

func (g *lateRewriter) Check(ctx context.Context, input *CheckInput) *CheckResult {
	defer close(g.done)
	<-ctx.Done()
	<-g.release
	for i := range input.Request.Messages {
		input.Request.Messages[i].Content = json.RawMessage(`"rewritten too late"`)
		for k := range input.Request.Messages[i].ToolCalls {
			input.Request.Messages[i].ToolCalls[k].Function.Arguments = `{"ssn":"rewritten too late"}`
		}
	}
	return &CheckResult{Pass: false, Score: 1, Message: "rewrote"}
}

const raceSent = `"My SSN is 123-45-6789."`

func raceRequest() *models.ChatCompletionRequest {
	return &models.ChatCompletionRequest{Model: "gpt-4o", Messages: []models.Message{
		{Role: "user", Content: json.RawMessage(raceSent)},
		{Role: "assistant", ToolCalls: []models.ToolCall{{ID: "c", Type: "function",
			Function: models.FunctionCall{Name: "f", Arguments: `{"ssn":"123-45-6789"}`}}}},
	}}
}

const raceBody = `{"model":"claude-x","max_tokens":8,"messages":[{"role":"user","content":"My SSN is 123-45-6789."},` +
	`{"role":"assistant","content":[{"type":"tool_use","id":"t","name":"f","input":{"ssn":"123-45-6789"}}]}]}`

// readWhileRewriting lets g write and, until it is done, does what the
// gateway does with the request after the checks: read and serialize it (and
// write a passed-through body's rewrite, ApplyChecks).
func readWhileRewriting(t *testing.T, g *lateRewriter, rc *models.RequestContext) {
	t.Helper()
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		for {
			select {
			case <-g.done:
				return
			default:
			}
			if rc.Request != nil {
				_, _ = json.Marshal(rc.Request)
			}
			if v, ok := rc.PassThrough.(*requestview.View); ok {
				_ = v.ApplyChecks()
				_ = v.Body()
			}
		}
	}()
	close(g.release)
	select {
	case <-g.done:
	case <-time.After(5 * time.Second):
		t.Fatal("the check never finished")
	}
	wg.Wait()
}

// assertAsSent: the request reads as the client sent it.
func assertAsSent(t *testing.T, rc *models.RequestContext) {
	t.Helper()
	if rc.Request != nil {
		if got := string(rc.Request.Messages[0].Content); got != raceSent {
			t.Errorf("message content %s after the check timed out, want %s", got, raceSent)
		}
		if got := rc.Request.Messages[1].ToolCalls[0].Function.Arguments; got != `{"ssn":"123-45-6789"}` {
			t.Errorf("tool call arguments %s after the check timed out, want them as sent", got)
		}
	}
	if v, ok := rc.PassThrough.(*requestview.View); ok {
		if err := v.ApplyChecks(); err != nil {
			t.Errorf("ApplyChecks: %v", err)
		}
		if got := string(v.Body()); got != raceBody {
			t.Errorf("body %s after the check timed out, want it as sent", got)
		}
	}
}

// assertRewriteRefused: the plugin refused the request with
// remediation_unavailable, naming the check.
func assertRewriteRefused(t *testing.T, err *models.APIError, check string) {
	t.Helper()
	if err == nil || err.Code != "remediation_unavailable" || !strings.Contains(err.Message, check) {
		t.Errorf("got %+v, want 403 remediation_unavailable naming %s", err, check)
	}
}

func raceContext(passThrough bool) *models.RequestContext {
	rc := models.AcquireRequestContext()
	rc.Metadata["org_id"] = "org-race"
	if passThrough {
		rc.PassThrough = requestview.Anthropic([]byte(raceBody))
	} else {
		rc.Request = raceRequest()
	}
	return rc
}

func TestPlugin_ARewriteThatTimesOutNeverWritesTheRequest(t *testing.T) {
	for _, failOpen := range []bool{true, false} {
		for _, passThrough := range []bool{false, true} {
			name := map[bool]string{false: "chat", true: "passed through"}[passThrough]
			name += map[bool]string{true: " / fail_open", false: " / fail closed"}[failOpen]

			t.Run("config rule / "+name, func(t *testing.T) {
				g := newLateRewriter("late-rewriter")
				engine := NewEngine(config.GuardrailsConfig{FailOpen: failOpen, DefaultTimeout: time.Second,
					Rules: []config.GuardrailRuleConfig{{Name: g.name, Stage: "pre", Mode: "sync", Action: "log", Timeout: 20 * time.Millisecond}}},
					map[string]Guardrail{g.name: g})
				plugin := NewPlugin(engine, map[string]Guardrail{g.name: g}, nil, nil, nil)
				rc := raceContext(passThrough)
				defer rc.Release()
				r := plugin.ProcessRequest(context.Background(), rc)
				readWhileRewriting(t, g, rc)
				assertRewriteRefused(t, r.Error, g.name)
				assertAsSent(t, rc)
			})

			t.Run("org check / "+name, func(t *testing.T) {
				g := newLateRewriter("late-org-rewriter")
				engine := NewEngine(config.GuardrailsConfig{FailOpen: failOpen, DefaultTimeout: time.Second}, nil)
				tenants := tenant.NewStore()
				tenants.Set("org-race", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{TimeoutMs: 20,
					Checks: map[string]*tenant.GuardrailCheck{g.name: {Enabled: true, Action: "log"}}}})
				plugin := NewPlugin(engine, map[string]Guardrail{g.name: g}, nil, nil, tenants)
				rc := raceContext(passThrough)
				defer rc.Release()
				r := plugin.ProcessRequest(context.Background(), rc)
				readWhileRewriting(t, g, rc)
				assertRewriteRefused(t, r.Error, g.name)
				assertAsSent(t, rc)
			})
		}
	}
}

// A rewriting rule in async mode runs on a copy, which is never written
// back: the request goes on as sent, and the check only logs.
func TestPlugin_AnAsyncRewriteNeverWritesTheRequest(t *testing.T) {
	g := newLateRewriter("async-rewriter")
	engine := NewEngine(config.GuardrailsConfig{DefaultTimeout: time.Second,
		Rules: []config.GuardrailRuleConfig{{Name: g.name, Stage: "pre", Mode: "async", Action: "log", Timeout: 20 * time.Millisecond}}},
		map[string]Guardrail{g.name: g})
	plugin := NewPlugin(engine, map[string]Guardrail{g.name: g}, nil, nil, nil)
	rc := raceContext(false)
	defer rc.Release()
	if r := plugin.ProcessRequest(context.Background(), rc); r.Error != nil {
		t.Fatalf("refused: %v", r.Error)
	}
	readWhileRewriting(t, g, rc)
	assertAsSent(t, rc)
}

// A rewriting check that finishes in time still has its rewrite applied:
// the copy it wrote is written back.
func TestPlugin_ARewriteThatFinishesIsWrittenBack(t *testing.T) {
	g := &quickRewriter{}
	engine := NewEngine(config.GuardrailsConfig{DefaultTimeout: time.Second,
		Rules: []config.GuardrailRuleConfig{{Name: "quick-rewriter", Stage: "pre", Mode: "sync", Action: "log"}}},
		map[string]Guardrail{"quick-rewriter": g})
	plugin := NewPlugin(engine, map[string]Guardrail{"quick-rewriter": g}, nil, nil, nil)
	for _, passThrough := range []bool{false, true} {
		rc := raceContext(passThrough)
		if r := plugin.ProcessRequest(context.Background(), rc); r.Error != nil {
			t.Fatalf("refused: %v", r.Error)
		}
		if passThrough {
			if got := string(rc.PassThrough.(*requestview.View).Body()); !strings.Contains(got, `"content":"masked"`) || !strings.Contains(got, `"input":{"ssn":"***"}`) {
				t.Errorf("body %s, want the rewrite written", got)
			}
		} else if string(rc.Request.Messages[0].Content) != `"masked"` || rc.Request.Messages[1].ToolCalls[0].Function.Arguments != `{"ssn":"***"}` {
			t.Errorf("request %+v, want the rewrite written back", rc.Request.Messages)
		}
		rc.Release()
	}
}

type quickRewriter struct{}

func (quickRewriter) Name() string          { return "quick-rewriter" }
func (quickRewriter) Stage() Stage          { return StagePre }
func (quickRewriter) RewritesRequest() bool { return true }
func (quickRewriter) Check(_ context.Context, input *CheckInput) *CheckResult {
	for i := range input.Request.Messages {
		if len(input.Request.Messages[i].Content) > 0 {
			input.Request.Messages[i].Content = json.RawMessage(`"masked"`)
		}
		for k := range input.Request.Messages[i].ToolCalls {
			input.Request.Messages[i].ToolCalls[k].Function.Arguments = `{"ssn":"***"}`
		}
	}
	return &CheckResult{Pass: false, Score: 1, Message: "masked"}
}

// A check that does not rewrite the request and outlives its timeout reads
// the request as it was when the check started, its own copy of the
// request's fields: a later check's rewrite, written back into the request
// (its messages), and what the gateway writes into the request after the
// checks (a field a later plugin sets) never reach it. Run with -race.

// lateReader does not rewrite the request. After its context ends it reads
// the request until stop is closed, then reads it once more and keeps what
// it read.
type lateReader struct {
	name    string
	stop    chan struct{}
	done    chan struct{}
	content string
	args    string
	model   string
}

func newLateReader(name string) *lateReader {
	return &lateReader{name: name, stop: make(chan struct{}), done: make(chan struct{})}
}

func (g *lateReader) Name() string { return g.name }
func (g *lateReader) Stage() Stage { return StagePre }

func (g *lateReader) Check(ctx context.Context, input *CheckInput) *CheckResult {
	defer close(g.done)
	<-ctx.Done()
	for {
		select {
		case <-g.stop:
			g.content = string(input.Request.Messages[0].Content)
			g.args = input.Request.Messages[1].ToolCalls[0].Function.Arguments
			g.model = input.Request.Model
			return &CheckResult{Pass: true}
		default:
		}
		_, _ = json.Marshal(input.Request)
	}
}

func TestPlugin_ACheckThatTimesOutReadsTheRequestAsItStarted(t *testing.T) {
	for _, passThrough := range []bool{false, true} {
		for _, rewriter := range []string{"a config rule", "an org check"} {
			name := map[bool]string{false: "chat", true: "passed through"}[passThrough] + " / then " + rewriter + " rewrites"
			t.Run(name, func(t *testing.T) {
				r := newLateReader("late-reader")
				q := quickRewriter{}
				rules := []config.GuardrailRuleConfig{{Name: r.name, Stage: "pre", Mode: "sync", Action: "log", Timeout: 20 * time.Millisecond}}
				tenants := tenant.NewStore()
				if rewriter == "a config rule" {
					rules = append(rules, config.GuardrailRuleConfig{Name: q.Name(), Stage: "pre", Mode: "sync", Action: "log"})
				} else {
					tenants.Set("org-race", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{TimeoutMs: 1000,
						Checks: map[string]*tenant.GuardrailCheck{q.Name(): {Enabled: true, Action: "log"}}}})
				}
				registry := map[string]Guardrail{r.name: r, q.Name(): q}
				engine := NewEngine(config.GuardrailsConfig{FailOpen: true, DefaultTimeout: time.Second, Rules: rules}, registry)
				plugin := NewPlugin(engine, registry, nil, nil, tenants)
				rc := raceContext(passThrough)
				defer rc.Release()
				if res := plugin.ProcessRequest(context.Background(), rc); res.Error != nil {
					t.Fatalf("refused: %v", res.Error)
				}
				// The rewrite is in the request; a later plugin sets a field.
				if passThrough {
					if got := string(rc.PassThrough.(*requestview.View).Body()); !strings.Contains(got, `"content":"masked"`) {
						t.Errorf("body %s, want the rewrite written", got)
					}
				} else {
					if got := string(rc.Request.Messages[0].Content); got != `"masked"` {
						t.Errorf("message content %s, want the rewrite written back", got)
					}
					rc.Request.Model = "routed-model"
				}
				close(r.stop)
				select {
				case <-r.done:
				case <-time.After(5 * time.Second):
					t.Fatal("the check never finished")
				}
				if r.content != raceSent || r.args != `{"ssn":"123-45-6789"}` {
					t.Errorf("the check that timed out read content %s and arguments %s, want the request as it started (%s)", r.content, r.args, raceSent)
				}
				if !passThrough && r.model != "gpt-4o" {
					t.Errorf("the check that timed out read model %q, set after the checks; want gpt-4o", r.model)
				}
			})
		}
	}

	// An organization's check that does not rewrite, timed out, does not
	// read a field set after the checks either.
	t.Run("org check / chat", func(t *testing.T) {
		r := newLateReader("late-org-reader")
		engine := NewEngine(config.GuardrailsConfig{FailOpen: true, DefaultTimeout: time.Second}, nil)
		tenants := tenant.NewStore()
		tenants.Set("org-race", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{TimeoutMs: 20,
			Checks: map[string]*tenant.GuardrailCheck{r.name: {Enabled: true, Action: "log"}}}})
		plugin := NewPlugin(engine, map[string]Guardrail{r.name: r}, nil, nil, tenants)
		rc := raceContext(false)
		defer rc.Release()
		if res := plugin.ProcessRequest(context.Background(), rc); res.Error != nil {
			t.Fatalf("refused: %v", res.Error)
		}
		rc.Request.Model = "routed-model"
		close(r.stop)
		<-r.done
		if r.model != "gpt-4o" || r.content != raceSent {
			t.Errorf("the check that timed out read model %q and content %s, want the request as it started", r.model, r.content)
		}
	})
}
