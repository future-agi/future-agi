package server

import (
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/guardrails/expression"
	"github.com/gorilla/websocket"
)

// Cartesia synthesis names its model in each request, not in the URL, so a
// model-dependent rule must see the model of the unit being checked.
func TestSpeechGuardrailsCartesiaRequestModel(t *testing.T) {
	for _, tc := range []struct {
		name     string
		allowed  []string
		denied   []string
		received []string
	}{
		{
			name:   "named_model",
			denied: []string{`{"model_id":"sonic-3","transcript":"hello","context_id":"turn"}`},
		},
		{
			name:     "model_switch",
			allowed:  []string{`{"model_id":"sonic-2","transcript":"hello","context_id":"first"}`},
			denied:   []string{`{"model_id":"sonic-3","transcript":"hello","context_id":"second"}`},
			received: []string{`{"model_id":"sonic-2","transcript":"hello","context_id":"first"}`},
		},
		{
			name: "model_switch_inside_context",
			denied: []string{
				`{"model_id":"sonic-2","transcript":"hel","context_id":"turn","continue":true}`,
				`{"model_id":"sonic-3","transcript":"lo","context_id":"turn"}`,
			},
		},
		{
			name: "continuation_without_model",
			denied: []string{
				`{"model_id":"sonic-3","transcript":"hel","context_id":"turn","continue":true}`,
				`{"transcript":"lo","context_id":"turn"}`,
			},
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			check := expression.New("speech-test", map[string]interface{}{"expression": `request.model == "cartesia/sonic-3"`})
			f := newSpeechFixture(t, "cartesia", "pre", "static", "block", nil, speechRule(check, 0))
			client, _, err := f.dial(t, "/cartesia/tts/websocket", nil)
			if err != nil {
				t.Fatal(err)
			}
			defer func() { _ = client.Close() }()
			for _, m := range tc.allowed {
				if err := client.WriteMessage(websocket.TextMessage, []byte(m)); err != nil {
					t.Fatal(err)
				}
				if err := client.SetReadDeadline(time.Now().Add(3 * time.Second)); err != nil {
					t.Fatal(err)
				}
				if _, data, err := client.ReadMessage(); err != nil || string(data) != `{"type":"ack"}` {
					t.Fatalf("allowed model refused: %s %v", data, err)
				}
			}
			for _, m := range tc.denied {
				if err := client.WriteMessage(websocket.TextMessage, []byte(m)); err != nil {
					t.Fatal(err)
				}
			}
			requireSpeechClose(t, client, "realtime_guardrail_blocked")
			f.mu.Lock()
			defer f.mu.Unlock()
			if len(f.received) != len(tc.received) {
				t.Fatalf("denied model reached provider: %v", f.received)
			}
			for i := range tc.received {
				if f.received[i] != tc.received[i] {
					t.Fatalf("provider frames=%v want=%v", f.received, tc.received)
				}
			}
		})
	}
}

// The provider and the session meter read the exact model_id key, so a case
// alias must not give the check a different model.
func TestSpeechGuardrailsCartesiaModelAlias(t *testing.T) {
	check := expression.New("speech-test", map[string]interface{}{"expression": `request.model == "cartesia/sonic-3"`})
	f := newSpeechFixture(t, "cartesia", "pre", "static", "block", nil, speechRule(check, 0))
	client, _, err := f.dial(t, "/cartesia/tts/websocket", nil)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = client.Close() }()
	if err := client.WriteMessage(websocket.TextMessage, []byte(`{"model_id":"sonic-3","Model_ID":"sonic-2","transcript":"hello"}`)); err != nil {
		t.Fatal(err)
	}
	requireSpeechClose(t, client, "realtime_guardrail_unsupported")
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.received) != 0 {
		t.Fatalf("ambiguous model reached provider: %v", f.received)
	}
}
