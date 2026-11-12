package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The final writer serves a refusal and audio with a transcript as they came:
// the checks on the answer read them (the guardrail plugin's post stage).
// Only audio with no transcript, which no text check can read, is refused
// under a check that would block the answer.
func TestWalkFinalResponsePreservesOutputOrFailsClosed(t *testing.T) {
	for _, message := range []string{
		`{"role":"assistant","audio":{"id":"audio_fixture","data":"SGVsbG8=","transcript":"Hello","expires_at":1900000000}}`,
		`{"role":"assistant","refusal":"Cannot help"}`,
		`{"role":"assistant","audio":{"id":"audio_fixture","data":"SGVsbG8=","expires_at":1900000000}}`,
	} {
		unreadable := !strings.Contains(message, "transcript") && !strings.Contains(message, "refusal")
		for _, blocked := range []bool{false, true} {
			t.Run(message+map[bool]string{false: "/allowed", true: "/checked"}[blocked], func(t *testing.T) {
				rc := models.AcquireRequestContext()
				defer rc.Release()
				if err := json.Unmarshal([]byte(`{"choices":[{"index":0,"message":`+message+`,"finish_reason":"stop"}]}`), &rc.Response); err != nil {
					t.Fatal(err)
				}
				if blocked {
					rc.Flags.AnswerBlockers = models.AnswerReadByRules
				}
				w := httptest.NewRecorder()
				(&Handlers{}).writeChatResponse(w, rc, false, nil)
				if blocked && unreadable {
					if w.Code != http.StatusForbidden || !strings.Contains(w.Body.String(), "answer_unavailable") {
						t.Fatalf("unchecked output released: %d %s", w.Code, w.Body.String())
					}
					if strings.Contains(w.Body.String(), "SGVsbG8=") || strings.Contains(w.Body.String(), "Cannot help") {
						t.Fatal("refusal leaked unchecked answer")
					}
				} else {
					if w.Code != http.StatusOK {
						t.Fatalf("status %d: %s", w.Code, w.Body.String())
					}
					if !strings.Contains(w.Body.String(), `"content":null`) {
						t.Fatalf("non-text output has wrong content: %s", w.Body.String())
					}
					if !strings.Contains(w.Body.String(), `"audio"`) && !strings.Contains(w.Body.String(), `"refusal"`) {
						t.Fatalf("output lost: %s", w.Body.String())
					}
				}
			})
		}
	}
}

// A streamed refusal or audio answer is captured whole. The checks on the
// answer read a refusal and a transcript, so a held stream that carries them
// is complete; only audio with no transcript is not, and a held stream that
// carries it fails closed.
func TestWalkCaptureSupplementalOutputAndHeldSafety(t *testing.T) {
	for _, fragments := range [][]string{
		{`{"audio":{"id":"audio_fixture","data":"SGVs","transcript":"Hel"}}`, `{"audio":{"data":"bG8=","transcript":"lo","expires_at":1900000000}}`},
		{`{"refusal":"Cannot "}`, `{"refusal":"help"}`},
		{`{"audio":{"id":"audio_fixture","data":"SGVs"}}`, `{"audio":{"data":"bG8=","expires_at":1900000000}}`},
	} {
		bare := !strings.Contains(strings.Join(fragments, ""), "transcript") && !strings.Contains(strings.Join(fragments, ""), "refusal")
		c := newStreamCapture(true)
		for _, fragment := range fragments {
			var delta models.Delta
			if err := json.Unmarshal([]byte(fragment), &delta); err != nil {
				t.Fatal(err)
			}
			c.observe(models.StreamChunk{Choices: []models.StreamChoice{{Index: 0, Delta: delta}}})
		}
		c.observe(chunk("", "stop"))
		resp := &models.ChatCompletionResponse{}
		c.applyTo(resp)
		raw, err := json.Marshal(resp)
		if err != nil {
			t.Fatal(err)
		}
		if len(resp.Choices) != 1 {
			t.Fatalf("missing capture: %s", raw)
		}
		if audio := resp.Choices[0].Message.Audio; audio != nil {
			want := "Hello"
			if bare {
				want = ""
			}
			if audio.ID != "audio_fixture" || audio.Data != "SGVsbG8=" || audio.Transcript != want || audio.ExpiresAt != 1900000000 {
				t.Fatalf("audio corrupted: %+v", audio)
			}
		} else if refusal := resp.Choices[0].Message.Refusal; refusal == nil || *refusal != "Cannot help" {
			t.Fatalf("refusal corrupted: %s", raw)
		}
		if string(resp.Extra["agentcc_capture_incomplete"]) == "true" {
			t.Fatalf("complete log marked partial: %s", raw)
		}
		if c.complete() == bare {
			t.Fatalf("complete() = %v for %v: the checks read a refusal and a transcript, not bare audio", c.complete(), fragments)
		}
		rc := models.AcquireRequestContext()
		defer rc.Release()
		rc.Response = resp
		rc.Flags.AnswerBlockers = models.AnswerReadByRules
		held := newHeldStream(httptest.NewRecorder())
		refuseUnreadHeldChat(rc, held, c)
		if rc.Flags.AnswerUnavailable != bare {
			t.Fatalf("held stream AnswerUnavailable = %v for %v, want %v", rc.Flags.AnswerUnavailable, fragments, bare)
		}
	}
}

func TestWalkCaptureRepeatedToolIdentityAndSparseIndex(t *testing.T) {
	c := newStreamCapture(true)
	c.observe(walkToolChunk(0, 1<<30, "call_fixture", "f", `{"a":`, ""))
	c.observe(walkToolChunk(0, 1<<30, "call_fixture", "", `1}`, "tool_calls"))
	resp := &models.ChatCompletionResponse{}
	c.applyTo(resp)
	if !c.complete() || len(resp.Choices) != 1 || len(resp.Choices[0].Message.ToolCalls) != 1 {
		t.Fatalf("bad sparse capture: %+v", resp)
	}
	tc := resp.Choices[0].Message.ToolCalls[0]
	if tc.ID != "call_fixture" || tc.Function.Arguments != `{"a":1}` {
		t.Fatalf("repeated ID corrupted tool: %+v", tc)
	}
}
