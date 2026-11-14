package realtime

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/gorilla/websocket"
)

type speechCheck struct {
	stage guardrails.Stage
	seen  *[]string
}

func (g speechCheck) Name() string            { return "speech-check" }
func (g speechCheck) Stage() guardrails.Stage { return g.stage }
func (g speechCheck) Check(_ context.Context, in *guardrails.CheckInput) *guardrails.CheckResult {
	var text string
	var content json.RawMessage
	if in.Response != nil {
		content = in.Response.Choices[0].Message.Content
	} else {
		content = in.Request.Messages[0].Content
	}
	if err := json.Unmarshal(content, &text); err != nil {
		return &guardrails.CheckResult{Pass: false, Err: err}
	}
	*g.seen = append(*g.seen, text)
	return &guardrails.CheckResult{Pass: !strings.Contains(text, "prohibited")}
}
func speechInspector(t *testing.T, vendor, stage string) (*SpeechGuardrails, *[]string) {
	t.Helper()
	seen := []string{}
	st := guardrails.StagePre
	if stage == "post" {
		st = guardrails.StagePost
	}
	g := speechCheck{st, &seen}
	registry := map[string]guardrails.Guardrail{g.Name(): g}
	engine := guardrails.NewEngine(config.GuardrailsConfig{DefaultTimeout: time.Second, Rules: []config.GuardrailRuleConfig{{Name: g.Name(), Stage: stage, Action: "block"}}}, registry)
	plugin := guardrails.NewPlugin(engine, registry, nil, nil, nil)
	rc := models.AcquireRequestContext()
	defer rc.Release()
	p, err := plugin.BindRealtime(rc)
	if err != nil {
		t.Fatal(err)
	}
	return NewSpeechGuardrails(context.Background(), p, vendor), &seen
}
func TestSpeechGuardrailSynthesisBoundaries(t *testing.T) {
	for _, vendor := range []string{"deepgram", "cartesia"} {
		t.Run(vendor, func(t *testing.T) {
			g, seen := speechInspector(t, vendor, "pre")
			events := []string{`{"type":"Speak","text":"pro"}`, `{"type":"Speak","text":"hibited"}`, `{"type":"Flush"}`}
			if vendor == "cartesia" {
				events = []string{`{"context_id":"a","transcript":"pro","continue":true}`, `{"context_id":"a","transcript":"hibited","continue":false}`}
			}
			for i, event := range events {
				frames, err := g.Client(websocket.TextMessage, []byte(event))
				if len(frames) != 0 {
					t.Fatalf("unapproved split text released at event %d", i)
				}
				if i == len(events)-1 {
					if err == nil {
						t.Fatal("combined prohibited text was not refused")
					}
				} else if err != nil {
					t.Fatal(err)
				}
			}
			if len(*seen) != 1 || (*seen)[0] != "prohibited" {
				t.Fatalf("checked units=%v", *seen)
			}
			if len(g.pending) != 0 || g.bytes != 0 || g.frames != 0 {
				t.Fatal("completed unit retained")
			}
		})
	}
}
func TestSpeechGuardrailCanonicalUtteranceEndAndAlternatives(t *testing.T) {
	g, seen := speechInspector(t, "deepgram", "post")
	for _, event := range []string{
		`{"type":"Results","is_final":true,"channel_index":[0,1],"channel":{"alternatives":[{"transcript":"hello"},{"transcript":"pro"}]}}`,
		`{"type":"Results","is_final":true,"channel_index":[0,1],"channel":{"alternatives":[{"transcript":" world"},{"transcript":"hibited"}]}}`,
	} {
		frames, err := g.Provider(websocket.TextMessage, []byte(event))
		if err != nil || len(frames) != 0 {
			t.Fatalf("premature release=%v err=%v", frames, err)
		}
	}
	frames, err := g.Provider(websocket.TextMessage, []byte(`{"type":"UtteranceEnd","channel":[0,1],"last_word_end":2}`))
	if err == nil || len(frames) != 0 {
		t.Fatal("blocked alternative reached client")
	}
	if len(*seen) != 2 || (*seen)[0] != "hello world" || (*seen)[1] != "prohibited" {
		t.Fatalf("alternatives checked=%v", *seen)
	}
	if len(g.pending) != 0 || g.bytes != 0 || g.frames != 0 {
		t.Fatal("utterance retained")
	}
}
func TestSpeechGuardrailInterleavedContexts(t *testing.T) {
	g, seen := speechInspector(t, "cartesia", "pre")
	if frames, err := g.Client(1, []byte(`{"context_id":"blocked","transcript":"pro","continue":true}`)); err != nil || len(frames) != 0 {
		t.Fatal(frames, err)
	}
	if frames, err := g.Client(1, []byte(`{"context_id":"allowed","transcript":"hello"}`)); err != nil || len(frames) != 1 {
		t.Fatal(frames, err)
	}
	if frames, err := g.Client(1, []byte(`{"context_id":"blocked","transcript":"hibited"}`)); err == nil || len(frames) != 0 {
		t.Fatal("interleaved prohibited unit escaped")
	}
	if len(*seen) != 2 || (*seen)[0] != "hello" || (*seen)[1] != "prohibited" {
		t.Fatal(*seen)
	}
}
func TestSpeechGuardrailBoundsAndUnsupportedFrames(t *testing.T) {
	for _, kind := range []string{"bytes", "frames", "binary", "unknown", "invalid"} {
		t.Run(kind, func(t *testing.T) {
			g, _ := speechInspector(t, "deepgram", "pre")
			var err error
			switch kind {
			case "bytes":
				_, err = g.Client(1, []byte(`{"type":"Speak","text":"`+strings.Repeat("a", maxGuardrailBytes)+`"}`))
			case "frames":
				for i := 0; i <= maxGuardrailFrames; i++ {
					_, err = g.Client(1, []byte(`{"type":"Speak","text":""}`))
					if err != nil {
						break
					}
				}
			case "binary":
				_, err = g.Client(2, []byte{0})
			case "unknown":
				_, err = g.Client(1, []byte(`{"type":"unknown","text":"prohibited"}`))
			case "invalid":
				_, err = g.Client(1, []byte(`{"type":`))
			}
			if err == nil {
				t.Fatal("unsupported/bounded content must refuse")
			}
		})
	}
}
func TestSpeechGuardrailClearDiscardsHeldText(t *testing.T) {
	g, seen := speechInspector(t, "deepgram", "pre")
	if frames, err := g.Client(1, []byte(`{"type":"Speak","text":"prohibited"}`)); err != nil || len(frames) != 0 {
		t.Fatal("pending text was not held", frames, err)
	}
	frames, err := g.Client(1, []byte(`{"type":"Clear"}`))
	if err != nil || len(frames) != 1 || len(*seen) != 0 || g.bytes != 0 || g.frames != 0 || len(g.pending) != 0 {
		t.Fatal("Clear did not discard pending text", frames, err, *seen)
	}
}
