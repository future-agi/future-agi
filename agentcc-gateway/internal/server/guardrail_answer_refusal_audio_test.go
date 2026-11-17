package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The checks on the answer read a model's refusal and an audio answer's
// transcript as they read its content: a refusal or a transcript a check
// passes is served (refusal and audio intact), and one a block-mode check
// matches is refused as content would be (content_blocked). Only audio with
// no transcript is unreadable: a block-mode check refuses it unread
// (answer_unavailable), and a check in log mode lets it through. Both a
// plain and a streamed answer, under data-leakage-prevention as a rule in
// the config file.

// refusalAudioMessage is a chat answer's message (as JSON) and, for a
// stream, the delta that carries the same fields.
type refusalAudioMessage struct {
	name    string
	message string // non-stream: choices[0].message
	delta   string // stream: the delta after {"role":"assistant"}
	text    string // the refusal or transcript as sent
	mark    string // a piece of the answer a served body shows
}

func refusalAudioCases() map[string]refusalAudioMessage {
	quote := func(s string) string { b, _ := json.Marshal(s); return string(b) }
	audio := func(transcript string) string {
		a := `{"id":"audio_fixture","data":"SGVsbG8=","expires_at":1900000000`
		if transcript != "" {
			a += `,"transcript":` + quote(transcript)
		}
		return a + `}`
	}
	return map[string]refusalAudioMessage{
		"refusal": {"a refusal a check passes",
			`{"role":"assistant","content":null,"refusal":"I can't help with that request."}`,
			`{"refusal":"I can't help with that request."}`, "I can't help with that request.", "help with that"},
		"refusal-leak": {"a refusal a check matches",
			`{"role":"assistant","content":null,"refusal":` + quote(postBlockLeak) + `}`,
			`{"refusal":` + quote(postBlockLeak) + `}`, postBlockLeak, "DROP TABLE"},
		"audio": {"audio with a transcript a check passes",
			`{"role":"assistant","audio":` + audio("Hello there.") + `}`,
			`{"audio":` + audio("Hello there.") + `}`, "Hello there.", "Hello there"},
		"audio-leak": {"audio with a transcript a check matches",
			`{"role":"assistant","audio":` + audio(postBlockLeak) + `}`,
			`{"audio":` + audio(postBlockLeak) + `}`, postBlockLeak, "DROP TABLE"},
		"audio-bare": {"audio with no transcript",
			`{"role":"assistant","audio":` + audio("") + `}`,
			`{"audio":` + audio("") + `}`, "", "SGVsbG8="},
	}
}

// refusalAudioUpstream answers a chat completion with m, as JSON or as a
// stream.
func refusalAudioUpstream(m refusalAudioMessage) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/chat/completions" {
			return
		}
		var req models.ChatCompletionRequest
		_ = json.NewDecoder(r.Body).Decode(&req)
		model, _ := json.Marshal(req.Model)
		if !req.Stream {
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprintf(w, `{"id":"c1","object":"chat.completion","created":1760000000,"model":%s,"choices":[{"index":0,"message":%s,"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":9,"total_tokens":14}}`, model, m.message)
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		flusher := w.(http.Flusher)
		for _, data := range []string{
			fmt.Sprintf(`{"id":"c1","object":"chat.completion.chunk","created":1760000000,"model":%s,"choices":[{"index":0,"delta":{"role":"assistant"}}]}`, model),
			fmt.Sprintf(`{"id":"c1","object":"chat.completion.chunk","created":1760000000,"model":%s,"choices":[{"index":0,"delta":%s}]}`, model, m.delta),
			fmt.Sprintf(`{"id":"c1","object":"chat.completion.chunk","created":1760000000,"model":%s,"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":9,"total_tokens":14}}`, model),
			"[DONE]",
		} {
			fmt.Fprintf(w, "data: %s\n\n", data)
			flusher.Flush()
		}
	}
}

func TestOutputChecksReadRefusalAndTranscript(t *testing.T) {
	cases := refusalAudioCases()
	for _, tc := range []struct {
		message string
		action  string
		want    string // "served", or the refusal's code
	}{
		{"refusal", "block", "served"},
		{"refusal-leak", "block", "content_blocked"},
		{"audio", "block", "served"},
		{"audio-leak", "block", "content_blocked"},
		{"audio-bare", "block", "answer_unavailable"},
		{"audio-bare", "log", "served"},
		{"refusal-leak", "log", "served"},
	} {
		m := cases[tc.message]
		for _, stream := range []bool{false, true} {
			t.Run(m.name+" / "+tc.action+" / "+streamLabel(stream), func(t *testing.T) {
				g := newPostBlockGateway(t, postBlockSetup{staticRule: leakageCheck, action: tc.action,
					capture: true, upstream: refusalAudioUpstream(m)})
				code, body := g.chat(t, stream)
				if tc.want == "served" {
					if code != http.StatusOK || !strings.Contains(body, m.mark) {
						t.Fatalf("status %d, body %s; want 200 with the answer", code, body)
					}
					if _, at := streamError(sseEvents(body)); stream && at >= 0 {
						t.Fatalf("a served stream got an error event; body %s", body)
					}
					if !stream {
						var resp models.ChatCompletionResponse
						if err := json.Unmarshal([]byte(body), &resp); err != nil || len(resp.Choices) != 1 {
							t.Fatalf("body %s is not the answer: %v", body, err)
						}
						msg := resp.Choices[0].Message
						switch {
						case strings.HasPrefix(tc.message, "refusal") && (msg.Refusal == nil || *msg.Refusal != m.text):
							t.Errorf("the served refusal is %v, want %q", msg.Refusal, m.text)
						case strings.HasPrefix(tc.message, "audio") && (msg.Audio == nil || msg.Audio.Data != "SGVsbG8="):
							t.Errorf("the served audio is %+v, want it as sent", msg.Audio)
						}
					}
					return
				}
				if strings.Contains(body, "DROP TABLE") || strings.Contains(body, "SGVsbG8=") {
					t.Errorf("the refused answer was served: status %d, body %s", code, body)
				}
				if stream {
					if code != http.StatusOK {
						t.Fatalf("status %d, want the held stream's 200; body %s", code, body)
					}
					assertStreamEndsWithGuardrailError(t, body, tc.want)
					return
				}
				var eb errorBody
				if err := json.Unmarshal([]byte(body), &eb); err != nil || code != http.StatusForbidden {
					t.Fatalf("status %d body %s, want 403 %s", code, body, tc.want)
				}
				if eb.Error.Type != models.ErrTypeGuardrail || eb.Error.Code != tc.want {
					t.Errorf("error %+v, want guardrail_error %s", eb.Error, tc.want)
				}
				if tc.want == "content_blocked" && !strings.Contains(eb.Error.Message, leakageCheck) {
					t.Errorf("error %+v, want it to name %s", eb.Error, leakageCheck)
				}
			})
		}
	}
}
