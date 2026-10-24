package guardrails

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"reflect"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// passThrough is a models.PassThroughRequest: the body read into req, or
// reason when it cannot be read. reads counts the reads, and applies the
// calls to ApplyChecks, which returns applyErr.
type passThrough struct {
	req      *models.ChatCompletionRequest
	reason   string
	reads    int
	applies  int
	applyErr error
}

func (p *passThrough) ChatRequest(context.Context) (*models.ChatCompletionRequest, string) {
	p.reads++
	if p.reason != "" {
		return nil, p.reason
	}
	return p.req, ""
}

func (p *passThrough) ApplyChecks() error {
	p.applies++
	return p.applyErr
}

func chatOf(text string) *models.ChatCompletionRequest {
	content, _ := json.Marshal(text)
	return &models.ChatCompletionRequest{Messages: []models.Message{{Role: "user", Content: content}}}
}

// requestSeen records the request a check was given, at its stage.
type requestSeen struct {
	name  string
	stage Stage
	seen  *models.ChatCompletionRequest
}

func (g *requestSeen) Name() string { return g.name }
func (g *requestSeen) Stage() Stage { return g.stage }
func (g *requestSeen) Check(_ context.Context, in *CheckInput) *CheckResult {
	g.seen = in.Request
	return &CheckResult{Pass: true}
}

// BlockingPre names the pre-stage rules that would refuse a request, as
// BlockingPost does for the answer.
func TestEngine_BlockingPre(t *testing.T) {
	check := &mockGuardrail{name: "request-check", stage: StagePre, result: &CheckResult{Pass: true}}
	registry := map[string]Guardrail{"request-check": check}
	engine := func(action, mode string) *Engine {
		return NewEngine(config.GuardrailsConfig{DefaultTimeout: time.Second, Rules: []config.GuardrailRuleConfig{
			{Name: "request-check", Stage: "pre", Mode: mode, Action: action, Threshold: 0.5}}}, registry)
	}
	for _, tc := range []struct {
		name   string
		engine *Engine
		key    *policy.Policy
		req    policy.RequestPolicy
		want   []string
	}{
		{"a block rule", engine("block", "sync"), nil, policy.RequestPolicyNone, []string{"request-check"}},
		{"a log rule", engine("log", "sync"), nil, policy.RequestPolicyNone, nil},
		{"an async block rule only logs", engine("block", "async"), nil, policy.RequestPolicyNone, nil},
		{"the key's policy disables it", engine("block", "sync"),
			&policy.Policy{Overrides: map[string]policy.Override{"request-check": {Disabled: true}}}, policy.RequestPolicyNone, nil},
		{"X-Guardrail-Policy log-only", engine("block", "sync"), nil, policy.RequestPolicyLogOnly, nil},
		{"X-Guardrail-Policy strict blocks a log rule", engine("log", "sync"), nil, policy.RequestPolicyStrict, []string{"request-check"}},
		{"a post rule is not a check on the request", NewEngine(config.GuardrailsConfig{DefaultTimeout: time.Second,
			Rules: []config.GuardrailRuleConfig{{Name: "request-check", Stage: "post", Mode: "sync", Action: "block"}}}, registry),
			nil, policy.RequestPolicyNone, nil},
		{"no engine", nil, nil, policy.RequestPolicyNone, nil},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := tc.engine.BlockingPre(tc.key, tc.req); !slices.Equal(got, tc.want) {
				t.Errorf("BlockingPre = %v, want %v", got, tc.want)
			}
		})
	}
}

// The checks read a request whose body the handler passes through, at both
// stages, from rc.PassThrough, as they read rc.Request; rc.Request wins when
// both are set. A check on the request reads its own copy of the request's
// fields (isolate), so what it read is compared by value.
func TestPlugin_ChecksReadAPassThroughRequest(t *testing.T) {
	pre := &requestSeen{name: "pre-check", stage: StagePre}
	post := &requestSeen{name: "post-check", stage: StagePost}
	org := &requestSeen{name: "org-check", stage: StagePre}
	registry := map[string]Guardrail{"pre-check": pre, "post-check": post, "org-check": org}
	engine := NewEngine(config.GuardrailsConfig{DefaultTimeout: time.Second, Rules: []config.GuardrailRuleConfig{
		{Name: "pre-check", Stage: "pre", Mode: "sync", Action: "block", Threshold: 0.5},
		{Name: "post-check", Stage: "post", Mode: "sync", Action: "block", Threshold: 0.5},
	}}, registry)
	tenants := tenant.NewStore()
	tenants.Set("org-pt", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{
		"org-check": {Enabled: true, Action: "block"}}}})
	plugin := NewPlugin(engine, registry, nil, nil, tenants)

	body := &passThrough{req: chatOf("passed through")}
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Metadata["org_id"] = "org-pt"
	rc.PassThrough = body
	if r := plugin.ProcessRequest(context.Background(), rc); r.Error != nil {
		t.Fatalf("refused: %v", r.Error)
	}
	rc.Response = &models.ChatCompletionResponse{}
	plugin.ProcessResponse(context.Background(), rc)
	for _, g := range []*requestSeen{pre, org, post} {
		if !reflect.DeepEqual(g.seen, body.req) {
			t.Errorf("%s read %v, want the pass-through request", g.name, g.seen)
		}
	}

	chat := chatOf("the chat request")
	rc.Request = chat
	plugin.ProcessRequest(context.Background(), rc)
	if !reflect.DeepEqual(pre.seen, chat) {
		t.Errorf("with rc.Request set the check read %v, want rc.Request", pre.seen)
	}
}

// A pass-through body is not read when no check reads the request.
func TestPlugin_ReadsAPassThroughBodyOnlyWhenACheckReadsIt(t *testing.T) {
	t.Run("no check", func(t *testing.T) {
		body := &passThrough{req: chatOf("x")}
		rc := models.AcquireRequestContext()
		defer rc.Release()
		rc.PassThrough = body
		NewPlugin(nil, nil, nil, nil, tenant.NewStore()).ProcessRequest(context.Background(), rc)
		if body.reads != 0 {
			t.Errorf("the body was read %d times with no check, want 0", body.reads)
		}
	})
}

// unreadablePlugin is a plugin whose org has one check on the request, in
// action's mode.
func unreadablePlugin(action string) *GuardrailPlugin {
	registry := map[string]Guardrail{
		"request-check": &mockGuardrail{name: "request-check", stage: StagePre, result: &CheckResult{Pass: true}},
	}
	tenants := tenant.NewStore()
	tenants.Set("org-unreadable", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{
		"request-check": {Enabled: true, Action: action},
	}}})
	return NewPlugin(nil, registry, nil, nil, tenants)
}

// A pass-through body the gateway cannot read is refused before the
// provider call when a check on the request would block it, with 403
// request_unavailable that names the check and says why; with the check in
// another mode it goes on, and the reason is in the request's metadata.
func TestPlugin_RefusesAnUnreadablePassThroughRequest(t *testing.T) {
	const reason = "the gateway cannot read this request: messages is not a list"
	t.Run("a blocking check on the request", func(t *testing.T) {
		rc := models.AcquireRequestContext()
		defer rc.Release()
		rc.Metadata["org_id"] = "org-unreadable"
		rc.PassThrough = &passThrough{reason: reason}
		apiErr := unreadablePlugin("block").ProcessRequest(context.Background(), rc).Error
		if apiErr == nil || apiErr.Status != http.StatusForbidden || apiErr.Code != "request_unavailable" {
			t.Fatalf("result error %v, want 403 request_unavailable", apiErr)
		}
		for _, want := range []string{"request-check", "checks the request", reason} {
			if !strings.Contains(apiErr.Message, want) {
				t.Errorf("message %q does not say %q", apiErr.Message, want)
			}
		}
		if !rc.Flags.GuardrailTriggered || rc.Metadata["guardrail_action"] != "blocked" ||
			rc.Metadata["guardrail_name"] != "request-check" || rc.Metadata["request_unavailable"] != reason {
			t.Errorf("the block is not recorded: triggered %v, metadata %v", rc.Flags.GuardrailTriggered, rc.Metadata)
		}
	})
	t.Run("the check in warn mode", func(t *testing.T) {
		rc := models.AcquireRequestContext()
		defer rc.Release()
		rc.Metadata["org_id"] = "org-unreadable"
		rc.PassThrough = &passThrough{reason: reason}
		if r := unreadablePlugin("warn").ProcessRequest(context.Background(), rc); r.Error != nil {
			t.Fatalf("refused: %v", r.Error)
		}
		if rc.Metadata["request_unavailable"] != reason {
			t.Errorf("metadata %v, want request_unavailable", rc.Metadata)
		}
	})
	t.Run("a readable body", func(t *testing.T) {
		rc := models.AcquireRequestContext()
		defer rc.Release()
		rc.Metadata["org_id"] = "org-unreadable"
		rc.PassThrough = &passThrough{req: chatOf("fine")}
		if r := unreadablePlugin("block").ProcessRequest(context.Background(), rc); r.Error != nil {
			t.Fatalf("refused: %v", r.Error)
		}
	})
}

// rewriter is a check on the request that rewrites it (RequestRewriter)
// when rewrites is set, and refuses it when block is set.
type rewriter struct {
	name     string
	rewrites bool
	block    bool
}

func (g *rewriter) Name() string          { return g.name }
func (g *rewriter) Stage() Stage          { return StagePre }
func (g *rewriter) RewritesRequest() bool { return g.rewrites }
func (g *rewriter) Check(context.Context, *CheckInput) *CheckResult {
	if g.block {
		return &CheckResult{Pass: false, Score: 1, Message: "refused"}
	}
	return &CheckResult{Pass: true}
}

// After the checks on the request pass, the plugin writes their rewrite into
// the pass-through body (ApplyChecks), once; a request they refuse is not
// written. A rewrite that cannot be written refuses the request before the
// provider call, with 403 remediation_unavailable naming the check that
// rewrites requests.
func TestPlugin_WritesTheChecksRewriteIntoThePassThroughBody(t *testing.T) {
	newPlugin := func(checks map[string]*tenant.GuardrailCheck, registry map[string]Guardrail, rules ...config.GuardrailRuleConfig) *GuardrailPlugin {
		tenants := tenant.NewStore()
		tenants.Set("org-rw", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{Checks: checks}})
		engine := NewEngine(config.GuardrailsConfig{DefaultTimeout: time.Second, Rules: rules}, registry)
		return NewPlugin(engine, registry, nil, nil, tenants)
	}
	ctx := context.Background()
	newContext := func(body *passThrough) *models.RequestContext {
		rc := models.AcquireRequestContext()
		rc.Metadata["org_id"] = "org-rw"
		rc.PassThrough = body
		return rc
	}
	registry := map[string]Guardrail{
		"masker":  &rewriter{name: "masker", rewrites: true},
		"blocker": &rewriter{name: "blocker", block: true},
		"reader":  &rewriter{name: "reader"},
	}

	t.Run("written once after the checks", func(t *testing.T) {
		body := &passThrough{req: chatOf("x")}
		rc := newContext(body)
		defer rc.Release()
		p := newPlugin(map[string]*tenant.GuardrailCheck{"masker": {Enabled: true, Action: "log"}}, registry)
		if r := p.ProcessRequest(ctx, rc); r.Error != nil || body.applies != 1 {
			t.Errorf("error %v, ApplyChecks called %d times; want none and 1", r.Error, body.applies)
		}
	})
	t.Run("not written when a check refuses the request", func(t *testing.T) {
		body := &passThrough{req: chatOf("x")}
		rc := newContext(body)
		defer rc.Release()
		p := newPlugin(map[string]*tenant.GuardrailCheck{"blocker": {Enabled: true, Action: "block"}}, registry)
		if r := p.ProcessRequest(ctx, rc); r.Error == nil || body.applies != 0 {
			t.Errorf("error %v, ApplyChecks called %d times; want the block and 0", r.Error, body.applies)
		}
	})
	t.Run("written when rc.Request is what the checks read (native /v1/completions)", func(t *testing.T) {
		body := &passThrough{req: chatOf("x")}
		rc := newContext(body)
		defer rc.Release()
		rc.Request = chatOf("chat")
		newPlugin(map[string]*tenant.GuardrailCheck{"masker": {Enabled: true, Action: "log"}}, registry).ProcessRequest(ctx, rc)
		if body.applies != 1 {
			t.Errorf("ApplyChecks called %d times, want 1", body.applies)
		}
	})
	for _, tc := range []struct {
		name   string
		checks map[string]*tenant.GuardrailCheck
		rules  []config.GuardrailRuleConfig
		want   string
	}{
		{"an org check rewrites", map[string]*tenant.GuardrailCheck{"reader": {Enabled: true, Action: "log"}, "masker": {Enabled: true, Action: "log"}}, nil, "masker"},
		{"a config rule rewrites", nil, []config.GuardrailRuleConfig{{Name: "reader", Stage: "pre", Mode: "sync", Action: "log"},
			{Name: "masker", Stage: "pre", Mode: "sync", Action: "log"}}, "masker"},
		{"no check says it rewrites", map[string]*tenant.GuardrailCheck{"reader": {Enabled: true, Action: "log"}}, nil, "request-remediation"},
	} {
		t.Run("an unwritable rewrite is refused: "+tc.name, func(t *testing.T) {
			body := &passThrough{req: chatOf("x"), applyErr: errors.New("message 0: the rewrite of messages[0].content cannot be written")}
			rc := newContext(body)
			defer rc.Release()
			apiErr := newPlugin(tc.checks, registry, tc.rules...).ProcessRequest(ctx, rc).Error
			if apiErr == nil || apiErr.Status != http.StatusForbidden || apiErr.Code != "remediation_unavailable" {
				t.Fatalf("result error %v, want 403 remediation_unavailable", apiErr)
			}
			for _, want := range []string{tc.want, "rewrites the request", "cannot be written"} {
				if !strings.Contains(apiErr.Message, want) {
					t.Errorf("message %q does not say %q", apiErr.Message, want)
				}
			}
			if rc.Metadata["guardrail_name"] != tc.want || rc.Metadata["guardrail_action"] != "blocked" || !rc.Flags.GuardrailTriggered {
				t.Errorf("the block is not recorded: metadata %v", rc.Metadata)
			}
		})
	}
}

// RewritingPre names the pre-stage rules that would rewrite a request: the
// rules that run on it, sync or async, whatever their action, and that
// rewrite what they read (RequestRewriter).
func TestEngine_RewritingPre(t *testing.T) {
	registry := map[string]Guardrail{
		"masker": &rewriter{name: "masker", rewrites: true},
		"reader": &rewriter{name: "reader"},
	}
	engine := func(rules ...config.GuardrailRuleConfig) *Engine {
		return NewEngine(config.GuardrailsConfig{DefaultTimeout: time.Second, Rules: rules}, registry)
	}
	rule := func(name, stage, mode, action string) config.GuardrailRuleConfig {
		return config.GuardrailRuleConfig{Name: name, Stage: stage, Mode: mode, Action: action, Threshold: 0.5}
	}
	for _, tc := range []struct {
		name   string
		engine *Engine
		key    *policy.Policy
		req    policy.RequestPolicy
		want   []string
	}{
		{"a rule that rewrites, in log mode", engine(rule("reader", "pre", "sync", "log"), rule("masker", "pre", "sync", "log")),
			nil, policy.RequestPolicyNone, []string{"masker"}},
		{"in block mode", engine(rule("masker", "pre", "sync", "block")), nil, policy.RequestPolicyNone, []string{"masker"}},
		{"an async rule that rewrites runs on the request too", engine(rule("masker", "pre", "async", "log")),
			nil, policy.RequestPolicyNone, []string{"masker"}},
		{"a rule that does not rewrite", engine(rule("reader", "pre", "sync", "log")), nil, policy.RequestPolicyNone, nil},
		{"the key's policy disables it", engine(rule("masker", "pre", "sync", "log")),
			&policy.Policy{Overrides: map[string]policy.Override{"masker": {Disabled: true}}}, policy.RequestPolicyNone, nil},
		{"X-Guardrail-Policy disabled", engine(rule("masker", "pre", "sync", "log")), nil, policy.RequestPolicyDisabled, nil},
		{"X-Guardrail-Policy log-only still runs it", engine(rule("masker", "pre", "sync", "log")),
			nil, policy.RequestPolicyLogOnly, []string{"masker"}},
		{"a post rule is not a check on the request", engine(rule("masker", "post", "sync", "log")), nil, policy.RequestPolicyNone, nil},
		{"no engine", nil, nil, policy.RequestPolicyNone, nil},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := tc.engine.RewritingPre(tc.key, tc.req); !slices.Equal(got, tc.want) {
				t.Errorf("RewritingPre = %v, want %v", got, tc.want)
			}
		})
	}
}

// A pass-through body the gateway cannot read is refused before the
// provider call when a check on the request would rewrite it (a
// remediation), whatever the check's action: its rewrite could never be
// applied. 403 request_unavailable naming the check, which says it rewrites
// the request and why the body cannot be read. A rule the key's policy
// disables does not apply, and a check that does not rewrite in a mode that
// does not block lets the request go on.
func TestPlugin_RefusesAnUnreadableRequestACheckWouldRewrite(t *testing.T) {
	const reason = "the gateway cannot read this request: messages is not a list"
	registry := map[string]Guardrail{
		"masker": &rewriter{name: "masker", rewrites: true},
		"reader": &rewriter{name: "reader"},
	}
	newPlugin := func(checks map[string]*tenant.GuardrailCheck, policies *policy.Store, rules ...config.GuardrailRuleConfig) *GuardrailPlugin {
		tenants := tenant.NewStore()
		tenants.Set("org-rw", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{Checks: checks}})
		engine := NewEngine(config.GuardrailsConfig{DefaultTimeout: time.Second, Rules: rules}, registry)
		return NewPlugin(engine, registry, nil, policies, tenants)
	}
	run := func(p *GuardrailPlugin) (*models.RequestContext, *models.APIError) {
		rc := models.AcquireRequestContext()
		rc.Metadata["org_id"] = "org-rw"
		rc.Metadata["auth_key_id"] = "key-rw"
		rc.PassThrough = &passThrough{reason: reason}
		return rc, p.ProcessRequest(context.Background(), rc).Error
	}
	logRule := func(name string) config.GuardrailRuleConfig {
		return config.GuardrailRuleConfig{Name: name, Stage: "pre", Mode: "sync", Action: "log", Threshold: 0.5}
	}
	for _, tc := range []struct {
		name string
		p    *GuardrailPlugin
		want string // the check the refusal names; "" when the request goes on
	}{
		{"an org check that rewrites, in log mode", newPlugin(map[string]*tenant.GuardrailCheck{
			"reader": {Enabled: true, Action: "log"}, "masker": {Enabled: true, Action: "log"}}, nil), "masker"},
		{"an org check that rewrites, in warn mode", newPlugin(map[string]*tenant.GuardrailCheck{
			"masker": {Enabled: true, Action: "warn"}}, nil), "masker"},
		{"a config rule that rewrites, in log mode", newPlugin(nil, nil, logRule("reader"), logRule("masker")), "masker"},
		{"a config rule the key's policy disables", newPlugin(nil, func() *policy.Store {
			s := policy.NewStore()
			s.Register("key-rw", &config.KeyGuardrailPolicyConfig{Overrides: []config.KeyGuardrailOverride{{Name: "masker", Disabled: true}}})
			return s
		}(), logRule("masker")), ""},
		{"a check that does not rewrite, in log mode", newPlugin(map[string]*tenant.GuardrailCheck{
			"reader": {Enabled: true, Action: "log"}}, nil, logRule("reader")), ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rc, apiErr := run(tc.p)
			defer rc.Release()
			if rc.Metadata["request_unavailable"] != reason {
				t.Errorf("metadata %v, want request_unavailable", rc.Metadata)
			}
			if tc.want == "" {
				if apiErr != nil {
					t.Fatalf("refused: %v", apiErr)
				}
				return
			}
			if apiErr == nil || apiErr.Status != http.StatusForbidden || apiErr.Code != "request_unavailable" {
				t.Fatalf("result error %v, want 403 request_unavailable", apiErr)
			}
			for _, want := range []string{tc.want, "rewrites the request", reason} {
				if !strings.Contains(apiErr.Message, want) {
					t.Errorf("message %q does not say %q", apiErr.Message, want)
				}
			}
			if !rc.Flags.GuardrailTriggered || rc.Metadata["guardrail_action"] != "blocked" || rc.Metadata["guardrail_name"] != tc.want {
				t.Errorf("the block is not recorded: triggered %v, metadata %v", rc.Flags.GuardrailTriggered, rc.Metadata)
			}
		})
	}
}
