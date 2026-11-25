package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/guardrails/futureagi"
)

// Realtime evaluation checks accept a verdict only when its template identity
// and result envelope are unambiguous, as before templates were requested
// one at a time.
func TestSpeechGuardrailsEvaluationIdentity(t *testing.T) {
	for _, tc := range []struct {
		name   string
		ids    []string
		reply  func(ids []string) interface{}
		reason string
	}{
		{"identified", []string{"tone", "toxicity"}, func(ids []string) interface{} {
			return []interface{}{map[string]interface{}{"evaluations": passedEvaluations(ids)}}
		}, ""},
		{"single_unidentified", []string{"tone"}, func(ids []string) interface{} {
			return []interface{}{map[string]interface{}{"evaluations": []interface{}{map[string]interface{}{"output": "Passed"}}}}
		}, ""},
		{"several_unidentified", []string{"tone", "toxicity"}, func(ids []string) interface{} {
			return []interface{}{map[string]interface{}{"evaluations": []interface{}{map[string]interface{}{"output": "Passed"}}}}
		}, "guardrail_unavailable"},
		{"extra_result_group", []string{"tone"}, func(ids []string) interface{} {
			return []interface{}{
				map[string]interface{}{"evaluations": []interface{}{}},
				map[string]interface{}{"evaluations": passedEvaluations(ids)},
			}
		}, "guardrail_unavailable"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			dependency := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				var body struct {
					Config map[string]json.RawMessage `json:"config"`
				}
				if err := json.NewDecoder(r.Body).Decode(&body); err != nil || len(body.Config) == 0 {
					t.Errorf("unexpected evaluation request: %v %v", body.Config, err)
				}
				var requested []string
				for key := range body.Config {
					requested = append(requested, key)
				}
				if err := json.NewEncoder(w).Encode(map[string]interface{}{"result": tc.reply(requested)}); err != nil {
					t.Error(err)
				}
			}))
			defer dependency.Close()
			ids := make([]interface{}, len(tc.ids))
			for i, id := range tc.ids {
				ids[i] = id
			}
			check := futureagi.New("speech-test", map[string]interface{}{"base_url": dependency.URL, "api_key": "synthetic", "secret_key": "synthetic", "eval_ids": ids})
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
					t.Fatalf("identified approval refused: %s %v", data, err)
				}
			}
			f.mu.Lock()
			defer f.mu.Unlock()
			if tc.reason != "" && len(f.received) != 0 {
				t.Fatalf("unapproved original delivered: %v", f.received)
			}
			if tc.reason == "" && (len(f.received) != 1 || f.received[0] != message) {
				t.Fatalf("approved original changed: %v", f.received)
			}
		})
	}
}

func passedEvaluations(ids []string) []interface{} {
	evaluations := make([]interface{}, len(ids))
	for i, id := range ids {
		evaluations[i] = map[string]interface{}{"evalId": id, "output": "Passed"}
	}
	return evaluations
}
