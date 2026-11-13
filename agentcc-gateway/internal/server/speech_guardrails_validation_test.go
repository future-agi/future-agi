package server

import (
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/validation"
	"github.com/gorilla/websocket"
)

// Transcripts are checked once, as the response text, by the input-validation
// checker used for a static post-stage rule.
func TestSpeechTranscriptValidationSeesTheTextOnce(t *testing.T) {
	sixty := strings.Repeat("a", 60)
	for _, tc := range []struct {
		name, text string
		rule       map[string]interface{}
		blocked    bool
	}{
		{"anchored deny pattern blocks", "prohibited", map[string]interface{}{"deny_patterns": []interface{}{"^prohibited$"}}, true},
		{"anchored deny pattern allows other text", "hello", map[string]interface{}{"deny_patterns": []interface{}{"^prohibited$"}}, false},
		{"total length within limit is allowed", sixty, map[string]interface{}{"max_total_length": 100}, false},
		{"total length over limit blocks", sixty, map[string]interface{}{"max_total_length": 50}, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			final := `{"type":"transcript","text":"` + tc.text + `","is_final":true}`
			f := newSpeechFixture(t, "cartesia", "post", "static", "block", []string{final},
				func(_ *config.GuardrailsConfig, checks map[string]guardrails.Guardrail) {
					checks["speech-test"] = validation.New(tc.rule)
				})
			client, _, err := f.dial(t, "/cartesia/stt/websocket?encoding=pcm_s16le&sample_rate=16000", nil)
			if err != nil {
				t.Fatal(err)
			}
			defer func() { _ = client.Close() }()
			if err := client.WriteMessage(websocket.BinaryMessage, []byte{0, 0}); err != nil {
				t.Fatal(err)
			}
			if err := client.SetReadDeadline(time.Now().Add(3 * time.Second)); err != nil {
				t.Fatal(err)
			}
			_, data, err := client.ReadMessage()
			if tc.blocked {
				var closeErr *websocket.CloseError
				if !errors.As(err, &closeErr) || closeErr.Code != 1008 || closeErr.Text != "realtime_guardrail_blocked" {
					t.Fatalf("transcript must close 1008 before delivery: data=%s err=%v", data, err)
				}
				return
			}
			if err != nil || string(data) != final {
				t.Fatalf("final=%s err=%v want=%s", data, err, final)
			}
		})
	}
}
