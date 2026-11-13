package server

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/expression"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/validation"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/webhook"
)

func TestSpeechGuardrailsCheckerReadiness(t *testing.T) {
	for _, tc := range []struct {
		name    string
		check   guardrails.Guardrail
		ready   bool
		blocked bool
	}{
		{"expression_allow", expression.New("speech-test", map[string]interface{}{"expression": "false"}), true, false},
		{"expression_deny", expression.New("speech-test", map[string]interface{}{"expression": "true"}), true, true},
		{"expression_trailing_token", expression.New("speech-test", map[string]interface{}{"expression": "true true"}), false, false},
		{"expression_empty", expression.New("speech-test", nil), false, false},
		{"expression_field_allow", expression.New("speech-test", map[string]interface{}{"expression": `request.messages[0].content matches "prohibited"`}), true, false},
		{"expression_field_deny", expression.New("speech-test", map[string]interface{}{"expression": `request.messages[0].content contains "hello"`}), true, true},
		{"expression_unknown_character", expression.New("speech-test", map[string]interface{}{"expression": "false @"}), false, false},
		{"expression_missing_operand", expression.New("speech-test", map[string]interface{}{"expression": "true &&"}), false, false},
		{"expression_missing_delimiter", expression.New("speech-test", map[string]interface{}{"expression": "(false"}), false, false},
		{"expression_invalid_regex", expression.New("speech-test", map[string]interface{}{"expression": `request.model matches "["`}), false, false},
		{"expression_unknown_function", expression.New("speech-test", map[string]interface{}{"expression": "unknown(false)"}), false, false},
		{"expression_unterminated_string", expression.New("speech-test", map[string]interface{}{"expression": `request.model == "hello`}), false, false},
		{"expression_missing_list_separator", expression.New("speech-test", map[string]interface{}{"expression": `request.model in ["a" "b"]`}), false, false},
		{"expression_syntax", expression.New("speech-test", map[string]interface{}{"expression": "("}), false, false},
		{"validation_allow", validation.New(map[string]interface{}{"deny_patterns": []interface{}{"prohibited"}}), true, false},
		{"validation_deny", validation.New(map[string]interface{}{"deny_patterns": []interface{}{"hello"}}), true, true},
		{"invalid_deny_regex", validation.New(map[string]interface{}{"deny_patterns": []interface{}{"["}}), false, false},
		{"invalid_require_regex", validation.New(map[string]interface{}{"require_patterns": []interface{}{"["}}), false, false},
		{"mixed_deny_regex", validation.New(map[string]interface{}{"deny_patterns": []interface{}{"prohibited", "["}}), false, false},
		{"wrong_pattern_type", validation.New(map[string]interface{}{"deny_patterns": []interface{}{42}}), false, false},
		{"wrong_pattern_list", validation.New(map[string]interface{}{"require_patterns": "hello"}), false, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f := newSpeechFixture(t, "cartesia", "pre", "static", "block", nil, speechRule(tc.check, 0))
			client, resp, err := f.dial(t, "/cartesia/tts/websocket", nil)
			if !tc.ready {
				if client != nil {
					_ = client.Close()
				}
				if resp == nil {
					t.Fatalf("no refusal response: %v", err)
				}
				defer func() { _ = resp.Body.Close() }()
				body, readErr := io.ReadAll(resp.Body)
				if readErr != nil {
					t.Fatal(readErr)
				}
				if err == nil || resp.StatusCode != 403 || !strings.Contains(string(body), `"code":"realtime_guardrail_unsupported"`) {
					t.Fatalf("invalid checker admitted: status=%d body=%s err=%v", resp.StatusCode, body, err)
				}
				f.mu.Lock()
				defer f.mu.Unlock()
				if f.dials != 0 || len(f.received) != 0 {
					t.Fatalf("invalid checker reached peer: dials=%d frames=%v", f.dials, f.received)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			defer func() { _ = client.Close() }()
			message := `{"model_id":"sonic-3","transcript":"hello"}`
			if err := client.WriteMessage(1, []byte(message)); err != nil {
				t.Fatal(err)
			}
			if tc.blocked {
				requireSpeechClose(t, client, "realtime_guardrail_blocked")
			} else {
				if err := client.SetReadDeadline(time.Now().Add(3 * time.Second)); err != nil {
					t.Fatal(err)
				}
				_, data, err := client.ReadMessage()
				if err != nil || string(data) != `{"type":"ack"}` {
					t.Fatalf("valid checker refused: %s %v", data, err)
				}
			}
			f.mu.Lock()
			defer f.mu.Unlock()
			if f.dials != 1 {
				t.Fatalf("provider dials=%d want=1", f.dials)
			}
			if tc.blocked && len(f.received) != 0 {
				t.Fatalf("denied text delivered: %v", f.received)
			}
			if !tc.blocked && (len(f.received) != 1 || f.received[0] != message) {
				t.Fatalf("approved text changed: %v", f.received)
			}
		})
	}
}

func TestSpeechGuardrailsExactWebhookVerdict(t *testing.T) {
	for _, tc := range []struct{ name, body, reason string }{
		{"allow_no_score", `{"pass":true}`, ""},
		{"deny_no_score", `{"pass":false}`, "realtime_guardrail_blocked"},
		{"allow_score", `{"pass":true,"score":0}`, ""},
		{"deny_score", `{"pass":false,"score":1}`, "realtime_guardrail_blocked"},
		{"pass_alias", `{"pass":false,"Pass":true}`, "guardrail_unavailable"},
		{"pass_alias_first", `{"Pass":true,"pass":false}`, "guardrail_unavailable"},
		{"pass_duplicate", `{"pass":false,"pass":true}`, "guardrail_unavailable"},
		{"pass_escaped_duplicate", `{"pass":false,"pass":true}`, "guardrail_unavailable"},
		{"score_alias", `{"pass":false,"score":1,"Score":0}`, "guardrail_unavailable"},
		{"score_duplicate", `{"pass":false,"score":1,"score":0}`, "guardrail_unavailable"},
		{"null_score", `{"pass":true,"score":null}`, "guardrail_unavailable"},
		{"score_string", `{"pass":true,"score":"0"}`, "guardrail_unavailable"},
		{"score_negative", `{"pass":true,"score":-1}`, "guardrail_unavailable"},
		{"score_large", `{"pass":true,"score":2}`, "guardrail_unavailable"},
		{"score_nonfinite", `{"pass":true,"score":1e999}`, "guardrail_unavailable"},
		{"oversize", `{"pass":true}` + strings.Repeat(" ", 1<<20), "guardrail_unavailable"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			dependency := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if _, err := io.WriteString(w, tc.body); err != nil {
					t.Error(err)
				}
			}))
			defer dependency.Close()
			check := webhook.New("speech-test", map[string]interface{}{"url": dependency.URL})
			f := newSpeechFixture(t, "cartesia", "pre", "static", "block", nil, speechRule(check, 0))
			client, _, err := f.dial(t, "/cartesia/tts/websocket", nil)
			if err != nil {
				t.Fatal(err)
			}
			defer func() { _ = client.Close() }()
			message := `{"model_id":"sonic-3","transcript":"hello"}`
			if err := client.WriteMessage(1, []byte(message)); err != nil {
				t.Fatal(err)
			}
			if tc.reason != "" {
				requireSpeechClose(t, client, tc.reason)
			} else {
				if err := client.SetReadDeadline(time.Now().Add(3 * time.Second)); err != nil {
					t.Fatal(err)
				}
				_, data, err := client.ReadMessage()
				if err != nil || string(data) != `{"type":"ack"}` {
					t.Fatalf("canonical approval refused: %s %v", data, err)
				}
			}
			f.mu.Lock()
			defer f.mu.Unlock()
			if f.dials != 1 {
				t.Fatalf("provider dials=%d want=1", f.dials)
			}
			if tc.reason != "" && len(f.received) != 0 {
				t.Fatalf("unapproved original delivered: %v", f.received)
			}
			if tc.reason == "" && (len(f.received) != 1 || f.received[0] != message) {
				t.Fatalf("approved original changed: %v", f.received)
			}
		})
	}
}
