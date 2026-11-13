package server

import (
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/blocklist"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/external"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/futureagi"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/webhook"
	"github.com/gorilla/websocket"
)

func speechRule(check guardrails.Guardrail, threshold float64) func(*config.GuardrailsConfig, map[string]guardrails.Guardrail) {
	return func(cfg *config.GuardrailsConfig, checks map[string]guardrails.Guardrail) {
		checks["speech-test"] = check
		cfg.Rules[0].Threshold = threshold
	}
}

func requireSpeechClose(t *testing.T, client *websocket.Conn, reason string) {
	t.Helper()
	if err := client.SetReadDeadline(time.Now().Add(3 * time.Second)); err != nil {
		t.Fatal(err)
	}
	_, data, err := client.ReadMessage()
	var closed *websocket.CloseError
	if len(data) != 0 || !errors.As(err, &closed) || closed.Code != 1008 || closed.Text != reason {
		t.Fatalf("unapproved frame escaped: data=%q close=%v want=1008/%s", data, err, reason)
	}
}

func TestSpeechGuardrailsAmbiguousClientJSON(t *testing.T) {
	for _, tc := range []struct{ name, vendor, path, message string }{
		{"cartesia_alias", "cartesia", "/cartesia/tts/websocket", `{"model_id":"sonic-3","transcript":"prohibited","Transcript":"hello"}`},
		{"cartesia_duplicate", "cartesia", "/cartesia/tts/websocket", `{"model_id":"sonic-3","transcript":"prohibited","transcript":"hello"}`},
		{"deepgram_alias", "deepgram", "/deepgram/v1/speak?model=aura-2-thalia-en", `{"type":"Speak","text":"prohibited","Text":"hello"}`},
		{"deepgram_duplicate", "deepgram", "/deepgram/v1/speak?model=aura-2-thalia-en", `{"type":"Speak","text":"prohibited","text":"hello"}`},
		{"type_alias", "deepgram", "/deepgram/v1/speak?model=aura-2-thalia-en", `{"type":"Speak","Type":"Flush","text":"prohibited"}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f := newSpeechFixture(t, tc.vendor, "pre", "static", "block", nil)
			client, _, err := f.dial(t, tc.path, nil)
			if err != nil {
				t.Fatal(err)
			}
			defer func() { _ = client.Close() }()
			if err := client.WriteMessage(1, []byte(tc.message)); err != nil {
				t.Fatal(err)
			}
			if tc.vendor == "deepgram" {
				_ = client.WriteMessage(1, []byte(`{"type":"Flush"}`))
			}
			requireSpeechClose(t, client, "realtime_guardrail_unsupported")
			f.mu.Lock()
			defer f.mu.Unlock()
			if len(f.received) != 0 {
				t.Fatalf("unapproved original reached peer: %v", f.received)
			}
		})
	}
}

func TestSpeechGuardrailsAmbiguousProviderJSON(t *testing.T) {
	for _, tc := range []struct{ name, vendor, message string }{
		{"cartesia_alias", "cartesia", `{"type":"transcript","is_final":true,"text":"prohibited","Text":"hello"}`},
		{"cartesia_duplicate", "cartesia", `{"type":"transcript","is_final":true,"text":"prohibited","text":"hello"}`},
		{"nested_alias", "deepgram", `{"type":"Results","is_final":true,"speech_final":true,"channel":{"alternatives":[{"transcript":"prohibited","Transcript":"hello"}]}}`},
		{"nested_duplicate", "deepgram", `{"type":"Results","is_final":true,"speech_final":true,"channel":{"alternatives":[{"transcript":"prohibited","transcript":"hello"}]}}`},
		{"channel_alias", "deepgram", `{"type":"Results","is_final":true,"speech_final":true,"channel":{"alternatives":[{"transcript":"prohibited"}]},"Channel":{"alternatives":[{"transcript":"hello"}]}}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f := newSpeechFixture(t, tc.vendor, "post", "static", "block", []string{tc.message})
			path := "/cartesia/stt/websocket?encoding=pcm_s16le&sample_rate=16000"
			if tc.vendor == "deepgram" {
				path = "/deepgram/v1/listen?encoding=linear16&sample_rate=16000"
			}
			client, _, err := f.dial(t, path, nil)
			if err != nil {
				t.Fatal(err)
			}
			defer func() { _ = client.Close() }()
			if err := client.WriteMessage(2, []byte{0, 0}); err != nil {
				t.Fatal(err)
			}
			requireSpeechClose(t, client, "realtime_guardrail_unsupported")
		})
	}
}

func TestSpeechGuardrailsFinalWordBoundaries(t *testing.T) {
	for _, tc := range []struct {
		name, first, second, word string
		blocked                   bool
	}{
		{"whole_word", "hello", "prohibited", "prohibited", true},
		{"cross_segment_phrase", "not", "allowed", "not allowed", true},
		{"split_token", "pro", "hibited", "prohibited", true},
		{"positive", "hello", "world", "prohibited", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			messages := []string{
				fmt.Sprintf(`{"type":"Results","is_final":true,"channel_index":[0,1],"channel":{"alternatives":[{"transcript":%q}]}}`, tc.first),
				fmt.Sprintf(`{"type":"Results","is_final":true,"speech_final":true,"channel_index":[0,1],"channel":{"alternatives":[{"transcript":%q}]}}`, tc.second),
			}
			check := blocklist.New(map[string]interface{}{"words": []interface{}{tc.word}, "whole_word": true})
			f := newSpeechFixture(t, "deepgram", "post", "static", "block", messages, speechRule(check, 0))
			client, _, err := f.dial(t, "/deepgram/v1/listen?encoding=linear16&sample_rate=16000", nil)
			if err != nil {
				t.Fatal(err)
			}
			defer func() { _ = client.Close() }()
			if err := client.WriteMessage(2, []byte{0, 0}); err != nil {
				t.Fatal(err)
			}
			if tc.blocked {
				requireSpeechClose(t, client, "realtime_guardrail_blocked")
				return
			}
			for _, want := range messages {
				if err := client.SetReadDeadline(time.Now().Add(3 * time.Second)); err != nil {
					t.Fatal(err)
				}
				_, got, err := client.ReadMessage()
				if err != nil || string(got) != want {
					t.Fatalf("approved final=%q err=%v want=%s", got, err, want)
				}
			}
		})
	}
}

func TestSpeechGuardrailsWebhookAvailability(t *testing.T) {
	for _, tc := range []struct {
		name    string
		status  int
		body    string
		blocked bool
	}{
		{"http500", 500, "synthetic outage", true},
		{"malformed", 200, `{`, true},
		{"missing_verdict", 200, `{}`, true},
		{"null_verdict", 200, `{"pass":null}`, true},
		{"invalid_score", 200, `{"pass":true,"score":2}`, true},
		{"approved", 200, `{"pass":true,"score":0}`, false},
		{"genuine_threshold", 200, `{"pass":false,"score":1}`, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			dependency := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(tc.status)
				if _, err := io.WriteString(w, tc.body); err != nil {
					t.Error(err)
				}
			}))
			defer dependency.Close()
			check := webhook.New("speech-test", map[string]interface{}{"url": dependency.URL})
			f := newSpeechFixture(t, "cartesia", "pre", "static", "block", nil, speechRule(check, 1))
			client, _, err := f.dial(t, "/cartesia/tts/websocket", nil)
			if err != nil {
				t.Fatal(err)
			}
			defer func() { _ = client.Close() }()
			if err := client.WriteMessage(1, []byte(`{"model_id":"sonic-3","transcript":"hello"}`)); err != nil {
				t.Fatal(err)
			}
			if tc.blocked {
				requireSpeechClose(t, client, "guardrail_unavailable")
				f.mu.Lock()
				defer f.mu.Unlock()
				if len(f.received) != 0 {
					t.Fatalf("unchecked text delivered: %v", f.received)
				}
			} else {
				if err := client.SetReadDeadline(time.Now().Add(3 * time.Second)); err != nil {
					t.Fatal(err)
				}
				_, data, err := client.ReadMessage()
				if err != nil || string(data) != `{"type":"ack"}` {
					t.Fatalf("valid verdict refused: %s %v", data, err)
				}
			}
		})
	}
}

func TestSpeechGuardrailsUnavailableConfigurationBeforeDial(t *testing.T) {
	t.Setenv("FI_API_KEY", "")
	t.Setenv("FI_SECRET_KEY", "")
	for _, tc := range []struct {
		name   string
		check  guardrails.Guardrail
		status int
		code   string
	}{
		{"credentials", futureagi.New("speech-test", map[string]interface{}{"base_url": "http://127.0.0.1:9", "eval_id": "synthetic"}), 503, "guardrail_unavailable"},
		{"eval_ids", futureagi.New("speech-test", map[string]interface{}{"base_url": "http://127.0.0.1:9", "api_key": "synthetic", "secret_key": "synthetic"}), 503, "guardrail_unavailable"},
		{"webhook_url", webhook.New("speech-test", nil), 503, "guardrail_unavailable"},
		{"external_unvalidated", external.New("speech-test", nil), 403, "realtime_guardrail_unsupported"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f := newSpeechFixture(t, "cartesia", "pre", "static", "block", nil, speechRule(tc.check, 1))
			client, resp, err := f.dial(t, "/cartesia/tts/websocket", nil)
			if client != nil {
				_ = client.Close()
			}
			if resp == nil {
				t.Fatalf("no refusal response: %v", err)
			}
			defer func() { _ = resp.Body.Close() }()
			body, _ := io.ReadAll(resp.Body)
			if err == nil || resp.StatusCode != tc.status || !strings.Contains(string(body), `"code":"`+tc.code+`"`) || !strings.Contains(string(body), `"type":"guardrail_error"`) {
				t.Fatalf("unavailable admitted: status=%d body=%s err=%v", resp.StatusCode, body, err)
			}
			f.mu.Lock()
			defer f.mu.Unlock()
			if f.dials != 0 || len(f.received) != 0 {
				t.Fatalf("unavailable configuration reached peer: dials=%d frames=%v", f.dials, f.received)
			}
		})
	}
}
