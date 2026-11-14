// Package speech relays the streaming speech APIs of Deepgram and Cartesia.
//
// A client keeps speaking the vendor's own WebSocket protocol and only changes where it connects:
// the gateway serves each vendor's API under the vendor's name (/deepgram/v1/listen,
// /cartesia/tts/websocket), so the vendor SDKs need nothing but a base URL and the gateway key.
// The gateway connects upstream with the platform's credential and relays frames unchanged.
// Usage is metered from what the client sends, in the unit the vendor bills: seconds of audio
// for transcription, characters of text for synthesis.
package speech

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Kind is what an endpoint does.
type Kind string

const (
	Transcription Kind = "transcription"
	Synthesis     Kind = "synthesis"
)

type vendor struct {
	auth      func(header http.Header, key string)
	endpoints map[string]Kind // vendor path -> what it does
	// Client headers that are part of the vendor's protocol rather than a credential, and so
	// travel upstream unchanged.
	forward []string
}

var vendors = map[string]vendor{
	"deepgram": {
		auth: func(h http.Header, key string) { h.Set("Authorization", "Token "+key) },
		endpoints: map[string]Kind{
			"/v1/listen": Transcription,
			"/v1/speak":  Synthesis,
		},
	},
	"cartesia": {
		auth:    func(h http.Header, key string) { h.Set("X-API-Key", key) },
		forward: []string{"Cartesia-Version"},
		endpoints: map[string]Kind{
			"/tts/websocket":       Synthesis,
			"/stt/websocket":       Transcription,
			"/stt/turns/websocket": Transcription,
		},
	},
}

// Endpoint is one vendor API the gateway serves at /{Vendor}{Path}.
type Endpoint struct {
	Vendor string
	Path   string
}

// Endpoints lists every vendor API the gateway relays.
func Endpoints() []Endpoint {
	var out []Endpoint
	for name, v := range vendors {
		for path := range v.endpoints {
			out = append(out, Endpoint{Vendor: name, Path: path})
		}
	}
	sort.Slice(out, func(i, j int) bool {
		return out[i].Vendor+out[i].Path < out[j].Vendor+out[j].Path
	})
	return out
}

// Provider is a speech vendor reached over its own streaming API with the platform's key.
type Provider struct {
	id      string
	vendor  string
	baseURL string
	apiKey  string
	models  []string
}

// New creates a speech provider; cfg.APIFormat names the vendor ("deepgram" or "cartesia").
func New(id string, cfg config.ProviderConfig) (*Provider, error) {
	if _, ok := vendors[cfg.APIFormat]; !ok {
		return nil, fmt.Errorf("speech provider %q: unknown vendor %q", id, cfg.APIFormat)
	}
	if cfg.APIKey == "" {
		return nil, fmt.Errorf("speech provider %q: api_key is required", id)
	}
	return &Provider{
		id:      id,
		vendor:  cfg.APIFormat,
		baseURL: strings.TrimRight(cfg.BaseURL, "/"),
		apiKey:  cfg.APIKey,
		models:  cfg.Models,
	}, nil
}

func (p *Provider) ID() string { return p.id }

// Vendor is the vendor whose API this provider serves, and the first segment of its routes.
func (p *Provider) Vendor() string { return p.vendor }

func (p *Provider) notChat() *models.APIError {
	return &models.APIError{
		Status:  http.StatusNotImplemented,
		Type:    models.ErrTypeServer,
		Code:    "not_supported",
		Message: fmt.Sprintf("provider %q serves streaming speech only, at /%s", p.id, p.vendor),
	}
}

func (p *Provider) ChatCompletion(context.Context, *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	return nil, p.notChat()
}

func (p *Provider) StreamChatCompletion(context.Context, *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	chunks := make(chan models.StreamChunk)
	close(chunks)
	errs := make(chan error, 1)
	errs <- p.notChat()
	close(errs)
	return chunks, errs
}

func (p *Provider) ListModels(context.Context) ([]models.ModelObject, error) {
	out := make([]models.ModelObject, len(p.models))
	for i, m := range p.models {
		out[i] = models.ModelObject{ID: m, Object: "model", OwnedBy: p.vendor}
	}
	return out, nil
}

func (p *Provider) Close() error { return nil }

// Session is one relayed connection: where to connect upstream, and the meter for what the
// client sends through it. ClientMessage is called from one goroutine only, and the usage
// accessors after the relay has finished.
type Session struct {
	URL    string      // upstream WebSocket URL, the client's query included
	Header http.Header // upstream credential
	Kind   Kind
	// AdmitModel, when set, decides whether the client may use a model it names in a message
	// (in the gateway's form, cartesia/sonic-3). A model in the URL is known before connecting
	// and checked there instead.
	AdmitModel func(model string) error

	vendor         string
	model          string
	voice          string
	bytesPerSecond int // transcription: raw audio bytes per second
	audioBytes     int64
	characters     int
}

// Open prepares a session for one of the vendor's paths and the client's query and headers.
func (p *Provider) Open(path string, query url.Values, header http.Header) (*Session, error) {
	kind, ok := vendors[p.vendor].endpoints[path]
	if !ok {
		return nil, models.ErrNotFound("unknown_endpoint", fmt.Sprintf("%s has no relayed endpoint %s", p.vendor, path))
	}
	q := url.Values{}
	for k, v := range query {
		q[k] = v
	}
	// A client credential never travels upstream: Cartesia accepts its key as a query parameter.
	q.Del("api_key")

	s := &Session{Kind: kind, vendor: p.vendor}
	switch {
	case kind == Transcription:
		bps, err := audioBytesPerSecond(q)
		if err != nil {
			return nil, err
		}
		s.bytesPerSecond = bps
		s.model = q.Get("model")
	case p.vendor == "deepgram":
		s.model, s.voice = splitAura(q.Get("model"))
	}

	s.Header = http.Header{}
	for _, name := range vendors[p.vendor].forward {
		if value := header.Get(name); value != "" {
			s.Header.Set(name, value)
		}
	}
	vendors[p.vendor].auth(s.Header, p.apiKey)
	s.URL = websocketURL(p.baseURL) + path
	if len(q) > 0 {
		s.URL += "?" + q.Encode()
	}
	return s, nil
}

// ClientMessage meters one message the client sent, or refuses it, unmetered, when it names a
// model AdmitModel rejects; a refused message must not be relayed.
func (s *Session) ClientMessage(binary bool, data []byte) error {
	switch {
	case s.Kind == Transcription:
		if binary {
			s.audioBytes += int64(len(data))
		}
	case binary:
	case s.vendor == "deepgram":
		var m struct {
			Type string `json:"type"`
			Text string `json:"text"`
		}
		if json.Unmarshal(data, &m) == nil && m.Type == "Speak" {
			s.characters += utf8.RuneCountInString(m.Text)
		}
	default: // cartesia synthesis: the model and voice travel with each request, not in the URL
		// Fields are read one by one so a field of an unexpected shape cannot hide the model.
		var fields map[string]json.RawMessage
		if json.Unmarshal(data, &fields) != nil {
			return nil
		}
		var model, transcript string
		var voice struct {
			ID string `json:"id"`
		}
		json.Unmarshal(fields["model_id"], &model)
		json.Unmarshal(fields["transcript"], &transcript)
		json.Unmarshal(fields["voice"], &voice)
		if model != "" && model != s.model {
			if s.AdmitModel != nil {
				if err := s.AdmitModel(s.vendor + "/" + model); err != nil {
					return err
				}
			}
			s.model = model
		}
		s.characters += utf8.RuneCountInString(transcript)
		if voice.ID != "" {
			s.voice = voice.ID
		}
	}
	return nil
}

// Model is the session's model as the gateway names it (deepgram/nova-3, cartesia/sonic-3),
// or "" while it is not yet known: Cartesia synthesis names it in its first message.
func (s *Session) Model() string {
	if s.model == "" {
		return ""
	}
	return s.vendor + "/" + s.model
}

// Voice is the synthesis voice, when the session named one.
func (s *Session) Voice() string { return s.voice }

// AudioSeconds is how much audio the client sent for transcription.
func (s *Session) AudioSeconds() float64 {
	if s.bytesPerSecond == 0 {
		return 0
	}
	return float64(s.audioBytes) / float64(s.bytesPerSecond)
}

// Characters is how much text the client sent for synthesis.
func (s *Session) Characters() int { return s.characters }

// sampleWidth is the bytes per sample of the raw encodings a transcription can be metered by.
var sampleWidth = map[string]int{
	"linear16": 2, "pcm_s16le": 2,
	"linear32": 4, "pcm_s32le": 4, "pcm_f32le": 4,
	"mulaw": 1, "alaw": 1, "pcm_mulaw": 1, "pcm_alaw": 1,
}

func audioBytesPerSecond(q url.Values) (int, error) {
	width := sampleWidth[q.Get("encoding")]
	rate, _ := strconv.Atoi(q.Get("sample_rate"))
	channels := 1
	if c := q.Get("channels"); c != "" {
		channels, _ = strconv.Atoi(c)
	}
	if width == 0 || rate <= 0 || channels <= 0 {
		return 0, models.ErrBadRequest("unmeterable_audio",
			"transcription through the gateway is metered by audio length, so it needs a raw "+
				"encoding (linear16, pcm_s16le, mulaw, ...), sample_rate and, for more than one "+
				"channel, channels")
	}
	return width * rate * channels, nil
}

// splitAura separates the model from the voice in a Deepgram Aura name (aura-2-thalia-en is
// voice thalia-en of aura-2), because usage is priced by model.
func splitAura(name string) (string, string) {
	for _, family := range []string{"aura-2", "aura"} {
		if voice, ok := strings.CutPrefix(name, family+"-"); ok {
			return family, voice
		}
	}
	return name, ""
}

func websocketURL(base string) string {
	if rest, ok := strings.CutPrefix(base, "https://"); ok {
		return "wss://" + rest
	}
	if rest, ok := strings.CutPrefix(base, "http://"); ok {
		return "ws://" + rest
	}
	return base
}
