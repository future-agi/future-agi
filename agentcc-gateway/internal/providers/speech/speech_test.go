package speech

import (
	"errors"
	"net/http"
	"net/url"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func newVendor(t *testing.T, vendor string) *Provider {
	t.Helper()
	p, err := New(vendor, config.ProviderConfig{BaseURL: "https://" + vendor + ".example/", APIKey: "platform-" + vendor, APIFormat: vendor})
	if err != nil {
		t.Fatalf("New(%s): %v", vendor, err)
	}
	return p
}

func open(t *testing.T, p *Provider, path, rawQuery string, header http.Header) *Session {
	t.Helper()
	q, err := url.ParseQuery(rawQuery)
	if err != nil {
		t.Fatal(err)
	}
	s, err := p.Open(path, q, header)
	if err != nil {
		t.Fatalf("Open(%s?%s): %v", path, rawQuery, err)
	}
	return s
}

// Transcription is metered in seconds of the raw audio the client sends: bytes
// divided by sample width x sample rate x channels. Text frames are not audio.
func TestTranscriptionIsMeteredInSecondsOfAudioSent(t *testing.T) {
	s := open(t, newVendor(t, "deepgram"), "/v1/listen", "model=nova-3&encoding=linear16&sample_rate=16000&channels=2", nil)
	if s.Kind != Transcription || s.Model() != "deepgram/nova-3" {
		t.Fatalf("kind %q model %q, want transcription deepgram/nova-3", s.Kind, s.Model())
	}
	s.ClientMessage(true, make([]byte, 64000)) // 2 bytes x 16000 Hz x 2 channels = 1 s
	s.ClientMessage(true, make([]byte, 32000))
	s.ClientMessage(false, []byte(`{"type":"KeepAlive"}`))
	if got := s.AudioSeconds(); got != 1.5 {
		t.Errorf("AudioSeconds = %v, want 1.5", got)
	}
}

// Audio the gateway cannot measure (no raw encoding or no sample rate) is
// refused before any connection is made.
func TestTranscriptionWithoutAMeterableEncodingIsRefused(t *testing.T) {
	p := newVendor(t, "deepgram")
	for _, q := range []string{"", "encoding=opus&sample_rate=48000", "encoding=linear16"} {
		query, _ := url.ParseQuery(q)
		_, err := p.Open("/v1/listen", query, nil)
		var apiErr *models.APIError
		if !errors.As(err, &apiErr) || apiErr.Status != http.StatusBadRequest || apiErr.Code != "unmeterable_audio" {
			t.Errorf("Open(/v1/listen?%s) = %v, want 400 unmeterable_audio", q, err)
		}
	}
}

// Synthesis is metered in characters of the text the client sends: Deepgram's
// Speak messages, Cartesia's transcripts. A Deepgram Aura name carries the
// voice, which is not part of the priced model.
func TestSynthesisIsMeteredInCharactersOfTextSent(t *testing.T) {
	dg := open(t, newVendor(t, "deepgram"), "/v1/speak", "model=aura-2-thalia-en", nil)
	dg.ClientMessage(false, []byte(`{"type":"Speak","text":"héllo"}`))
	dg.ClientMessage(false, []byte(`{"type":"Flush"}`))
	dg.ClientMessage(false, []byte(`{"type":"Speak","text":"world"}`))
	if dg.Characters() != 10 || dg.Model() != "deepgram/aura-2" || dg.Voice() != "thalia-en" {
		t.Errorf("deepgram: characters %d model %q voice %q, want 10 deepgram/aura-2 thalia-en", dg.Characters(), dg.Model(), dg.Voice())
	}

	ca := open(t, newVendor(t, "cartesia"), "/tts/websocket", "", nil)
	if !ca.ModelPerMessage() || ca.Model() != "" {
		t.Fatalf("cartesia synthesis: ModelPerMessage %v model %q, want true and no model before a request", ca.ModelPerMessage(), ca.Model())
	}
	ca.ClientMessage(false, []byte(`{"model_id":"sonic-2","transcript":"hello","voice":{"mode":"id","id":"v1"}}`))
	ca.ClientMessage(false, []byte(`{"model_id":"sonic-2","transcript":" there","continue":true}`))
	if ca.Characters() != 11 || ca.Model() != "cartesia/sonic-2" || ca.Voice() != "v1" {
		t.Errorf("cartesia: characters %d model %q voice %q, want 11 cartesia/sonic-2 v1", ca.Characters(), ca.Model(), ca.Voice())
	}
}

// Each Cartesia synthesis request with text is checked: with its model when it names one other
// than the last one admitted, and with "" whenever it names none, also after an earlier request
// named one. A message without text (a flush, a cancel) synthesizes nothing and is not checked.
// A refused request is not metered.
func TestCartesiaSynthesisChecksEveryRequestThatNamesNoModel(t *testing.T) {
	ca := open(t, newVendor(t, "cartesia"), "/tts/websocket", "", nil)
	var asked []string
	ca.AdmitModel = func(model string) error {
		asked = append(asked, model)
		return nil
	}
	for _, m := range []string{
		`{"model_id":"sonic-2","transcript":"hello","context_id":"a"}`,
		`{"model_id":"sonic-2","transcript":" there","context_id":"a","continue":true}`,
		`{"transcript":"world","context_id":"b"}`,
		`{"transcript":"","context_id":"b","continue":false}`,
		`{"context_id":"b","cancel":true}`,
		`{"transcript":"again","context_id":"c"}`,
	} {
		if err := ca.ClientMessage(false, []byte(m)); err != nil {
			t.Fatalf("ClientMessage(%s): %v", m, err)
		}
	}
	if got := strings.Join(asked, "|"); got != "cartesia/sonic-2||" {
		t.Errorf("AdmitModel asked for %q, want cartesia/sonic-2, then \"\" for each of the two requests naming no model", asked)
	}

	refused := errors.New("refused")
	ca = open(t, newVendor(t, "cartesia"), "/tts/websocket", "", nil)
	ca.AdmitModel = func(model string) error {
		if model == "" {
			return refused
		}
		return nil
	}
	if err := ca.ClientMessage(false, []byte(`{"model_id":"sonic-2","transcript":"hello"}`)); err != nil {
		t.Fatalf("request naming sonic-2: %v", err)
	}
	if err := ca.ClientMessage(false, []byte(`{"transcript":"world"}`)); !errors.Is(err, refused) {
		t.Errorf("request naming no model after one naming sonic-2: %v, want the AdmitModel refusal", err)
	}
	if ca.Characters() != 5 {
		t.Errorf("characters %d, want 5: the refused request is not metered", ca.Characters())
	}
}

// The upstream connection carries the platform's credential in the vendor's
// own header and never the client's: a key the client put in the query is
// removed, and only the vendor's protocol headers are forwarded.
func TestTheUpstreamCarriesThePlatformCredentialOnly(t *testing.T) {
	ca := open(t, newVendor(t, "cartesia"), "/stt/websocket",
		"model=ink-whisper&encoding=pcm_s16le&sample_rate=16000&api_key=client-key-0001",
		http.Header{"Cartesia-Version": {"2025-04-16"}, "Authorization": {"Bearer client-key-0001"}, "X-Api-Key": {"client-key-0001"}})
	if strings.Contains(ca.URL, "client-key-0001") || !strings.HasPrefix(ca.URL, "wss://cartesia.example/stt/websocket?") {
		t.Errorf("upstream URL %q, want wss://cartesia.example/stt/websocket without the client's key", ca.URL)
	}
	want := http.Header{"Cartesia-Version": {"2025-04-16"}, "X-Api-Key": {"platform-cartesia"}}
	if len(ca.Header) != len(want) || ca.Header.Get("Cartesia-Version") != "2025-04-16" || ca.Header.Get("X-API-Key") != "platform-cartesia" {
		t.Errorf("upstream header %v, want %v", ca.Header, want)
	}

	dg := open(t, newVendor(t, "deepgram"), "/v1/speak", "model=aura-2-thalia-en", http.Header{"Authorization": {"Token client-key-0001"}})
	if len(dg.Header) != 1 || dg.Header.Get("Authorization") != "Token platform-deepgram" {
		t.Errorf("deepgram upstream header %v, want only Authorization: Token platform-deepgram", dg.Header)
	}
}

// A query that names a parameter the session is checked or metered by (model, encoding,
// sample_rate, channels) more than once is refused, so the upstream query never carries a value
// of them that was not read. Other parameters may repeat, as the vendors' own repeated options
// do (Deepgram's keyterm), and travel as sent.
func TestAQueryThatRepeatsAParameterTheGatewayReadsIsRefused(t *testing.T) {
	cases := []struct{ vendor, path, query string }{
		{"deepgram", "/v1/listen", "model=nova-3&model=nova-2-medical&encoding=linear16&sample_rate=16000"},
		{"deepgram", "/v1/listen", "model=nova-3&model=nova-3&encoding=linear16&sample_rate=16000"},
		{"deepgram", "/v1/listen", "model=nova-3&encoding=linear16&sample_rate=48000&sample_rate=8000"},
		{"deepgram", "/v1/listen", "model=nova-3&encoding=linear16&encoding=mulaw&sample_rate=16000"},
		{"deepgram", "/v1/listen", "model=nova-3&encoding=linear16&sample_rate=16000&channels=2&channels=1"},
		{"deepgram", "/v1/speak", "model=aura-2-thalia-en&model=aura-2-zeus-en"},
		{"cartesia", "/stt/websocket", "model=ink-whisper&encoding=pcm_s16le&sample_rate=16000&sample_rate=8000"},
	}
	for _, tc := range cases {
		query, _ := url.ParseQuery(tc.query)
		_, err := newVendor(t, tc.vendor).Open(tc.path, query, nil)
		var apiErr *models.APIError
		if !errors.As(err, &apiErr) || apiErr.Status != http.StatusBadRequest || apiErr.Code != "ambiguous_parameter" {
			t.Errorf("Open(%s %s?%s) = %v, want 400 ambiguous_parameter", tc.vendor, tc.path, tc.query, err)
		}
	}

	s := open(t, newVendor(t, "deepgram"), "/v1/listen", "model=nova-3&encoding=linear16&sample_rate=16000&keyterm=alpha&keyterm=beta", nil)
	if !strings.HasSuffix(s.URL, "?encoding=linear16&keyterm=alpha&keyterm=beta&model=nova-3&sample_rate=16000") {
		t.Errorf("upstream URL %q, want both keyterm values and one value of each read parameter", s.URL)
	}
}

// A query that names a parameter the session is checked or metered by in any other spelling
// (Model, MODEL, %4Dodel for model; Sample_Rate; ...) is refused like a repeat, so the upstream
// query never carries a value under a name that may mean the same parameter to the vendor but
// was not read. A spelled-out repeat (mod%65l is model) and an empty second value are repeats.
// Other parameters travel as sent, whatever their spelling.
func TestAQueryThatSpellsAParameterTheGatewayReadsDifferentlyIsRefused(t *testing.T) {
	cases := []struct{ vendor, path, query string }{
		{"deepgram", "/v1/listen", "model=nova-3&Model=nova-2-medical&encoding=linear16&sample_rate=16000"},
		{"deepgram", "/v1/listen", "Model=nova-2-medical&encoding=linear16&sample_rate=16000"},
		{"deepgram", "/v1/listen", "MODEL=nova-2-medical&encoding=linear16&sample_rate=16000"},
		{"deepgram", "/v1/listen", "model=nova-3&%4Dodel=nova-2-medical&encoding=linear16&sample_rate=16000"},
		{"deepgram", "/v1/listen", "model=nova-3&Model=&encoding=linear16&sample_rate=16000"},
		{"deepgram", "/v1/listen", "model=nova-3&mod%65l=nova-2-medical&encoding=linear16&sample_rate=16000"},
		{"deepgram", "/v1/listen", "model=nova-3&model=&encoding=linear16&sample_rate=16000"},
		{"deepgram", "/v1/listen", "model=nova-3&encoding=linear16&sample_rate=16000&Sample_Rate=8000"},
		{"deepgram", "/v1/listen", "model=nova-3&encoding=linear16&ENCODING=mulaw&sample_rate=16000"},
		{"deepgram", "/v1/listen", "model=nova-3&encoding=linear16&sample_rate=16000&Channels=2"},
		{"deepgram", "/v1/speak", "model=aura-2-thalia-en&Model=aura-2-zeus-en"},
		{"cartesia", "/stt/websocket", "model=ink-whisper&Model=ink-2&encoding=pcm_s16le&sample_rate=16000"},
	}
	for _, tc := range cases {
		query, err := url.ParseQuery(tc.query)
		if err != nil {
			t.Fatal(err)
		}
		_, err = newVendor(t, tc.vendor).Open(tc.path, query, nil)
		var apiErr *models.APIError
		if !errors.As(err, &apiErr) || apiErr.Status != http.StatusBadRequest || apiErr.Code != "ambiguous_parameter" {
			t.Errorf("Open(%s %s?%s) = %v, want 400 ambiguous_parameter", tc.vendor, tc.path, tc.query, err)
		}
	}

	s := open(t, newVendor(t, "deepgram"), "/v1/listen", "model=nova-3&encoding=linear16&sample_rate=16000&Punctuate=true&keyterm=alpha&Keyterm=beta", nil)
	if !strings.HasSuffix(s.URL, "?Keyterm=beta&Punctuate=true&encoding=linear16&keyterm=alpha&model=nova-3&sample_rate=16000") {
		t.Errorf("upstream URL %q, want every other parameter as sent and one value of each read parameter", s.URL)
	}
}

// Only the vendor's relayed endpoints open a session.
func TestAnEndpointTheVendorDoesNotRelayIsNotFound(t *testing.T) {
	_, err := newVendor(t, "deepgram").Open("/v1/read", url.Values{}, nil)
	var apiErr *models.APIError
	if !errors.As(err, &apiErr) || apiErr.Status != http.StatusNotFound {
		t.Errorf("Open(/v1/read) = %v, want 404", err)
	}
}
