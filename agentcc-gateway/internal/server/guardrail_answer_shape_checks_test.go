package server

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/blocklist"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/pii"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/validation"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// A check that judges the answer's shape (input-validation: its lengths, the
// patterns the whole text must match) does not apply to a model's refusal: a
// refusal is not an answer in that shape, and structured outputs hand it to
// the application on purpose. A check that judges what the text says
// (keyword-blocklist, pii-detection, data-leakage-prevention) still reads the
// refusal as content, also when a shape check runs beside it. Plain and
// streamed answers; every check in block mode.

// shapeRule is input-validation allowing at most 40 characters per message;
// the prompt is shorter, shapeRefusal and postBlockLeak are longer.
func shapeRule() guardrails.Guardrail {
	return validation.New(map[string]interface{}{"max_message_length": 40})
}

const shapeRefusal = "I'm sorry, but I can't help with that request. Please ask about something else."

func refusalOnlyAnswer(text string) refusalAudioMessage {
	q, _ := json.Marshal(text)
	return refusalAudioMessage{name: "a refusal",
		message: `{"role":"assistant","content":null,"refusal":` + string(q) + `}`,
		delta:   `{"refusal":` + string(q) + `}`, text: text, mark: text[:12]}
}

// assertRefusalServed: 200 with the refusal, no error event.
func assertRefusalServed(t *testing.T, stream bool, code int, body string, m refusalAudioMessage) {
	t.Helper()
	if code != http.StatusOK || !strings.Contains(body, m.mark) {
		t.Fatalf("status %d, body %s; want 200 with the refusal", code, body)
	}
	if _, at := streamError(sseEvents(body)); stream && at >= 0 {
		t.Fatalf("a served stream got an error event; body %s", body)
	}
	if !stream {
		var resp models.ChatCompletionResponse
		if err := json.Unmarshal([]byte(body), &resp); err != nil || len(resp.Choices) != 1 ||
			resp.Choices[0].Message.Refusal == nil || *resp.Choices[0].Message.Refusal != m.text {
			t.Fatalf("body %s does not carry the refusal: %v", body, err)
		}
	}
}

// assertRefusalBlocked: refused content_blocked naming check (plain and
// streamed), nothing of the refusal shown.
func assertRefusalBlocked(t *testing.T, stream bool, code int, body string, m refusalAudioMessage, check string) {
	t.Helper()
	if strings.Contains(body, m.mark) {
		t.Errorf("the refused answer was served: status %d, body %s", code, body)
	}
	if stream {
		if code != http.StatusOK {
			t.Fatalf("status %d, want the held stream's 200; body %s", code, body)
		}
		events, at := assertStreamEndsWithGuardrailError(t, body, "content_blocked")
		if eb, _ := streamError(events[at:]); !strings.Contains(eb.Error.Message, check) {
			t.Errorf("stream error %+v, want content_blocked naming %s", eb.Error, check)
		}
		return
	}
	var eb errorBody
	if err := json.Unmarshal([]byte(body), &eb); err != nil || code != http.StatusForbidden {
		t.Fatalf("status %d body %s, want 403 content_blocked", code, body)
	}
	if eb.Error.Code != "content_blocked" || !strings.Contains(eb.Error.Message, check) {
		t.Errorf("error %+v, want content_blocked naming %s", eb.Error, check)
	}
}

func TestShapeCheckDoesNotApplyToARefusal(t *testing.T) {
	refusal := refusalOnlyAnswer(shapeRefusal)
	for _, stream := range []bool{false, true} {
		t.Run(streamLabel(stream), func(t *testing.T) {
			g := newPostBlockGateway(t, postBlockSetup{staticRule: "input-validation", capture: true,
				checks: map[string]guardrails.Guardrail{"input-validation": shapeRule()}, upstream: refusalAudioUpstream(refusal)})
			code, body := g.chat(t, stream)
			assertRefusalServed(t, stream, code, body, refusal)
		})
		// Control: the same check refuses an ordinary answer longer than
		// that, so it is in force.
		t.Run(streamLabel(stream)+" / control", func(t *testing.T) {
			g := newPostBlockGateway(t, postBlockSetup{staticRule: "input-validation", capture: true,
				checks: map[string]guardrails.Guardrail{"input-validation": shapeRule()}})
			code, body := g.chat(t, stream)
			if strings.Contains(body, "DROP TABLE") {
				t.Fatalf("the shape check let a long answer through: %d %s", code, body)
			}
		})
	}
}

func TestSaysChecksStillReadARefusal(t *testing.T) {
	for _, tc := range []struct {
		check string
		g     guardrails.Guardrail
		text  string
	}{
		{"keyword-blocklist", blocklist.New(map[string]interface{}{"words": []interface{}{"drop table"}}), postBlockLeak},
		{"pii-detection", pii.New(nil), "Your SSN on file is " + requestCheckSSN + "."},
	} {
		blocked := refusalOnlyAnswer(tc.text)
		clean := refusalOnlyAnswer("I can't help with that request.")
		for _, stream := range []bool{false, true} {
			t.Run(tc.check+" / "+streamLabel(stream), func(t *testing.T) {
				g := newPostBlockGateway(t, postBlockSetup{staticRule: tc.check, capture: true,
					checks: map[string]guardrails.Guardrail{tc.check: tc.g}, upstream: refusalAudioUpstream(blocked)})
				code, body := g.chat(t, stream)
				assertRefusalBlocked(t, stream, code, body, blocked, tc.check)

				g = newPostBlockGateway(t, postBlockSetup{staticRule: tc.check, capture: true,
					checks: map[string]guardrails.Guardrail{tc.check: tc.g}, upstream: refusalAudioUpstream(clean)})
				code, body = g.chat(t, stream)
				assertRefusalServed(t, stream, code, body, clean)
			})
		}
	}
}

// A shape check beside a says check: the shape check skips the refusal, the
// says check (data-leakage-prevention, the org's check) still reads it.
func TestShapeAndSaysChecksSideBySide(t *testing.T) {
	blocked := refusalOnlyAnswer(postBlockLeak)
	clean := refusalOnlyAnswer(shapeRefusal)
	for _, stream := range []bool{false, true} {
		t.Run(streamLabel(stream), func(t *testing.T) {
			setup := func(m refusalAudioMessage) postBlockSetup {
				return postBlockSetup{staticRule: "input-validation", orgCheck: true, capture: true,
					checks: map[string]guardrails.Guardrail{"input-validation": shapeRule()}, upstream: refusalAudioUpstream(m)}
			}
			g := newPostBlockGateway(t, setup(blocked))
			code, body := g.chat(t, stream)
			assertRefusalBlocked(t, stream, code, body, blocked, leakageCheck)

			g = newPostBlockGateway(t, setup(clean))
			code, body = g.chat(t, stream)
			assertRefusalServed(t, stream, code, body, clean)
		})
	}
}
