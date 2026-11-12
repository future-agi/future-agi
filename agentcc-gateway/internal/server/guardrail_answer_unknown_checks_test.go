package server

import (
	"context"
	"encoding/json"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/external"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/futureagi"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/webhook"
)

// A check whose kind the gateway cannot tell (a Future AGI evaluation id, a
// webhook, an external evaluator) does not read a model's refusal: the
// refusal is served as production served it. A built-in check that judges
// what the text says still reads it beside such a check. (Built-in says and
// shape checks on their own: guardrail_answer_shape_checks_test.go.)
//
// Each probe has the real type's kind (it embeds the type) and stands in for
// the remote call with its own Check: like a schema evaluation, it refuses an
// answer in which it can read the refusal's text. Plain and streamed answers,
// block mode.

// refusalProbe refuses an answer whose JSON holds mark; ran counts its calls.
type refusalProbe struct {
	name string
	mark string
	ran  *atomic.Int32
}

func (p *refusalProbe) check(input *guardrails.CheckInput) *guardrails.CheckResult {
	p.ran.Add(1)
	b, _ := json.Marshal(input.Response)
	if strings.Contains(string(b), p.mark) {
		return &guardrails.CheckResult{Pass: false, Score: 1, Message: "the answer is not the JSON the schema asks for"}
	}
	return &guardrails.CheckResult{Pass: true}
}

type evaluationProbe struct {
	*futureagi.FutureAGIGuardrail
	p *refusalProbe
}

func (g evaluationProbe) Name() string            { return g.p.name }
func (g evaluationProbe) Stage() guardrails.Stage { return guardrails.StagePre }
func (g evaluationProbe) Check(_ context.Context, in *guardrails.CheckInput) *guardrails.CheckResult {
	return g.p.check(in)
}

type webhookProbe struct {
	*webhook.WebhookGuardrail
	p *refusalProbe
}

func (g webhookProbe) Name() string            { return g.p.name }
func (g webhookProbe) Stage() guardrails.Stage { return guardrails.StagePre }
func (g webhookProbe) Check(_ context.Context, in *guardrails.CheckInput) *guardrails.CheckResult {
	return g.p.check(in)
}

type externalProbe struct {
	*external.ExternalGuardrail
	p *refusalProbe
}

func (g externalProbe) Name() string            { return g.p.name }
func (g externalProbe) Stage() guardrails.Stage { return guardrails.StagePre }
func (g externalProbe) Check(_ context.Context, in *guardrails.CheckInput) *guardrails.CheckResult {
	return g.p.check(in)
}

// unknownKindProbes are one probe per kind, each refusing answers that hold
// mark.
func unknownKindProbes(mark string) []struct {
	g guardrails.Guardrail
	p *refusalProbe
} {
	mk := func(name string) *refusalProbe { return &refusalProbe{name: name, mark: mark, ran: new(atomic.Int32)} }
	e, w, x := mk("schema-evaluation"), mk("schema-webhook"), mk("schema-external")
	return []struct {
		g guardrails.Guardrail
		p *refusalProbe
	}{
		{evaluationProbe{&futureagi.FutureAGIGuardrail{}, e}, e},
		{webhookProbe{&webhook.WebhookGuardrail{}, w}, w},
		{externalProbe{&external.ExternalGuardrail{}, x}, x},
	}
}

func TestUnknownKindChecksServeARefusalUnread(t *testing.T) {
	refusal := refusalOnlyAnswer(shapeRefusal)
	q, _ := json.Marshal(shapeRefusal)
	content := refusalAudioMessage{name: "the same text as content",
		message: `{"role":"assistant","content":` + string(q) + `}`,
		delta:   `{"content":` + string(q) + `}`, text: shapeRefusal, mark: refusal.mark}
	for _, stream := range []bool{false, true} {
		for _, probe := range unknownKindProbes(refusal.mark) {
			t.Run(probe.p.name+" / "+streamLabel(stream), func(t *testing.T) {
				g := newPostBlockGateway(t, postBlockSetup{staticRule: probe.p.name, capture: true,
					checks: map[string]guardrails.Guardrail{probe.p.name: probe.g}, upstream: refusalAudioUpstream(refusal)})
				code, body := g.chat(t, stream)
				assertRefusalServed(t, stream, code, body, refusal)
				if probe.p.ran.Load() == 0 {
					t.Errorf("%s did not run on the answer", probe.p.name)
				}
			})
			// Control: the same check refuses the same text as an answer's
			// content, so it is in force and reads content.
			t.Run(probe.p.name+" / "+streamLabel(stream)+" / control", func(t *testing.T) {
				g := newPostBlockGateway(t, postBlockSetup{staticRule: probe.p.name, capture: true,
					checks: map[string]guardrails.Guardrail{probe.p.name: probe.g}, upstream: refusalAudioUpstream(content)})
				code, body := g.chat(t, stream)
				if strings.Contains(body, content.mark) {
					t.Fatalf("%s let the content through: %d %s", probe.p.name, code, body)
				}
			})
		}
	}
}

// An evaluation beside a built-in says check (the org's
// data-leakage-prevention): the evaluation does not read the refusal, the
// says check does and refuses one that leaks.
func TestSaysCheckBesideAnEvaluation(t *testing.T) {
	for _, tc := range []struct {
		refusal refusalAudioMessage
		blocked bool
	}{
		{refusalOnlyAnswer(postBlockLeak), true},
		{refusalOnlyAnswer(shapeRefusal), false},
	} {
		for _, stream := range []bool{false, true} {
			t.Run(tc.refusal.mark+" / "+streamLabel(stream), func(t *testing.T) {
				probe := unknownKindProbes(tc.refusal.mark)[0]
				g := newPostBlockGateway(t, postBlockSetup{staticRule: probe.p.name, orgCheck: true, capture: true,
					checks: map[string]guardrails.Guardrail{probe.p.name: probe.g}, upstream: refusalAudioUpstream(tc.refusal)})
				code, body := g.chat(t, stream)
				if tc.blocked {
					assertRefusalBlocked(t, stream, code, body, tc.refusal, leakageCheck)
					return
				}
				assertRefusalServed(t, stream, code, body, tc.refusal)
			})
		}
	}
}

// A choice that carries both a refusal and content: the refusal is withheld,
// the content is not. input-validation (shape) and an evaluation (unknown
// kind) each read the content and refuse it when it matches.
func TestContentBesideARefusalIsStillChecked(t *testing.T) {
	q, _ := json.Marshal(postBlockLeak)
	both := refusalAudioMessage{name: "content beside a refusal",
		message: `{"role":"assistant","content":` + string(q) + `,"refusal":"No."}`,
		delta:   `{"content":` + string(q) + `,"refusal":"No."}`, text: postBlockLeak, mark: "DROP TABLE"}
	for _, stream := range []bool{false, true} {
		t.Run("input-validation / "+streamLabel(stream), func(t *testing.T) {
			g := newPostBlockGateway(t, postBlockSetup{staticRule: "input-validation", capture: true,
				checks: map[string]guardrails.Guardrail{"input-validation": shapeRule()}, upstream: refusalAudioUpstream(both)})
			code, body := g.chat(t, stream)
			assertRefusalBlocked(t, stream, code, body, both, "input-validation")
		})
		t.Run("evaluation / "+streamLabel(stream), func(t *testing.T) {
			probe := unknownKindProbes(both.mark)[0]
			g := newPostBlockGateway(t, postBlockSetup{staticRule: probe.p.name, capture: true,
				checks: map[string]guardrails.Guardrail{probe.p.name: probe.g}, upstream: refusalAudioUpstream(both)})
			code, body := g.chat(t, stream)
			assertRefusalBlocked(t, stream, code, body, both, probe.p.name)
		})
	}
}
