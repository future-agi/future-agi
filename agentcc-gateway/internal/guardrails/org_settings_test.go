package guardrails_test

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	g "github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/pii"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/webhook"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type acceptanceCheck struct {
	name    string
	mutates bool
	fn      func(context.Context, *g.CheckInput) *g.CheckResult
}

func (c acceptanceCheck) MutatesInput() bool { return c.mutates }
func (c acceptanceCheck) Name() string       { return c.name }
func (c acceptanceCheck) Stage() g.Stage     { return g.StagePre }
func (c acceptanceCheck) Check(ctx context.Context, in *g.CheckInput) *g.CheckResult {
	return c.fn(ctx, in)
}

func acceptancePlugin(t *testing.T, global bool, raw string, registry map[string]g.Guardrail) *g.GuardrailPlugin {
	t.Helper()
	var gc tenant.GuardrailConfig
	if err := json.Unmarshal([]byte(raw), &gc); err != nil {
		t.Fatal(err)
	}
	store := tenant.NewStore()
	store.Set("synthetic-org", &tenant.OrgConfig{Guardrails: &gc})
	engine := g.NewEngine(config.GuardrailsConfig{FailOpen: global, DefaultTimeout: time.Second}, nil)
	return g.NewPlugin(engine, registry, nil, nil, store)
}
func acceptanceRC() *models.RequestContext {
	rc := models.AcquireRequestContext()
	rc.Metadata["org_id"] = "synthetic-org"
	rc.Request = &models.ChatCompletionRequest{Model: "synthetic-model", Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"synthetic text"`)}}}
	return rc
}
func assertDecision(t *testing.T, p *g.GuardrailPlugin, wantBlock bool, status int) {
	t.Helper()
	rc := acceptanceRC()
	defer rc.Release()
	res := p.ProcessRequest(context.Background(), rc)
	blocked := res.Action == pipeline.ShortCircuit
	if blocked != wantBlock {
		t.Fatalf("blocked=%v want=%v result=%+v", blocked, wantBlock, res)
	}
	if wantBlock && (res.Error == nil || res.Error.Status != status) {
		t.Fatalf("expected %d guardrail refusal, got %+v", status, res.Error)
	}
}

// The policy assertions distinguish explicit organization choice from an
// operational failure. A genuine score exactly at the threshold still passes.
func TestWQ2FailurePolicy(t *testing.T) {
	for _, global := range []bool{false, true} {
		for _, org := range []bool{false, true} {
			for _, threshold := range []float64{0.5, 1} {
				for _, kind := range []string{"timeout", "panic", "nil"} {
					t.Run(fmt.Sprintf("global=%v/org=%v/threshold=%g/%s", global, org, threshold, kind), func(t *testing.T) {
						check := acceptanceCheck{name: "check", fn: func(ctx context.Context, in *g.CheckInput) *g.CheckResult {
							switch kind {
							case "timeout":
								<-ctx.Done()
								return nil
							case "panic":
								panic("synthetic failure")
							default:
								return nil
							}
						}}
						raw := fmt.Sprintf(`{"fail_open":%v,"timeout_ms":30,"checks":{"check":{"enabled":true,"action":"block","confidence_threshold":%g}}}`, org, threshold)
						assertDecision(t, acceptancePlugin(t, global, raw, map[string]g.Guardrail{"check": check}), !org, 503)
					})
				}
			}
		}
	}
}

func TestWQ2OmittedPolicyStaysClosed(t *testing.T) {
	check := acceptanceCheck{name: "check", fn: func(ctx context.Context, in *g.CheckInput) *g.CheckResult { panic("synthetic failure") }}
	p := acceptancePlugin(t, true, `{"checks":{"check":{"enabled":true,"action":"block","confidence_threshold":0.5}}}`, map[string]g.Guardrail{"check": check})
	assertDecision(t, p, true, 503)
}

func TestWQ2WebhookPolicy(t *testing.T) {
	for _, org := range []bool{false, true} {
		for _, threshold := range []float64{0.5, 1} {
			for _, kind := range []string{"operational", "safe", "reject", "at-threshold"} {
				t.Run(fmt.Sprintf("org=%v/threshold=%g/%s", org, threshold, kind), func(t *testing.T) {
					var requests atomic.Int32
					srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
						requests.Add(1)
						if kind == "operational" {
							w.WriteHeader(503)
							return
						}
						w.Header().Set("Content-Type", "application/json")
						switch kind {
						case "safe":
							fmt.Fprint(w, `{"pass":true,"score":0}`)
						case "at-threshold":
							fmt.Fprintf(w, `{"pass":false,"score":%g}`, threshold)
						default:
							fmt.Fprint(w, `{"pass":false,"score":1}`)
						}
					}))
					defer srv.Close()
					raw := fmt.Sprintf(`{"fail_open":%v,"checks":{"hook":{"enabled":true,"action":"block","confidence_threshold":%g}}}`, org, threshold)
					p := acceptancePlugin(t, !org, raw, map[string]g.Guardrail{"hook": webhook.New("hook", map[string]interface{}{"url": srv.URL})})
					want := kind == "operational" && !org || kind == "reject" && threshold < 1
					status := 403
					if kind == "operational" {
						status = 503
					}
					assertDecision(t, p, want, status)
					if n := requests.Load(); n != 1 {
						t.Fatalf("local webhook requests=%d want=1", n)
					}
				})
			}
		}
	}
}

func TestWQ2Mode(t *testing.T) {
	for _, mode := range []string{"parallel", "sequential", ""} {
		t.Run(mode, func(t *testing.T) {
			var active, peak atomic.Int32
			check := func(name string) g.Guardrail {
				return acceptanceCheck{name: name, fn: func(ctx context.Context, in *g.CheckInput) *g.CheckResult {
					n := active.Add(1)
					defer active.Add(-1)
					for {
						old := peak.Load()
						if old >= n || peak.CompareAndSwap(old, n) {
							break
						}
					}
					select {
					case <-time.After(40 * time.Millisecond):
					case <-ctx.Done():
					}
					return &g.CheckResult{Pass: true}
				}}
			}
			raw := fmt.Sprintf(`{"pipeline_mode":%q,"fail_open":false,"checks":{"a":{"enabled":true,"action":"block"},"b":{"enabled":true,"action":"block"}}}`, mode)
			p := acceptancePlugin(t, false, raw, map[string]g.Guardrail{"a": check("a"), "b": check("b")})
			assertDecision(t, p, false, 403)
			want := int32(2)
			if mode == "sequential" {
				want = 1
			}
			if n := peak.Load(); n != want {
				t.Fatalf("peak concurrency=%d want=%d for mode %q", n, want, mode)
			}
		})
	}
}

// A completed warning is not a cancellation artifact and must remain in the
// audit result when a slower parallel check blocks.
func TestWQ2CompletedWarningRetained(t *testing.T) {
	warnDone := make(chan struct{})
	registry := map[string]g.Guardrail{
		"a-warn": acceptanceCheck{name: "a-warn", fn: func(context.Context, *g.CheckInput) *g.CheckResult {
			close(warnDone)
			return &g.CheckResult{Pass: false, Score: 0.9, Message: "completed warning"}
		}},
		"b-block": acceptanceCheck{name: "b-block", fn: func(context.Context, *g.CheckInput) *g.CheckResult {
			<-warnDone
			time.Sleep(30 * time.Millisecond)
			return &g.CheckResult{Pass: false, Score: 0.9, Message: "blocked"}
		}},
	}
	p := acceptancePlugin(t, false, `{"pipeline_mode":"parallel","checks":{"a-warn":{"enabled":true,"action":"warn","confidence_threshold":0.5},"b-block":{"enabled":true,"action":"block","confidence_threshold":0.5}}}`, registry)
	rc := acceptanceRC()
	defer rc.Release()
	res := p.ProcessRequest(context.Background(), rc)
	if res.Action != pipeline.ShortCircuit {
		t.Fatal("expected block")
	}
	if rc.Metadata["guardrail_name"] != "b-block" || !strings.Contains(res.Error.Message, "b-block") {
		t.Fatalf("block attribution=%v error=%v", rc.Metadata, res.Error)
	}
	found := false
	for _, r := range rc.GuardrailResults {
		if r.Name == "a-warn" {
			found = true
		}
	}
	if !found {
		t.Fatal("completed warning erased when another parallel check blocked")
	}
}

// These are real built-in PII and webhook implementations. PII remediation
// writes message Content while webhook serialization reads it. The wrapper
// only schedules their overlap; it does not mutate production inputs itself.
func TestWQ2ParallelPIIRemediation(t *testing.T) {
	var wg sync.WaitGroup
	var requests atomic.Int32
	var received string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests.Add(1)
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Error(err)
		}
		received = string(body)
		fmt.Fprint(w, `{"pass":true,"score":0}`)
	}))
	defer srv.Close()
	redactor := pii.New(map[string]interface{}{"remediation": "redact"})
	hook := webhook.New("hook", map[string]interface{}{"url": srv.URL})
	start := make(chan struct{})
	var entered atomic.Int32
	wrap := func(name string, inner g.Guardrail) g.Guardrail {
		return acceptanceCheck{name: name, mutates: name == "pii-detection", fn: func(ctx context.Context, in *g.CheckInput) *g.CheckResult {
			defer wg.Done()
			if entered.Add(1) == 2 {
				close(start)
			}
			select {
			case <-start:
			case <-time.After(60 * time.Millisecond):
			}
			return inner.Check(ctx, in)
		}}
	}
	registry := map[string]g.Guardrail{"pii-detection": wrap("pii-detection", redactor), "hook": wrap("hook", hook)}
	p := acceptancePlugin(t, false, `{"pipeline_mode":"parallel","checks":{"pii-detection":{"enabled":true,"action":"warn","confidence_threshold":0.5},"hook":{"enabled":true,"action":"block","confidence_threshold":0.5}}}`, registry)
	rc := acceptanceRC()
	defer rc.Release()
	rc.Request.Messages = make([]models.Message, 1000)
	for i := range rc.Request.Messages {
		rc.Request.Messages[i] = models.Message{Role: "user", Content: json.RawMessage(`"reach synthetic@example.test for help"`)}
	}
	wg.Add(2)
	res := p.ProcessRequest(context.Background(), rc)
	wg.Wait()
	if res.Action != pipeline.Continue {
		t.Fatalf("remediation should continue: %+v", res)
	}
	for i, m := range rc.Request.Messages {
		if strings.Contains(string(m.Content), "synthetic@example.test") {
			t.Fatalf("message %d not redacted", i)
		}
	}
	if requests.Load() != 1 {
		t.Fatalf("local webhook calls=%d", requests.Load())
	}
	if strings.Contains(received, "synthetic@example.test") || !strings.Contains(received, "[REDACTED") {
		t.Fatalf("outgoing webhook body was not remediated: %.300s", received)
	}
}

func TestCompletedWarningRetentionIndependently(t *testing.T) {
	warnDone := make(chan struct{})
	registry := map[string]g.Guardrail{
		"a-warn": acceptanceCheck{name: "a-warn", fn: func(context.Context, *g.CheckInput) *g.CheckResult {
			close(warnDone)
			return &g.CheckResult{Pass: false, Score: 0.9, Message: "completed warning"}
		}},
		"b-block": acceptanceCheck{name: "b-block", fn: func(context.Context, *g.CheckInput) *g.CheckResult {
			<-warnDone
			time.Sleep(30 * time.Millisecond)
			return &g.CheckResult{Pass: false, Score: 0.9, Message: "blocked"}
		}},
	}
	p := acceptancePlugin(t, false, `{"pipeline_mode":"parallel","checks":{"a-warn":{"enabled":true,"action":"warn","confidence_threshold":0.5},"b-block":{"enabled":true,"action":"block","confidence_threshold":0.5}}}`, registry)
	rc := acceptanceRC()
	defer rc.Release()
	p.ProcessRequest(context.Background(), rc)
	found := false
	for _, result := range rc.GuardrailResults {
		if result.Name == "a-warn" {
			found = true
		}
	}
	if !found {
		t.Fatal("completed warning erased when another parallel check blocked")
	}
}

func TestStaticWebhookExecutionPolicy(t *testing.T) {
	for _, open := range []bool{false, true} {
		for _, threshold := range []float64{0.5, 1} {
			for _, kind := range []string{"unavailable", "content", "safe"} {
				t.Run(fmt.Sprintf("open=%v/threshold=%g/%s", open, threshold, kind), func(t *testing.T) {
					srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
						if kind == "unavailable" {
							w.WriteHeader(503)
							return
						}
						if kind == "content" {
							fmt.Fprint(w, `{"pass":false}`)
						} else {
							fmt.Fprint(w, `{"pass":true}`)
						}
					}))
					defer srv.Close()
					engine := g.NewEngine(config.GuardrailsConfig{FailOpen: open, Rules: []config.GuardrailRuleConfig{{Name: "hook", Stage: "pre", Mode: "sync", Action: "block", Threshold: threshold}}}, map[string]g.Guardrail{"hook": webhook.New("hook", map[string]interface{}{"url": srv.URL})})
					rc := acceptanceRC()
					defer rc.Release()
					result := engine.RunPre(context.Background(), &g.CheckInput{Request: rc.Request}, nil, policy.RequestPolicyNone)
					want := kind == "content" || kind == "unavailable" && !open
					if result.Blocked != want {
						t.Fatalf("blocked=%v want=%v", result.Blocked, want)
					}
					if kind == "unavailable" && (len(result.Triggered) != 1 || !strings.Contains(result.Triggered[0].Message, "guardrail_unavailable")) {
						t.Fatalf("unavailable outcome not recorded: %+v", result)
					}
				})
			}
		}
	}
}
