package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/blocklist"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/validation"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestStaticPostContentInputChangesOnlyForRetryRules(t *testing.T) {
	for _, retry := range []bool{false, true} {
		for _, stream := range []bool{false, true} {
			// Ordinary streams intentionally retain their non-buffered behavior.
			if stream && !retry {
				continue
			}
			for _, tc := range []struct {
				name, prompt, answer string
				cfg                  map[string]interface{}
			}{
				{"input-validation", "OK please answer", "INVALID", map[string]interface{}{"require_patterns": []interface{}{"^OK"}}},
				{"keyword-blocklist", "Explain forbidden without repeating it", "clean answer", map[string]interface{}{"words": []interface{}{"forbidden"}}},
			} {
				t.Run(fmt.Sprintf("%s/retry=%t/stream=%t", tc.name, retry, stream), func(t *testing.T) {
					if retry {
						tc.cfg["on_block"] = "reflect"
					}
					var check guardrails.Guardrail = blocklist.New(tc.cfg)
					if tc.name == "input-validation" {
						check = validation.New(tc.cfg)
					}
					registry := map[string]guardrails.Guardrail{tc.name: check}
					ge := guardrails.NewEngine(config.GuardrailsConfig{DefaultTimeout: time.Second, Rules: []config.GuardrailRuleConfig{{Name: tc.name, Stage: "post", Mode: "sync", Action: "block", Threshold: .8, Config: tc.cfg}}}, registry)
					u := &reflectionUpstream{blocked: 99, blockedText: tc.answer}
					upstream := httptest.NewServer(http.HandlerFunc(u.serve))
					defer upstream.Close()
					s := createTestServerWithPlugins(t, upstream.URL, guardrails.NewPlugin(ge, registry, nil, nil, nil))
					r := httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(fmt.Sprintf(`{"model":"gpt-4o","messages":[{"role":"user","content":%q}],"stream":%t}`, tc.prompt, stream)))
					w := httptest.NewRecorder()
					s.httpServer.Handler.ServeHTTP(w, r)
					wantStatus, calls := 200, 1
					if (tc.name == "input-validation") == retry {
						wantStatus = 403
						if retry {
							calls = 2
						}
					}
					if w.Code != wantStatus || len(u.calls()) != calls {
						t.Fatalf("status=%d calls=%d body=%s", w.Code, len(u.calls()), w.Body.String())
					}
				})
			}
		}
	}
}

func TestReflexionInBandRepairErrorsDoNotExposePrivateMessages(t *testing.T) {
	for _, mode := range []string{"envelope", "finish", "envelope-finish"} {
		t.Run(mode, func(t *testing.T) {
			u := &reflectionUpstream{blocked: 1, repairStreamError: mode}
			upstream := httptest.NewServer(http.HandlerFunc(u.serve))
			defer upstream.Close()
			logger, records := reflectionLogCapture(t)
			observer := &reflectionObserver{}
			s := createTestServerWithPlugins(t, upstream.URL, reflectionPlugin(&reflectionCheck{name: "output"}, reflectionPolicy(), "block", "post"), observer, logger)
			w := callReflectionServer(s, true)
			if w.Code != 502 || len(u.calls()) != 2 || observer.snapshot.Response != nil {
				t.Fatalf("status=%d calls=%d body=%s", w.Code, len(u.calls()), w.Body.String())
			}
			var response models.ErrorResponse
			if err := json.Unmarshal(w.Body.Bytes(), &response); err != nil || response.Error.Type != models.ErrTypeUpstream || response.Error.Code != "provider_502" || response.Error.Message != "Provider request failed during response repair" {
				t.Fatalf("unexpected sanitized error: body=%s error=%v", w.Body.String(), err)
			}
			logger.Close()
			var record string
			select {
			case record = <-records:
			default:
				t.Fatal("request log was not delivered")
			}
			if !strings.Contains(record, `"status_code":502`) || len(observer.snapshot.Errors) != 1 {
				t.Fatalf("unexpected error observation: record=%s errors=%v", record, observer.snapshot.Errors)
			}
			for _, sink := range []string{w.Body.String(), record, fmt.Sprint(observer.snapshot.Errors)} {
				for _, private := range []string{"STREAM_PRIVATE_SENTINEL", "rejected secret answer", "Remove the rejected", "The preceding answer failed"} {
					if strings.Contains(sink, private) {
						t.Fatalf("private material escaped through an in-band error: %s", sink)
					}
				}
			}
		})
	}
}

func TestCollectCandidateRejectsInBandErrors(t *testing.T) {
	finish := "error"
	for _, chunk := range []models.StreamChunk{
		{Extra: map[string]json.RawMessage{"error": json.RawMessage(`{"message":"private sentinel"}`)}},
		{Choices: []models.StreamChoice{{Index: 0, FinishReason: &finish}}},
	} {
		for _, first := range []bool{false, true} {
			chunks := make(chan models.StreamChunk, 1)
			errs := make(chan error)
			var initial *models.StreamChunk
			if first {
				initial = &chunk
			} else {
				chunks <- chunk
			}
			close(chunks)
			close(errs)
			candidate, err := collectCandidate(context.Background(), chunks, errs, initial)
			if err == nil || strings.Contains(err.Error(), "private sentinel") || len(candidate.frames) != 0 {
				t.Fatalf("in-band failure retained for replay: candidate=%+v error=%v", candidate, err)
			}
		}
	}
	// A null error extension is not an upstream error envelope.
	chunk := models.StreamChunk{Extra: map[string]json.RawMessage{"error": json.RawMessage(" null ")}}
	if err := newBufferedCandidate().add(chunk); err != nil {
		t.Fatal(err)
	}
}

func TestCollectCandidateSettlesInBandFailureUsage(t *testing.T) {
	finish := "error"
	content := "private sentinel"
	// The failing frame reports the serving model; an already queued usage-only
	// trailer follows it. Both must reach settlement without the frame content.
	chunks := make(chan models.StreamChunk, 3)
	chunks <- models.StreamChunk{Model: "gpt-4o-mini", Choices: []models.StreamChoice{{Index: 0, Delta: models.Delta{Content: &content}, FinishReason: &finish}}}
	chunks <- models.StreamChunk{Usage: &models.Usage{PromptTokens: 1000, CompletionTokens: 500, TotalTokens: 1500}}
	errs := make(chan error)
	candidate, err := collectCandidate(context.Background(), chunks, errs, nil)
	if err == nil || len(candidate.frames) != 0 {
		t.Fatalf("in-band failure accepted: candidate=%+v error=%v", candidate, err)
	}
	u := candidate.usage
	if candidate.model != "gpt-4o-mini" || u == nil || u.PromptTokens != 1000 || u.CompletionTokens != 500 || u.TotalTokens != 1500 {
		t.Fatalf("failed attempt lost its accounting: model=%q usage=%+v", candidate.model, u)
	}
	if r := candidate.response(); len(r.Choices) != 0 || r.Model != "gpt-4o-mini" {
		t.Fatalf("failed attempt kept frame content: %+v", r)
	}
	// Harvesting never waits: the open channel and empty queue return at once.
	if len(chunks) != 0 {
		t.Fatalf("queued frames left behind: %d", len(chunks))
	}
}

func TestUnbufferedStreamInBandErrorRemainsUnchanged(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		fmt.Fprint(w, "data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"partial\"}}]}\n\ndata: {\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"error\"}],\"error\":{\"message\":\"ordinary-stream-error\"}}\n\ndata: [DONE]\n\n")
	}))
	defer upstream.Close()
	s := createTestServerWithPlugins(t, upstream.URL)
	w := callReflectionServer(s, true)
	if w.Code != 200 || !bytes.Contains(w.Body.Bytes(), []byte("ordinary-stream-error")) {
		t.Fatalf("ordinary stream behavior changed: status=%d body=%s", w.Code, w.Body.String())
	}
}
