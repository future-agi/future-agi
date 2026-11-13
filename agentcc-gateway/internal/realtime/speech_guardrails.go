package realtime

import (
	"context"
	"encoding/json"
	"errors"
	"slices"
	"strconv"
	"strings"

	"github.com/gorilla/websocket"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

const (
	maxGuardrailBytes  = 1 << 20
	maxGuardrailFrames = 1024
)

// SpeechGuardrails inspects complete text units, not individual deltas. Synthesis
// buffers until Flush (Deepgram) or continue=false (Cartesia); transcription drops
// interim hypotheses and holds final segments until the utterance is complete.
// A session can have at most 1 MiB / 1024 frames pending across all contexts.
// Unknown protected event shapes fail closed rather than being forwarded blindly.
// Each direction is used by just one relay reader.
type SpeechGuardrails struct {
	ctx    context.Context
	policy *guardrails.RealtimePolicy
	vendor string
	// model is the model last named on a Cartesia synthesis connection, which a
	// request that names none inherits.
	model   string
	pending map[string]*speechText
	bytes   int
	frames  int
}

type speechText struct {
	frames []Frame
	text   string
	// models are the distinct models the unit's requests name; "" is the session's.
	models       []string
	alternatives []string
	utterances   []string
	segments     []string
}

func NewSpeechGuardrails(ctx context.Context, policy *guardrails.RealtimePolicy, vendor string) *SpeechGuardrails {
	return &SpeechGuardrails{ctx: ctx, policy: policy, vendor: vendor, pending: make(map[string]*speechText)}
}

func speechRefused() error { return errors.New("realtime_guardrail_unsupported") }

func (g *SpeechGuardrails) hold(id, text string, frame Frame) error {
	// Count both the raw frame and the decoded text; even empty contexts cost a frame.
	g.bytes += len(frame.Data) + len(text)
	g.frames++
	if g.bytes > maxGuardrailBytes || g.frames > maxGuardrailFrames {
		return speechRefused()
	}
	unit := g.pending[id]
	if unit == nil {
		unit = &speechText{}
		g.pending[id] = unit
	}
	unit.text += text
	unit.frames = append(unit.frames, frame)
	return nil
}

// take removes a pending unit and frees its share of the session bounds.
func (g *SpeechGuardrails) take(id string) *speechText {
	unit := g.pending[id]
	if unit == nil {
		return nil
	}
	delete(g.pending, id)
	g.bytes -= len(unit.text)
	for _, text := range append(append(append([]string{}, unit.alternatives...), unit.utterances...), unit.segments...) {
		g.bytes -= len(text)
	}
	for _, f := range unit.frames {
		g.bytes -= len(f.Data)
		g.frames--
	}
	return unit
}

func (g *SpeechGuardrails) release(id string, stage guardrails.Stage) ([]Frame, error) {
	unit := g.take(id)
	if unit == nil {
		return nil, nil
	}
	texts := append(append(append([]string{}, unit.alternatives...), unit.segments...), unit.utterances...)
	if unit.alternatives == nil {
		texts = []string{unit.text}
	}
	models := unit.models
	if len(models) == 0 {
		models = []string{""}
	}
	for _, text := range texts {
		for _, model := range models {
			if err := g.policy.CheckModel(g.ctx, stage, model, text); err != nil {
				return nil, err
			}
		}
	}
	return unit.frames, nil
}

// Client checks synthesis input before any part of that text unit reaches a vendor.
func (g *SpeechGuardrails) Client(kind int, data []byte) ([]Frame, error) {
	frame := Frame{kind, data}
	if !g.policy.Has(guardrails.StagePre) {
		return []Frame{frame}, nil
	}
	var event struct {
		Type       string  `json:"type"`
		Text       string  `json:"text"`
		Transcript *string `json:"transcript"`
		ModelID    string  `json:"model_id"`
		ContextID  string  `json:"context_id"`
		Continue   bool    `json:"continue"`
		Cancel     bool    `json:"cancel"`
	}
	if kind != websocket.TextMessage || validateSpeechJSON(data) != nil || json.Unmarshal(data, &event) != nil {
		return nil, speechRefused()
	}
	if g.vendor == "deepgram" {
		switch event.Type {
		case "Speak":
			return nil, g.hold("", event.Text, frame)
		case "Flush":
			if err := g.hold("", "", frame); err != nil {
				return nil, err
			}
			return g.release("", guardrails.StagePre)
		case "Clear", "Close":
			g.pending = make(map[string]*speechText)
			g.bytes, g.frames = 0, 0
			return []Frame{frame}, nil
		default:
			return nil, speechRefused()
		}
	}
	// Cartesia names the model in each request rather than the URL; check the unit
	// against every model its requests name.
	if event.ModelID != "" {
		g.model = g.vendor + "/" + event.ModelID
	}
	// Cancelling a context discards its unchecked text and carries none itself;
	// other contexts and the connection stay open. A cancel with text is refused.
	if event.Cancel && event.Transcript == nil {
		g.take(event.ContextID)
		return []Frame{frame}, nil
	}
	if event.Transcript == nil || event.Cancel {
		return nil, speechRefused()
	}
	if err := g.hold(event.ContextID, *event.Transcript, frame); err != nil {
		return nil, err
	}
	if unit := g.pending[event.ContextID]; !slices.Contains(unit.models, g.model) {
		unit.models = append(unit.models, g.model)
	}
	if event.Continue {
		return nil, nil
	}
	return g.release(event.ContextID, guardrails.StagePre)
}

// Provider checks every alternative of a final transcript before releasing it.
// Interim transcripts never reach the client when post guardrails are configured.
func (g *SpeechGuardrails) Provider(kind int, data []byte) ([]Frame, error) {
	frame := Frame{kind, data}
	if !g.policy.Has(guardrails.StagePost) {
		return []Frame{frame}, nil
	}
	var event struct {
		Type         string          `json:"type"`
		Text         string          `json:"text"`
		IsFinal      bool            `json:"is_final"`
		SpeechFinal  bool            `json:"speech_final"`
		Channel      json.RawMessage `json:"channel"`
		ChannelIndex []int           `json:"channel_index"`
	}
	if kind != websocket.TextMessage || validateSpeechJSON(data) != nil || json.Unmarshal(data, &event) != nil {
		return nil, speechRefused()
	}
	if g.vendor == "deepgram" {
		switch event.Type {
		case "Results":
			var channel struct {
				Alternatives []struct {
					Transcript string `json:"transcript"`
				} `json:"alternatives"`
			}
			if json.Unmarshal(event.Channel, &channel) != nil || len(channel.Alternatives) == 0 {
				return nil, speechRefused()
			}
			if !event.IsFinal {
				return nil, nil
			}
			id := "0"
			if len(event.ChannelIndex) > 0 {
				id = strconv.Itoa(event.ChannelIndex[0])
			}
			if err := g.hold(id, "", frame); err != nil {
				return nil, err
			}
			unit := g.pending[id]
			if unit.alternatives == nil {
				unit.alternatives = make([]string, len(channel.Alternatives))
				unit.utterances = make([]string, len(channel.Alternatives))
			}
			if len(unit.alternatives) != len(channel.Alternatives) {
				return nil, speechRefused()
			}
			for i, alt := range channel.Alternatives {
				unit.alternatives[i] += alt.Transcript
				g.bytes += len(alt.Transcript)
				// Final segments are words/phrases, not token deltas. Check each
				// original segment and a word-separated utterance as well as the
				// legacy concatenation, retaining cross-segment checks.
				segment := strings.TrimSpace(alt.Transcript)
				unit.segments = append(unit.segments, alt.Transcript)
				g.bytes += len(alt.Transcript)
				if segment != "" {
					if unit.utterances[i] != "" {
						unit.utterances[i] += " "
						g.bytes++
					}
					unit.utterances[i] += segment
					g.bytes += len(segment)
				}
			}
			if g.bytes > maxGuardrailBytes {
				return nil, speechRefused()
			}
			if event.SpeechFinal {
				return g.release(id, guardrails.StagePost)
			}
			return nil, nil
		case "UtteranceEnd":
			var channel []int
			if json.Unmarshal(event.Channel, &channel) != nil || len(channel) == 0 {
				return nil, speechRefused()
			}
			id := strconv.Itoa(channel[0])
			if err := g.hold(id, "", frame); err != nil {
				return nil, err
			}
			return g.release(id, guardrails.StagePost)
		case "Metadata", "SpeechStarted":
			return []Frame{frame}, nil
		default:
			return nil, speechRefused()
		}
	}
	// Cartesia STT emits complete text in is_final transcript events. The turns
	// protocol is refused at admission until it has a separately supported parser.
	if event.Type != "transcript" {
		return nil, speechRefused()
	}
	if !event.IsFinal {
		return nil, nil
	}
	if err := g.hold("", event.Text, frame); err != nil {
		return nil, err
	}
	return g.release("", guardrails.StagePost)
}
