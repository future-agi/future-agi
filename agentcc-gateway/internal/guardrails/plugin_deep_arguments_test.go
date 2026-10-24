package guardrails_test

import (
	"context"
	"encoding/json"
	"strings"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/pii"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/requestview"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A request whose tool calls' arguments are nested deeper than the checks
// read them is input the gateway cannot read: a check on the request that
// would block it or rewrite it refuses it before the provider is called
// (403 request_unavailable), within the check's timeout, with fail_open on
// or off; a check that only logs lets it go on, with request_unavailable in
// its metadata, and returns at once.

const deepSSN = "123-45-6789"

// deepArguments is arguments depth objects deep with deepSSN at the
// innermost level.
func deepArguments(depth int) string {
	return strings.Repeat(`{"a":`, depth) + `"` + deepSSN + `"` + strings.Repeat("}", depth)
}

// deepChat is a chat request whose only social security number is at the
// innermost level of three tool calls' arguments, depth objects deep.
func deepChat(depth int) *models.ChatCompletionRequest {
	user, _ := json.Marshal("Look my record up.")
	var calls []models.ToolCall
	for i := 0; i < 3; i++ {
		calls = append(calls, models.ToolCall{ID: "c", Type: "function",
			Function: models.FunctionCall{Name: "lookup", Arguments: deepArguments(depth)}})
	}
	return &models.ChatCompletionRequest{Model: "gpt-4o", Messages: []models.Message{
		{Role: "user", Content: user},
		{Role: "assistant", ToolCalls: calls},
	}}
}

// deepBody is the same request as an Anthropic body passed through to the
// provider: three tool_use blocks whose input is depth objects deep.
func deepBody(depth int) []byte {
	block := `{"type":"tool_use","id":"t","name":"lookup","input":` + deepArguments(depth) + `}`
	return []byte(`{"model":"claude-x","max_tokens":8,"messages":[{"role":"user","content":"Look my record up."},` +
		`{"role":"assistant","content":[` + block + `,` + block + `,` + block + `]}]}`)
}

func deepContext(passThrough bool, depth int) *models.RequestContext {
	rc := models.AcquireRequestContext()
	rc.Metadata["org_id"] = "org-deep"
	if passThrough {
		rc.PassThrough = requestview.Anthropic(deepBody(depth))
	} else {
		rc.Request = deepChat(depth)
	}
	return rc
}

// watched is a check that counts the calls of its Check that have not
// returned.
type watched struct {
	guardrails.Guardrail
	running atomic.Int32
}

func (w *watched) Check(ctx context.Context, in *guardrails.CheckInput) *guardrails.CheckResult {
	w.running.Add(1)
	defer w.running.Add(-1)
	return w.Guardrail.Check(ctx, in)
}

func (w *watched) RewritesRequest() bool {
	r, ok := w.Guardrail.(guardrails.RequestRewriter)
	return ok && r.RewritesRequest()
}

// assertStopped: within a second of the plugin returning, no call of the
// check is still running, so the check uses no more of the gateway's CPU
// for this request.
func assertStopped(t *testing.T, w *watched) {
	t.Helper()
	cpu := processCPU()
	deadline := time.Now().Add(time.Second)
	for w.running.Load() != 0 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if n := w.running.Load(); n != 0 {
		t.Errorf("%d call(s) of the check still running a second after the plugin returned (CPU in that second: %.2fs)", n, processCPU()-cpu)
	}
}

func processCPU() float64 {
	var ru syscall.Rusage
	_ = syscall.Getrusage(syscall.RUSAGE_SELF, &ru)
	return time.Duration(ru.Utime.Nano() + ru.Stime.Nano()).Seconds()
}

const deepTimeout = time.Second

// deepPlugin is the plugin with pii-detection (config, action) as a rule in
// the config file at the pre stage, or as the organization's check.
func deepPlugin(orgCheck, failOpen bool, action string, cfg map[string]interface{}) (*guardrails.GuardrailPlugin, *watched) {
	w := &watched{Guardrail: pii.New(cfg)}
	registry := map[string]guardrails.Guardrail{"pii-detection": w}
	gcfg := config.GuardrailsConfig{FailOpen: failOpen, DefaultTimeout: deepTimeout}
	if !orgCheck {
		gcfg.Rules = []config.GuardrailRuleConfig{{Name: "pii-detection", Stage: "pre", Mode: "sync", Action: action}}
		return guardrails.NewPlugin(guardrails.NewEngine(gcfg, registry), registry, nil, nil, nil), w
	}
	tenants := tenant.NewStore()
	tenants.Set("org-deep", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{
		Checks: map[string]*tenant.GuardrailCheck{"pii-detection": {Enabled: true, Action: action}}}})
	return guardrails.NewPlugin(guardrails.NewEngine(gcfg, nil), registry, nil, nil, tenants), w
}

var deepShapes = []struct {
	name     string
	orgCheck bool
}{{"config rule", false}, {"org check", true}}

func deepName(shape string, passThrough, failOpen bool) string {
	name := shape + map[bool]string{false: " / chat", true: " / passed through"}[passThrough]
	return name + map[bool]string{true: " / fail_open", false: " / fail closed"}[failOpen]
}

// Under a check in block mode the request never goes on: it is refused as
// unreadable, or read and blocked, within the check's timeout.
func TestPlugin_DeeplyNestedArgumentsAreRefusedUnderABlockingCheck(t *testing.T) {
	for _, shape := range deepShapes {
		for _, passThrough := range []bool{false, true} {
			for _, failOpen := range []bool{true, false} {
				t.Run(deepName(shape.name, passThrough, failOpen), func(t *testing.T) {
					plugin, w := deepPlugin(shape.orgCheck, failOpen, "block", nil)
					rc := deepContext(passThrough, 9000)
					defer rc.Release()
					start := time.Now()
					r := plugin.ProcessRequest(context.Background(), rc)
					elapsed := time.Since(start)
					switch {
					case r.Error == nil:
						t.Errorf("the request went on after %v: a social security number in its tool calls' arguments passed pii-detection in block mode", elapsed)
					case r.Error.Code != "request_unavailable" && r.Error.Code != "content_blocked":
						t.Errorf("refused with %s (%s), want request_unavailable or content_blocked", r.Error.Code, r.Error.Message)
					case strings.Contains(r.Error.Message, "timed out"):
						t.Errorf("refused because the check timed out (%s), not because it read the request", r.Error.Message)
					}
					if elapsed >= deepTimeout {
						t.Errorf("the plugin returned after %v, want it within the check's timeout (%v)", elapsed, deepTimeout)
					}
					assertStopped(t, w)
				})
			}
		}
	}
}

// Under a check that rewrites the request (pii-detection's mask), the
// request is refused as unreadable, since a rewrite of what the gateway
// cannot read could never be applied, and its arguments stay as sent.
func TestPlugin_DeeplyNestedArgumentsAreRefusedUnderARewritingCheck(t *testing.T) {
	for _, shape := range deepShapes {
		for _, passThrough := range []bool{false, true} {
			t.Run(deepName(shape.name, passThrough, true), func(t *testing.T) {
				plugin, w := deepPlugin(shape.orgCheck, true, "log", map[string]interface{}{"remediation": "mask"})
				rc := deepContext(passThrough, 9000)
				defer rc.Release()
				r := plugin.ProcessRequest(context.Background(), rc)
				if r.Error == nil || r.Error.Code != "request_unavailable" || !strings.Contains(r.Error.Message, "pii-detection") {
					t.Errorf("got %+v, want 403 request_unavailable naming pii-detection", r.Error)
				}
				if !passThrough {
					for _, tc := range rc.Request.Messages[1].ToolCalls {
						if tc.Function.Arguments != deepArguments(9000) {
							t.Errorf("the arguments changed: %.80s...", tc.Function.Arguments)
						}
					}
				}
				assertStopped(t, w)
			})
		}
	}
}

// Under a check that only logs, the request goes on, at once, with
// request_unavailable in its metadata, and the check does not go on reading
// it.
func TestPlugin_DeeplyNestedArgumentsGoOnUnderACheckThatOnlyLogs(t *testing.T) {
	for _, shape := range deepShapes {
		for _, passThrough := range []bool{false, true} {
			t.Run(deepName(shape.name, passThrough, true), func(t *testing.T) {
				plugin, w := deepPlugin(shape.orgCheck, true, "log", nil)
				rc := deepContext(passThrough, 9000)
				defer rc.Release()
				start := time.Now()
				r := plugin.ProcessRequest(context.Background(), rc)
				elapsed := time.Since(start)
				if r.Error != nil {
					t.Fatalf("refused under a check that only logs: %+v", r.Error)
				}
				if elapsed >= deepTimeout {
					t.Errorf("the plugin returned after %v, want it within the check's timeout (%v)", elapsed, deepTimeout)
				}
				if rc.Metadata["request_unavailable"] == "" {
					t.Errorf("metadata %v, want request_unavailable", rc.Metadata)
				}
				assertStopped(t, w)
			})
		}
	}
}

// Arguments nested as deep as the checks read are read: the controls.
func TestPlugin_ArgumentsAtTheDepthLimitAreRead(t *testing.T) {
	for _, shape := range deepShapes {
		for _, passThrough := range []bool{false, true} {
			t.Run(deepName(shape.name, passThrough, true)+" / block", func(t *testing.T) {
				plugin, _ := deepPlugin(shape.orgCheck, true, "block", nil)
				rc := deepContext(passThrough, 64)
				defer rc.Release()
				r := plugin.ProcessRequest(context.Background(), rc)
				if r.Error == nil || r.Error.Code != "content_blocked" {
					t.Errorf("got %+v, want the check to read the arguments and block them (content_blocked)", r.Error)
				}
			})
			t.Run(deepName(shape.name, passThrough, true)+" / mask", func(t *testing.T) {
				plugin, _ := deepPlugin(shape.orgCheck, true, "log", map[string]interface{}{"remediation": "mask"})
				rc := deepContext(passThrough, 64)
				defer rc.Release()
				if r := plugin.ProcessRequest(context.Background(), rc); r.Error != nil {
					t.Fatalf("refused: %+v", r.Error)
				}
				var sent string
				if passThrough {
					sent = string(rc.PassThrough.(*requestview.View).Body())
				} else {
					for _, tc := range rc.Request.Messages[1].ToolCalls {
						sent += tc.Function.Arguments
					}
				}
				if strings.Contains(sent, deepSSN) || strings.Count(sent, `"***"`) != 3 {
					t.Errorf("the request the provider gets holds the number or not three masks: %.300s", sent)
				}
			})
		}
	}
}
