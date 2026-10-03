package external

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

const (
	directionPrompt = "How do I reset my password?"
	directionOutput = "Open Settings and choose Reset password."
)

// captureTransport records request bodies and answers with statuses in order
// (200 once they run out) and a fixed body, so no network is needed.
type captureTransport struct {
	statuses []int
	reply    string
	bodies   [][]byte
}

func (c *captureTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		return nil, err
	}
	c.bodies = append(c.bodies, body)
	status := http.StatusOK
	if n := len(c.bodies); n <= len(c.statuses) {
		status = c.statuses[n-1]
	}
	return &http.Response{
		StatusCode: status,
		Header:     http.Header{"Content-Type": {"application/json"}},
		Body:       io.NopCloser(strings.NewReader(c.reply)),
		Request:    r,
	}, nil
}

// newCapturedGuardrail builds an ExternalGuardrail whose provider calls land
// in the returned transport.
func newCapturedGuardrail(t *testing.T, cfg map[string]interface{}, tr *captureTransport) *ExternalGuardrail {
	t.Helper()
	g := New("direction-test", cfg)
	g.client = &http.Client{Transport: tr}
	if cs, ok := g.adapter.(*crowdstrikeAdapter); ok {
		cs.cachedToken, cs.tokenExpiry = "token", time.Now().Add(time.Hour)
	}
	return g
}

func directionMessage(role, text string) models.Message {
	raw, _ := json.Marshal(text)
	return models.Message{Role: role, Content: raw}
}

func directionInput(withOutput bool) *guardrails.CheckInput {
	input := &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Model:    "gpt-4o",
			Messages: []models.Message{directionMessage("user", directionPrompt)},
		},
	}
	if withOutput {
		input.Response = &models.ChatCompletionResponse{
			Choices: []models.Choice{{Message: directionMessage("assistant", directionOutput)}},
		}
	}
	return input
}

func jsonField(v interface{}, path ...string) interface{} {
	for _, key := range path {
		m, _ := v.(map[string]interface{})
		v = m[key]
	}
	return v
}

func jsonString(v interface{}, path ...string) string {
	s, _ := jsonField(v, path...).(string)
	return s
}

// lastMessage returns the role and content of the last message in a list.
func lastMessage(v interface{}) (role, content string) {
	msgs, _ := v.([]interface{})
	if len(msgs) == 0 {
		return "", ""
	}
	return jsonString(msgs[len(msgs)-1], "role"), jsonString(msgs[len(msgs)-1], "content")
}

func TestOutputAwareAdaptersLabelDirection(t *testing.T) {
	tests := []struct {
		name  string
		cfg   map[string]interface{}
		reply string
		// sent returns the direction label a request carries and the text
		// it asks the vendor to check.
		sent    func(body interface{}) (label, text string)
		in, out string
		// promptWithOutput is set for vendors that judge a response in the
		// context of its prompt.
		promptWithOutput bool
	}{
		{
			name:  "bedrock_guardrails",
			cfg:   map[string]interface{}{"provider": "bedrock_guardrails", "endpoint": "http://vendor.test", "guardrail_id": "gr-1"},
			reply: `{"action":"NONE"}`,
			sent: func(b interface{}) (string, string) {
				content, _ := jsonField(b, "content").([]interface{})
				if len(content) == 0 {
					return jsonString(b, "source"), ""
				}
				return jsonString(b, "source"), jsonString(content[0], "text", "text")
			},
			in: "INPUT", out: "OUTPUT",
		},
		{
			name:  "aporia",
			cfg:   map[string]interface{}{"provider": "aporia", "endpoint": "http://vendor.test", "project_id": "proj-1"},
			reply: `{"action":"passthrough"}`,
			sent: func(b interface{}) (string, string) {
				target := jsonString(b, "validation_target")
				if target == "response" {
					return target, jsonString(b, "response")
				}
				_, text := lastMessage(jsonField(b, "messages"))
				return target, text
			},
			in: "prompt", out: "response",
			promptWithOutput: true,
		},
		{
			name:  "crowdstrike",
			cfg:   map[string]interface{}{"provider": "crowdstrike", "base_url": "http://vendor.test"},
			reply: `{"result":{"blocked":false}}`,
			sent: func(b interface{}) (string, string) {
				role, text := lastMessage(jsonField(b, "guard_input", "messages"))
				return jsonString(b, "event_type") + " " + role, text
			},
			in: "input user", out: "output assistant",
		},
		{
			name:  "dynamoai",
			cfg:   map[string]interface{}{"provider": "dynamoai", "endpoint": "http://vendor.test/moderation/analyze", "policy_ids": []interface{}{"pol-1"}},
			reply: `{"finalAction":"NONE"}`,
			sent: func(b interface{}) (string, string) {
				role, text := lastMessage(jsonField(b, "messages"))
				return jsonString(b, "textType") + " " + role, text
			},
			in: "MODEL_INPUT user", out: "MODEL_RESPONSE assistant",
			promptWithOutput: true,
		},
		{
			name:  "grayswan",
			cfg:   map[string]interface{}{"provider": "grayswan", "endpoint": "http://vendor.test/cygnal/monitor", "policy_id": "pol-1"},
			reply: `{"violation":0}`,
			sent: func(b interface{}) (string, string) {
				return lastMessage(jsonField(b, "messages"))
			},
			in: "user", out: "assistant",
		},
		{
			name:  "lasso",
			cfg:   map[string]interface{}{"provider": "lasso", "endpoint": "http://vendor.test/gateway/v3"},
			reply: `{"violations_detected":false}`,
			sent: func(b interface{}) (string, string) {
				role, text := lastMessage(jsonField(b, "messages"))
				return jsonString(b, "messageType") + " " + role, text
			},
			in: "PROMPT user", out: "COMPLETION assistant",
		},
		{
			name:  "llama_guard",
			cfg:   map[string]interface{}{"provider": "llama_guard", "endpoint": "http://vendor.test"},
			reply: `{"choices":[{"message":{"content":"safe"}}]}`,
			sent: func(b interface{}) (string, string) {
				return lastMessage(jsonField(b, "messages"))
			},
			in: "user", out: "assistant",
			promptWithOutput: true,
		},
		{
			name:  "pangea",
			cfg:   map[string]interface{}{"provider": "pangea", "domain": "vendor.test"},
			reply: `{"result":{"detected":false}}`,
			sent: func(b interface{}) (string, string) {
				return jsonString(b, "recipe"), jsonString(b, "text")
			},
			in: "pangea_prompt_guard", out: "pangea_llm_response_guard",
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			tr := &captureTransport{reply: tt.reply}
			g := newCapturedGuardrail(t, tt.cfg, tr)

			for _, withOutput := range []bool{false, true} {
				if res := g.Check(context.Background(), directionInput(withOutput)); !res.Pass {
					t.Fatalf("check (output %v) did not pass: %s", withOutput, res.Message)
				}
			}
			if len(tr.bodies) != 2 {
				t.Fatalf("vendor calls = %d, want 2", len(tr.bodies))
			}

			var promptBody, outputBody interface{}
			if err := json.Unmarshal(tr.bodies[0], &promptBody); err != nil {
				t.Fatalf("decode prompt request: %v", err)
			}
			if err := json.Unmarshal(tr.bodies[1], &outputBody); err != nil {
				t.Fatalf("decode output request: %v", err)
			}
			if label, text := tt.sent(promptBody); label != tt.in || text != directionPrompt {
				t.Errorf("prompt sent as (%q, %q), want (%q, %q)", label, text, tt.in, directionPrompt)
			}
			if label, text := tt.sent(outputBody); label != tt.out || text != directionOutput {
				t.Errorf("model output sent as (%q, %q), want (%q, %q)", label, text, tt.out, directionOutput)
			}
			if got := bytes.Contains(tr.bodies[1], []byte(directionPrompt)); got != tt.promptWithOutput {
				t.Errorf("output request carries the prompt = %v, want %v: %s", got, tt.promptWithOutput, tr.bodies[1])
			}
		})
	}
}

// A response is judged in the context of the prompt it answers: the last user
// message, not the system prompt or earlier turns.
func TestOutputRequestPromptIsLastUserMessage(t *testing.T) {
	const (
		systemPrompt = "You are the Example Corp support bot. Escalation code 7731."
		earlierTurn  = "Hi, I have a question about my account."
		earlierReply = "Sure, what would you like to know?"
	)
	tests := []struct {
		name  string
		cfg   map[string]interface{}
		reply string
	}{
		{"aporia", map[string]interface{}{"provider": "aporia", "endpoint": "http://vendor.test", "project_id": "proj-1"}, `{"action":"passthrough"}`},
		{"dynamoai", map[string]interface{}{"provider": "dynamoai", "endpoint": "http://vendor.test/moderation/analyze"}, `{"finalAction":"NONE"}`},
		{"llama_guard", map[string]interface{}{"provider": "llama_guard", "endpoint": "http://vendor.test"}, `{"choices":[{"message":{"content":"safe"}}]}`},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			tr := &captureTransport{reply: tt.reply}
			g := newCapturedGuardrail(t, tt.cfg, tr)
			input := directionInput(true)
			input.Request.Messages = []models.Message{
				directionMessage("system", systemPrompt),
				directionMessage("user", earlierTurn),
				directionMessage("assistant", earlierReply),
				directionMessage("user", directionPrompt),
			}

			if res := g.Check(context.Background(), input); !res.Pass {
				t.Fatalf("check did not pass: %s", res.Message)
			}
			if len(tr.bodies) != 1 {
				t.Fatalf("vendor calls = %d, want 1", len(tr.bodies))
			}
			body := tr.bodies[0]
			if !bytes.Contains(body, []byte(directionPrompt)) || !bytes.Contains(body, []byte(directionOutput)) {
				t.Errorf("output request lacks the prompt or the output: %s", body)
			}
			for _, earlier := range []string{systemPrompt, earlierTurn, earlierReply} {
				if bytes.Contains(body, []byte(earlier)) {
					t.Errorf("output request carries %q: %s", earlier, body)
				}
			}
		})
	}
}

// With no user message to pair it with (no request, or only a system prompt),
// the output is still sent, next to an empty user turn.
func TestOutputRequestWithoutUserPrompt(t *testing.T) {
	vendors := []struct {
		name  string
		cfg   map[string]interface{}
		reply string
	}{
		{"aporia", map[string]interface{}{"provider": "aporia", "endpoint": "http://vendor.test", "project_id": "proj-1"}, `{"action":"passthrough"}`},
		{"dynamoai", map[string]interface{}{"provider": "dynamoai", "endpoint": "http://vendor.test/moderation/analyze"}, `{"finalAction":"NONE"}`},
		{"llama_guard", map[string]interface{}{"provider": "llama_guard", "endpoint": "http://vendor.test"}, `{"choices":[{"message":{"content":"safe"}}]}`},
	}
	requests := []struct {
		name    string
		request *models.ChatCompletionRequest
	}{
		{"no request", nil},
		{"system prompt only", &models.ChatCompletionRequest{Model: "gpt-4o", Messages: []models.Message{directionMessage("system", "Be brief.")}}},
	}
	for _, v := range vendors {
		for _, rq := range requests {
			t.Run(v.name+"/"+rq.name, func(t *testing.T) {
				tr := &captureTransport{reply: v.reply}
				g := newCapturedGuardrail(t, v.cfg, tr)
				input := directionInput(true)
				input.Request = rq.request

				if res := g.Check(context.Background(), input); !res.Pass {
					t.Fatalf("check did not pass: %s", res.Message)
				}
				if len(tr.bodies) != 1 {
					t.Fatalf("vendor calls = %d, want 1", len(tr.bodies))
				}
				var body interface{}
				if err := json.Unmarshal(tr.bodies[0], &body); err != nil {
					t.Fatalf("body is not JSON: %v", err)
				}
				if !bytes.Contains(tr.bodies[0], []byte(directionOutput)) {
					t.Errorf("output request lacks the output: %s", tr.bodies[0])
				}
				if content, ok := firstUserContent(body); !ok || content != "" {
					t.Errorf("user turn = %q (found %v), want an empty user turn: %s", content, ok, tr.bodies[0])
				}
				if bytes.Contains(tr.bodies[0], []byte("Be brief.")) {
					t.Errorf("output request carries the system prompt: %s", tr.bodies[0])
				}
			})
		}
	}
}

// firstUserContent returns the content of the first user message in a body's
// "messages" list.
func firstUserContent(body interface{}) (string, bool) {
	msgs, _ := jsonField(body, "messages").([]interface{})
	for _, m := range msgs {
		if mm, ok := m.(map[string]interface{}); ok && mm["role"] == "user" {
			s, _ := mm["content"].(string)
			return s, true
		}
	}
	return "", false
}

// A retry must rebuild the output request: a sent request's body is spent.
func TestOutputRequestRebuiltOnRetry(t *testing.T) {
	tr := &captureTransport{statuses: []int{http.StatusServiceUnavailable}, reply: `{"action":"NONE"}`}
	g := newCapturedGuardrail(t, map[string]interface{}{
		"provider":     "bedrock_guardrails",
		"endpoint":     "http://vendor.test",
		"guardrail_id": "gr-1",
		"retry":        1,
	}, tr)

	if res := g.Check(context.Background(), directionInput(true)); !res.Pass {
		t.Fatalf("check did not pass after retry: %s", res.Message)
	}
	if len(tr.bodies) != 2 {
		t.Fatalf("attempts = %d, want 2", len(tr.bodies))
	}
	if !bytes.Equal(tr.bodies[0], tr.bodies[1]) || !bytes.Contains(tr.bodies[1], []byte(`"source":"OUTPUT"`)) {
		t.Errorf("retry sent %s after %s", tr.bodies[1], tr.bodies[0])
	}
}
