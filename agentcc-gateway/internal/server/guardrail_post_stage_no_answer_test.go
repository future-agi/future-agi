package server

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// On a route whose answer is not model text (embeddings, images, speech,
// a transcription's or translation's text, rerank, search, OCR,
// embedContent, and the two routes that count a request's tokens), the post
// stage has no answer it can withhold: the handler serves what the provider
// answered whatever a check there says. So the response's headers and the
// request log never say a check there blocked a served answer. A check
// there that reads the request gets none (it reads it before the provider,
// if it runs there), and a check that fails anyway records the action it
// took: logged. The Assistants API, vector store search and /v1/videos run
// no post stage: no check there records anything.

// postAlarmCheck fails whatever it is given, at the post stage: a check on
// the answer that fails without reading one (an evaluator or a webhook that
// judges what it gets, the request's metadata, as failing).
const postAlarmCheck = "post-alarm"

type postAlarm struct{}

func (postAlarm) Name() string            { return postAlarmCheck }
func (postAlarm) Stage() guardrails.Stage { return guardrails.StagePost }
func (postAlarm) Check(context.Context, *guardrails.CheckInput) *guardrails.CheckResult {
	return &guardrails.CheckResult{Pass: false, Score: 0.99, Message: "judged failing"}
}

// postStageShapes are the checks at the post stage in block mode that fail
// without reading the answer: a rule in the config file (one that always
// fails), and an organization's check whose type runs there.
var postStageShapes = []struct {
	name  string
	setup postBlockSetup
}{
	{"config rule", postBlockSetup{staticRule: promptBlockRule}},
	{"org check", postBlockSetup{checks: map[string]guardrails.Guardrail{postAlarmCheck: postAlarm{}},
		orgChecks: map[string]*tenant.GuardrailCheck{postAlarmCheck: {Enabled: true, Action: "block", ConfidenceThreshold: 0.5}}}},
}

// doCapturingHeaders is doAs with the org's key, with the response's
// headers too.
func (g *postBlockGateway) doCapturingHeaders(t *testing.T, path, body string) (int, string, http.Header) {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, path, bytes.NewReader([]byte(body)))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+postBlockKey)
	req.Header.Set("anthropic-version", "2023-06-01")
	ctx, cancel := context.WithTimeout(req.Context(), 5*time.Second)
	defer cancel()
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req.WithContext(ctx))
	b, _ := io.ReadAll(w.Result().Body)
	return w.Code, string(b), w.Result().Header
}

// assertNoBlockRecorded: the log of a served answer records no block; when
// want is set, it records the check's trigger as the action taken, logged,
// and so does X-Agentcc-Guardrail-Triggered, unless the route streams its
// answer (speech/stream), whose headers go out before the post stage runs.
func assertNoBlockRecorded(t *testing.T, e map[string]any, header http.Header, code int, want string, streamed bool) {
	t.Helper()
	if e["status_code"] != float64(code) {
		t.Errorf("log status_code %v, want %d", e["status_code"], code)
	}
	meta, _ := e["metadata"].(map[string]any)
	if meta["guardrail_action"] == "blocked" {
		t.Errorf("the log records guardrail_action blocked for a served answer (%v)", meta["guardrail_name"])
	}
	results, _ := e["guardrail_results"].([]any)
	for _, r := range results {
		if r, _ := r.(map[string]any); r["action"] == "block" {
			t.Errorf("the log's guardrail_results record a block for a served answer: %v", r)
		}
	}
	if want == "" {
		if e["guardrail_triggered"] == true || meta["guardrail_action"] != nil || header.Get("X-Agentcc-Guardrail-Triggered") != "" {
			t.Errorf("a check that read nothing recorded a trigger: log %v, header %q", meta, header.Get("X-Agentcc-Guardrail-Triggered"))
		}
		return
	}
	if e["guardrail_triggered"] != true || meta["guardrail_action"] != "logged" || meta["guardrail_name"] != want {
		t.Errorf("log guardrail_triggered %v, guardrail_action %v, guardrail_name %v; want true, logged, %s",
			e["guardrail_triggered"], meta["guardrail_action"], meta["guardrail_name"], want)
	}
	if !streamed && header.Get("X-Agentcc-Guardrail-Triggered") != "true" {
		t.Errorf("X-Agentcc-Guardrail-Triggered %q, want true: the check triggered and its trigger was logged", header.Get("X-Agentcc-Guardrail-Triggered"))
	}
}

// On the routes that count a request's tokens, a pii-detection rule at the
// post stage in block mode gets no request, so it records nothing; the
// count is served.
func TestAnswerCheck_CountTokensPostStageRuleRecordsNoBlock(t *testing.T) {
	for _, sf := range countTokensSurfaces {
		t.Run(sf.name, func(t *testing.T) {
			calls := &upstreamCalls{}
			g := newPostBlockGateway(t, postBlockSetup{staticRule: requestCheck, upstream: countTokensUpstream(calls)})
			code, body, header := g.doCapturingHeaders(t, sf.path, sf.body(requestCheckPrompt))
			if code != http.StatusOK || calls.count() != 1 {
				t.Fatalf("status %d, provider calls %d; want 200 and 1; body %s", code, calls.count(), body)
			}
			assertNoBlockRecorded(t, g.entry(t), header, code, "", false)
		})
	}
}

// otherAPIsWithoutAnAnswerToWithhold are the non-chat routes that run the
// post stage, with OCR, whose document no check reads.
func otherAPIsWithoutAnAnswerToWithhold() []otherAPISurface {
	return append(postStageSurfaces(), otherAPISurface{name: "OCR", path: "/v1/ocr",
		body: func(string) string {
			return `{"model":"` + otherAPIsOCR + `","document":{"type":"document_url","document_url":"https://docs.example.invalid/a.pdf"}}`
		}, logged: true, ok: 200})
}

// A check at the post stage in block mode that fails without reading the
// answer records what it did on such a route, logged; the answer is served.
func TestAnswerCheck_PostStageBlockIsRecordedAsLoggedWhereNoAnswerCanBeWithheld(t *testing.T) {
	for _, shape := range postStageShapes {
		for _, sf := range otherAPIsWithoutAnAnswerToWithhold() {
			t.Run(shape.name+" / "+sf.name, func(t *testing.T) {
				g := newOtherAPIsGateway(t, shape.setup)
				code, body, header := g.sendCapturingHeaders(t, sf, postBlockPrompt)
				if code != sf.ok || g.providerCalls() != 1 {
					t.Fatalf("status %d, provider calls %d; want %d and 1; body %.200s", code, g.providerCalls(), sf.ok, body)
				}
				if !sf.logged {
					if header.Get("X-Agentcc-Guardrail-Triggered") != "true" {
						t.Errorf("X-Agentcc-Guardrail-Triggered %q, want true", header.Get("X-Agentcc-Guardrail-Triggered"))
					}
					return
				}
				name := postAlarmCheck
				if shape.setup.staticRule != "" {
					name = shape.setup.staticRule
				}
				assertNoBlockRecorded(t, g.entry(t), header, code, name, sf.path == "/v1/audio/speech/stream")
			})
		}
		for _, sf := range countTokensSurfaces {
			t.Run(shape.name+" / "+sf.name, func(t *testing.T) {
				calls := &upstreamCalls{}
				setup := shape.setup
				setup.upstream = countTokensUpstream(calls)
				g := newPostBlockGateway(t, setup)
				code, body, header := g.doCapturingHeaders(t, sf.path, sf.body(postBlockPrompt))
				if code != http.StatusOK || calls.count() != 1 {
					t.Fatalf("status %d, provider calls %d; want 200 and 1; body %s", code, calls.count(), body)
				}
				name := postAlarmCheck
				if shape.setup.staticRule != "" {
					name = shape.setup.staticRule
				}
				assertNoBlockRecorded(t, g.entry(t), header, code, name, false)
			})
		}
	}
}

// The Assistants API, vector store search and /v1/videos run no post
// stage: a check there in block mode never runs, and nothing says it
// blocked the answer they serve.
func TestAnswerCheck_PostStageCheckNeverRunsOnTheProxiedRoutes(t *testing.T) {
	for _, shape := range postStageShapes {
		for _, sf := range otherAPISurfaces {
			if sf.logged || sf.path == "/v1/search" {
				continue
			}
			t.Run(shape.name+" / "+sf.name, func(t *testing.T) {
				g := newOtherAPIsGateway(t, shape.setup)
				code, body, header := g.sendCapturingHeaders(t, sf, postBlockPrompt)
				if code != sf.ok {
					t.Fatalf("status %d, want %d; body %.200s", code, sf.ok, body)
				}
				if v := header.Get("X-Agentcc-Guardrail-Triggered"); v != "" {
					t.Errorf("X-Agentcc-Guardrail-Triggered %q on a route that runs no post stage", v)
				}
			})
		}
	}
}

// The control: where there is a chat answer, a post-stage check in block
// mode withholds it, and the log records the block (403).
func TestAnswerCheck_PostStageBlockWithholdsAChatAnswer(t *testing.T) {
	for _, shape := range postStageShapes {
		t.Run(shape.name, func(t *testing.T) {
			g, calls := newRequestCheckGateway(t, shape.setup)
			code, body := g.do(t, "/v1/chat/completions", `{"model":"gpt-4o","messages":[{"role":"user","content":"hello"}]}`)
			if code != http.StatusForbidden || calls.count() != 1 {
				t.Fatalf("status %d, provider calls %d; want 403 and 1; body %s", code, calls.count(), body)
			}
			name := postAlarmCheck
			if shape.setup.staticRule != "" {
				name = shape.setup.staticRule
			}
			assertBlockLogged(t, g.entry(t), name)
		})
	}
}
