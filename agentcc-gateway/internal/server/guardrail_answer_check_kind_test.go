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

// An evaluation, a webhook or an external evaluator runs on the answer with
// the kind its config sets ("judges"): "says" reads a model's refusal as
// content, "shape" does not (a refused choice with no content is not read at
// all), and with none it reads the answer as production did (the refused
// choice, without the refusal). An evaluation with no "judges" takes the
// kind of its system eval ids when they are all of one kind. Any other
// "judges" fails the check closed. Plain and streamed answers, block mode,
// the check as a config-file rule on the answer.
//
// Each probe is the real type, built from the config under test, so it has
// that config's kind; its Check stands in for the remote call: like a schema
// evaluation, it refuses an answer in which it can read the refusal's text,
// and it records how many choices it read.

type kindProbe struct {
	name    string
	mark    string
	choices atomic.Int32 // choices in the answer it last read; -2 before it runs
}

func newKindProbe(name, mark string) *kindProbe {
	p := &kindProbe{name: name, mark: mark}
	p.choices.Store(-2)
	return p
}

func (p *kindProbe) check(in *guardrails.CheckInput) *guardrails.CheckResult {
	n := int32(-1)
	if in.Response != nil {
		n = int32(len(in.Response.Choices))
	}
	p.choices.Store(n)
	b, _ := json.Marshal(in.Response)
	if strings.Contains(string(b), p.mark) {
		return &guardrails.CheckResult{Pass: false, Score: 1, Message: "the answer is not the JSON the schema asks for"}
	}
	return &guardrails.CheckResult{Pass: true}
}

type kindEvaluation struct {
	*futureagi.FutureAGIGuardrail
	p *kindProbe
}

func (g kindEvaluation) Name() string            { return g.p.name }
func (g kindEvaluation) Stage() guardrails.Stage { return guardrails.StagePre }
func (g kindEvaluation) Check(_ context.Context, in *guardrails.CheckInput) *guardrails.CheckResult {
	return g.p.check(in)
}

type kindWebhook struct {
	*webhook.WebhookGuardrail
	p *kindProbe
}

func (g kindWebhook) Name() string            { return g.p.name }
func (g kindWebhook) Stage() guardrails.Stage { return guardrails.StagePre }
func (g kindWebhook) Check(_ context.Context, in *guardrails.CheckInput) *guardrails.CheckResult {
	return g.p.check(in)
}

type kindExternal struct {
	*external.ExternalGuardrail
	p *kindProbe
}

func (g kindExternal) Name() string            { return g.p.name }
func (g kindExternal) Stage() guardrails.Stage { return guardrails.StagePre }
func (g kindExternal) Check(_ context.Context, in *guardrails.CheckInput) *guardrails.CheckResult {
	return g.p.check(in)
}

// kindConfigs are each type's config with the given "judges" (none when nil)
// and, for the evaluation, eval ids; nothing in them is ever called.
func kindConfig(kind string, judges interface{}, evalIDs ...string) map[string]interface{} {
	var cfg map[string]interface{}
	switch kind {
	case "evaluation":
		ids := []interface{}{"999999"} // no system eval's id
		if len(evalIDs) > 0 {
			ids = nil
			for _, id := range evalIDs {
				ids = append(ids, id)
			}
		}
		cfg = map[string]interface{}{"eval_ids": ids, "api_key": "k", "secret_key": "s", "base_url": "http://127.0.0.1:1"}
	case "webhook":
		cfg = map[string]interface{}{"url": "http://127.0.0.1:1/check"}
	case "external":
		cfg = map[string]interface{}{"provider": "lakera", "api_key": "k", "endpoint": "http://127.0.0.1:1/guard"}
	}
	if judges != nil {
		cfg["judges"] = judges
	}
	return cfg
}

// kindCheck is the probe of that type built from cfg, under name.
func kindCheck(kind, name string, cfg map[string]interface{}, p *kindProbe) guardrails.Guardrail {
	switch kind {
	case "evaluation":
		return kindEvaluation{futureagi.NewConfigFileRule(name, cfg), p}
	case "webhook":
		return kindWebhook{webhook.NewConfigFileRule(name, cfg), p}
	default:
		return kindExternal{external.NewConfigFileRule(name, cfg), p}
	}
}

func TestOutputCheckKindSetting(t *testing.T) {
	refusal := refusalOnlyAnswer(shapeRefusal)
	type want int
	const (
		reads        want = iota // refuses the refusal it reads
		skips                    // serves it; the refused choice is not read
		asProduction             // serves it; the refused choice is read without the refusal
	)
	cases := []struct {
		kind    string
		judges  interface{}
		evalIDs []string
		want    want
	}{
		{"evaluation", "says", nil, reads},
		{"evaluation", "shape", nil, skips},
		{"evaluation", nil, nil, asProduction},
		{"evaluation", nil, []string{"15"}, reads},              // toxicity: says
		{"evaluation", nil, []string{"23", "37"}, skips},        // is_json, json_scheme_validation: shape
		{"evaluation", nil, []string{"23", "15"}, asProduction}, // mixed
		{"webhook", "says", nil, reads},
		{"webhook", "shape", nil, skips},
		{"webhook", nil, nil, asProduction},
		{"external", "says", nil, reads},
		{"external", "shape", nil, skips},
		{"external", nil, nil, asProduction},
	}
	for _, tc := range cases {
		for _, stream := range []bool{false, true} {
			label := tc.kind + " / judges " + judgesLabel(tc.judges)
			if len(tc.evalIDs) > 0 {
				label += " / evals " + strings.Join(tc.evalIDs, ",")
			}
			t.Run(label+" / "+streamLabel(stream), func(t *testing.T) {
				name := "schema-" + tc.kind
				p := newKindProbe(name, refusal.mark)
				g := newPostBlockGateway(t, postBlockSetup{staticRule: name, capture: true,
					checks:   map[string]guardrails.Guardrail{name: kindCheck(tc.kind, name, kindConfig(tc.kind, tc.judges, tc.evalIDs...), p)},
					upstream: refusalAudioUpstream(refusal)})
				code, body := g.chat(t, stream)
				switch tc.want {
				case reads:
					assertRefusalBlocked(t, stream, code, body, refusal, name)
				case skips:
					assertRefusalServed(t, stream, code, body, refusal)
					if n := p.choices.Load(); n != 0 {
						t.Errorf("the check read %d choices, want none (the refused choice has no content)", n)
					}
				case asProduction:
					assertRefusalServed(t, stream, code, body, refusal)
					if n := p.choices.Load(); n != 1 {
						t.Errorf("the check read %d choices, want the refused choice, as production gave it", n)
					}
				}
			})
		}
	}
}

// judgesLabel is a "judges" value for a test name: "none" when unset.
func judgesLabel(v interface{}) string {
	if v == nil {
		return "none"
	}
	b, _ := json.Marshal(v)
	return strings.ReplaceAll(string(b), `"`, "")
}

// A "judges" other than "says" or "shape" fails the check closed: it refuses
// every answer, naming the setting, and calls nothing.
func TestOutputCheckKindInvalidFailsClosed(t *testing.T) {
	refusal := refusalOnlyAnswer(shapeRefusal)
	for _, kind := range []string{"evaluation", "webhook", "external"} {
		for _, judges := range []interface{}{"content", true} {
			for _, stream := range []bool{false, true} {
				t.Run(kind+" / "+judgesLabel(judges)+" / "+streamLabel(stream), func(t *testing.T) {
					name := "schema-" + kind
					cfg := kindConfig(kind, judges)
					var g guardrails.Guardrail
					switch kind {
					case "evaluation":
						g = futureagi.NewConfigFileRule(name, cfg)
					case "webhook":
						g = webhook.NewConfigFileRule(name, cfg)
					default:
						g = external.NewConfigFileRule(name, cfg)
					}
					gw := newPostBlockGateway(t, postBlockSetup{staticRule: name, capture: true,
						checks: map[string]guardrails.Guardrail{name: g}, upstream: refusalAudioUpstream(refusal)})
					code, body := gw.chat(t, stream)
					assertRefusalBlocked(t, stream, code, body, refusal, name)
					if !strings.Contains(body, `\"judges\" must be`) && !strings.Contains(body, `"judges" must be`) {
						t.Errorf("the refusal does not name the invalid setting: %s", body)
					}
				})
			}
		}
	}
}
