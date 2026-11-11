package speech

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"testing"
	"unicode/utf8"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

// The speech relay's contract tests.
//
// The relay parses nothing the vendor sends: the handler's realtime relay
// forwards every upstream frame to the client unchanged and has no hook on
// them, so there is no upstream response shape to pin and nothing a response
// mutation could break. What the gateway does take from each vendor's API is
//
//   - where and how it connects: Open's upstream URL, the credential and the
//     protocol headers it forwards;
//   - what it bills: Session.ClientMessage meters the client's own frames, in
//     the vendor's message format (seconds of audio from raw-audio frames,
//     characters from Deepgram's Speak.text and Cartesia's transcript), and
//     records the model and voice;
//   - what it refuses before connecting: Open's 404 unknown_endpoint and 400
//     unmeterable_audio.
//
// TestContractSessions pins those for client sessions written from the
// vendors' own definitions (testdata/sessions/<vendor>/<case>/session.json,
// each naming its source), against golden.json and against an independent
// reading of the vendor's billing rules (saneSession), whose known failures a
// session lists under sanity_gaps as contracttest.ExpectGaps does.
// TestContractVendorAPI checks the gateway against the vendor's lists
// (testdata/vendor_api.json): the streaming endpoints, the transcription
// encodings and Deepgram's voice names.
//
// A refused upstream handshake is mapped by speechDialError in
// internal/server/handlers_speech.go, outside this package, and is not pinned
// here.
//
//	go test ./internal/providers/speech/ -run Contract -update   # rewrite golden.json

const (
	platformKey = "platform-key" // the gateway's vendor credential (the provider's api_key)
	clientKey   = "gateway-key"  // what the client's SDK sends as its key: a gateway key
)

// rawSampleWidth is the bytes per sample of the raw (uncompressed,
// headerless) encodings the vendors document, by the codec each names: the
// length of such a stream is its byte count over width x rate x channels.
var rawSampleWidth = map[string]int{
	// Deepgram ListenV1Encoding
	"linear16": 2, "linear32": 4, "mulaw": 1, "alaw": 1,
	// Cartesia STTEncoding
	"pcm_s16le": 2, "pcm_s32le": 4, "pcm_f16le": 2, "pcm_f32le": 4, "pcm_mulaw": 1, "pcm_alaw": 1,
}

// compressedEncodings are the documented encodings whose byte count does not
// give the audio's length. The gateway refuses them by design (400
// unmeterable_audio): it bills transcription by audio length.
var compressedEncodings = map[string]bool{
	"flac": true, "amr-nb": true, "amr-wb": true, "opus": true, "ogg-opus": true, "speex": true, "g729": true,
}

// vendorAPIFile is testdata/vendor_api.json: what each vendor documents, and
// the gateway's known gaps against it.
type vendorAPIFile struct {
	Vendors    map[string]vendorAPI     `json:"vendors"`
	SanityGaps []contracttest.SanityGap `json:"sanity_gaps,omitempty"`
}

type vendorAPI struct {
	Source string `json:"source"`
	// BaseURL is the API base a provider is configured with
	// (config.example.yaml); WebSocketURL is where the vendor's SDK connects.
	BaseURL      string `json:"base_url"`
	WebSocketURL string `json:"websocket_url"`
	// Endpoints are the vendor's speech streaming APIs and what each does.
	Endpoints map[string]Kind `json:"endpoints"`
	// NotSpeech are the vendor's other streaming APIs, with why they are not
	// speech APIs the relay should serve.
	NotSpeech map[string]string `json:"not_speech,omitempty"`
	// ProtocolHeaders are client headers the vendor's protocol defines.
	ProtocolHeaders        []string `json:"protocol_headers,omitempty"`
	TranscriptionEncodings []string `json:"transcription_encodings"`
	// SynthesisDefaultModel is the model the vendor synthesizes with when the
	// connection names none; SynthesisModels the models it names.
	SynthesisDefaultModel string   `json:"synthesis_default_model,omitempty"`
	SynthesisModels       []string `json:"synthesis_models,omitempty"`
}

// sessionSpec is a session.json: one client session, written from the
// vendor's definition of its API.
type sessionSpec struct {
	Source string `json:"source"`
	// Path is the vendor's API path, which the gateway serves at
	// /{vendor}{path}; Query and Headers are what the client connects with.
	Path       string                   `json:"path"`
	Query      string                   `json:"query,omitempty"`
	Headers    map[string]string        `json:"headers,omitempty"`
	Messages   []clientFrame            `json:"messages,omitempty"`
	Notes      string                   `json:"notes,omitempty"`
	SanityGaps []contracttest.SanityGap `json:"sanity_gaps,omitempty"`
}

// clientFrame is one message the client sends: exactly one of JSON (a text
// frame of that JSON, compacted), Text (a text frame of that text) and Binary
// (a binary frame of that many bytes of silence: the meter reads only the
// length), Repeat times (default once).
type clientFrame struct {
	JSON   json.RawMessage `json:"json,omitempty"`
	Text   *string         `json:"text,omitempty"`
	Binary int             `json:"binary,omitempty"`
	Repeat int             `json:"repeat,omitempty"`
}

// sessionGolden is golden.json: what the gateway took from the session.
type sessionGolden struct {
	Upstream *upstreamGolden           `json:"upstream,omitempty"`
	Usage    *usageGolden              `json:"usage,omitempty"`
	Error    *contracttest.ErrorResult `json:"error,omitempty"`
}

type upstreamGolden struct {
	URL    string      `json:"url"`
	Header http.Header `json:"header"`
	Kind   Kind        `json:"kind"`
}

type usageGolden struct {
	Model        string  `json:"model"`
	Voice        string  `json:"voice"`
	AudioSeconds float64 `json:"audio_seconds"`
	Characters   int     `json:"characters"`
	// AdmittedModels are the models ClientMessage asked AdmitModel about, in
	// order.
	AdmittedModels []string `json:"admitted_models,omitempty"`
}

func TestContractSessions(t *testing.T) {
	api := readVendorAPI(t)
	dirs, err := filepath.Glob(filepath.Join("testdata", "sessions", "*", "*"))
	if err != nil || len(dirs) == 0 {
		t.Fatalf("no sessions under testdata/sessions (%v)", err)
	}
	for _, dir := range dirs {
		vendor, name := filepath.Base(filepath.Dir(dir)), filepath.Base(dir)
		t.Run(vendor+"/"+name, func(t *testing.T) {
			v, ok := api.Vendors[vendor]
			if !ok {
				t.Fatalf("testdata/sessions/%s: no such vendor in vendor_api.json", vendor)
			}
			spec := readSession(t, dir)
			run := runSession(t, vendor, v, spec)
			contracttest.AssertGolden(t, filepath.Join(dir, "golden.json"), run.golden())
			c := contracttest.Case{Name: name, Dir: dir, SanityGaps: spec.SanityGaps}
			contracttest.ExpectGaps(t, c, func(tb testing.TB) { saneSession(tb, vendor, v, spec, run) })
		})
	}
}

// sessionRun is a session played through Open and ClientMessage.
type sessionRun struct {
	session  *Session
	openErr  error
	admitted []string
	audio    int // binary bytes the client sent
}

func (r sessionRun) golden() sessionGolden {
	if r.openErr != nil {
		e := contracttest.ErrorGolden(r.openErr)
		return sessionGolden{Error: &e}
	}
	s := r.session
	return sessionGolden{
		Upstream: &upstreamGolden{URL: s.URL, Header: s.Header, Kind: s.Kind},
		Usage: &usageGolden{
			Model: s.Model(), Voice: s.Voice(), AudioSeconds: s.AudioSeconds(), Characters: s.Characters(),
			AdmittedModels: r.admitted,
		},
	}
}

func newProvider(t testing.TB, vendor string, v vendorAPI) *Provider {
	t.Helper()
	p, err := New(vendor, config.ProviderConfig{APIFormat: vendor, BaseURL: v.BaseURL, APIKey: platformKey})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	return p
}

func runSession(t *testing.T, vendor string, v vendorAPI, spec sessionSpec) sessionRun {
	t.Helper()
	query, err := url.ParseQuery(spec.Query)
	if err != nil {
		t.Fatalf("session.json query: %v", err)
	}
	header := http.Header{}
	for k, val := range spec.Headers {
		header.Set(k, val)
	}
	var run sessionRun
	run.session, run.openErr = newProvider(t, vendor, v).Open(spec.Path, query, header)
	if run.openErr != nil {
		if len(spec.Messages) > 0 {
			t.Fatalf("session.json has messages, but Open refused the session: %v", run.openErr)
		}
		return run
	}
	run.session.AdmitModel = func(model string) error {
		run.admitted = append(run.admitted, model)
		return nil
	}
	for i, f := range spec.Messages {
		binary, data := f.frame()
		for range max(f.Repeat, 1) {
			if binary {
				run.audio += len(data)
			}
			if err := run.session.ClientMessage(binary, data); err != nil {
				t.Errorf("message %d: ClientMessage refused it: %v", i, err)
			}
		}
	}
	return run
}

func (f clientFrame) frame() (binary bool, data []byte) {
	switch {
	case f.JSON != nil:
		var buf bytes.Buffer
		if err := json.Compact(&buf, f.JSON); err != nil {
			panic(err) // readSession checked it
		}
		return false, buf.Bytes()
	case f.Text != nil:
		return false, []byte(*f.Text)
	default:
		return true, make([]byte, f.Binary)
	}
}

func readSession(t *testing.T, dir string) sessionSpec {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(dir, "session.json"))
	if err != nil {
		t.Fatal(err)
	}
	var spec sessionSpec
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&spec); err != nil {
		t.Fatalf("session.json: %v", err)
	}
	if strings.TrimSpace(spec.Source) == "" || !strings.HasPrefix(spec.Path, "/") {
		t.Fatalf("session.json needs a source and a path")
	}
	for i, f := range spec.Messages {
		set := 0
		if f.JSON != nil {
			set++
			if !json.Valid(f.JSON) {
				t.Fatalf("session.json message %d: json is not JSON", i)
			}
		}
		if f.Text != nil {
			set++
		}
		if f.Binary > 0 {
			set++
		}
		if set != 1 || f.Repeat < 0 {
			t.Fatalf("session.json message %d: want exactly one of json, text and binary", i)
		}
	}
	for _, g := range spec.SanityGaps {
		if strings.TrimSpace(g.Failure) == "" || !strings.HasPrefix(g.Reason, "known gap ") {
			t.Fatalf("session.json sanity_gaps entry %q needs a failure and a reason starting \"known gap <ID>:\"", g.Failure)
		}
	}
	return spec
}

func readVendorAPI(t testing.TB) vendorAPIFile {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("testdata", "vendor_api.json"))
	if err != nil {
		t.Fatal(err)
	}
	var api vendorAPIFile
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&api); err != nil {
		t.Fatalf("vendor_api.json: %v", err)
	}
	return api
}

// saneSession checks a session against the vendor's API and billing rules,
// read independently of the meter:
//
//   - a refusal is a *models.APIError with a 4xx status and a message, and
//     refuses neither an endpoint the vendor documents nor a raw encoding;
//   - the session connects to the vendor's WebSocket URL with the client's
//     query (less a credential), on the platform's credential, never the
//     client's, and forwards the vendor's protocol headers;
//   - it meters the audio the client sent, in seconds, from the encoding's
//     sample width, rate and channels (Deepgram's channels defaults to 1);
//   - it meters the characters the vendor synthesizes: Deepgram's Speak.text,
//     Cartesia's transcript;
//   - it records the model the vendor runs and the voice the client named.
func saneSession(t testing.TB, vendor string, v vendorAPI, spec sessionSpec, run sessionRun) {
	t.Helper()
	query, _ := url.ParseQuery(spec.Query)
	if run.openErr != nil {
		saneRefusal(t, vendor, v, spec.Path, query, run.openErr)
		return
	}
	s := run.session

	u, err := url.Parse(s.URL)
	if err != nil {
		t.Errorf("upstream URL %q: %v", s.URL, err)
		return
	}
	if got, want := u.Scheme+"://"+u.Host+u.Path, v.WebSocketURL+spec.Path; got != want {
		t.Errorf("upstream URL %s, want %s", got, want)
	}
	forwarded := url.Values{}
	for k, vals := range query {
		forwarded[k] = vals
	}
	forwarded.Del("api_key")
	if got, want := u.Query().Encode(), forwarded.Encode(); got != want {
		t.Errorf("upstream query %q, want the client's %q less a credential", got, want)
	}
	if strings.Contains(s.URL, clientKey) {
		t.Errorf("the client's key travels upstream in the URL")
	}
	sawPlatformKey := false
	for name, vals := range s.Header {
		for _, val := range vals {
			if strings.Contains(val, clientKey) {
				t.Errorf("the client's key travels upstream in %s", name)
			}
			sawPlatformKey = sawPlatformKey || strings.Contains(val, platformKey)
		}
	}
	if !sawPlatformKey {
		t.Errorf("the upstream header carries no platform credential: %v", s.Header)
	}
	for sent, want := range spec.Headers {
		for _, name := range v.ProtocolHeaders {
			if strings.EqualFold(sent, name) && s.Header.Get(name) != want {
				t.Errorf("protocol header %s = %q upstream, want the client's %q", name, s.Header.Get(name), want)
			}
		}
	}
	if want := v.Endpoints[spec.Path]; s.Kind != want {
		t.Errorf("kind %q, want %q", s.Kind, want)
	}

	switch s.Kind {
	case Transcription:
		if model := query.Get("model"); model != "" && s.Model() != vendor+"/"+model {
			t.Errorf("model %q, want %s/%s from the query", s.Model(), vendor, model)
		}
		rate, _ := strconv.Atoi(query.Get("sample_rate"))
		channels := 1
		if c := query.Get("channels"); c != "" {
			channels, _ = strconv.Atoi(c)
		}
		want := float64(run.audio) / float64(rawSampleWidth[query.Get("encoding")]*rate*channels)
		if math.Abs(s.AudioSeconds()-want) > 1e-9 {
			t.Errorf("audio_seconds %v, want %v (%d bytes of %s at %d Hz, %d channels)",
				s.AudioSeconds(), want, run.audio, query.Get("encoding"), rate, channels)
		}
		if s.Characters() != 0 {
			t.Errorf("a transcription metered %d characters", s.Characters())
		}
	case Synthesis:
		model, voice, characters := synthesized(vendor, v, query, spec.Messages)
		if characters != s.Characters() {
			t.Errorf("characters %d, want %d (the text the vendor synthesizes)", s.Characters(), characters)
		}
		if s.AudioSeconds() != 0 {
			t.Errorf("a synthesis metered %v seconds of audio", s.AudioSeconds())
		}
		saneSynthesisModel(t, vendor, model, voice, s)
	}
}

func saneRefusal(t testing.TB, vendor string, v vendorAPI, path string, query url.Values, err error) {
	t.Helper()
	var apiErr *models.APIError
	if !errors.As(err, &apiErr) {
		t.Errorf("Open returned %T, not a *models.APIError: the gateway answers it with a 500", err)
		return
	}
	if apiErr.Status < 400 || apiErr.Status >= 500 {
		t.Errorf("Open refused with status %d; a refusal before connecting is the client's 4xx", apiErr.Status)
	}
	if strings.TrimSpace(apiErr.Message) == "" {
		t.Errorf("Open refused with no message")
	}
	if kind, documented := v.Endpoints[path]; documented && apiErr.Code == "unknown_endpoint" {
		t.Errorf("%s documents %s (%s), which the gateway does not relay", vendor, path, kind)
	}
	enc := query.Get("encoding")
	if _, raw := rawSampleWidth[enc]; raw && apiErr.Code == "unmeterable_audio" && query.Get("sample_rate") != "" {
		t.Errorf("raw encoding %q, which %s documents, is refused as unmeterable", enc, vendor)
	}
}

// synthesized reads a synthesis session as the vendor does: the model it
// runs, the voice the client named and the characters it synthesizes.
func synthesized(vendor string, v vendorAPI, query url.Values, frames []clientFrame) (model, voice string, characters int) {
	if vendor == "deepgram" {
		model = query.Get("model")
		if model == "" {
			model = v.SynthesisDefaultModel
		}
	}
	for _, f := range frames {
		if f.JSON == nil {
			continue
		}
		var m map[string]json.RawMessage
		if json.Unmarshal(f.JSON, &m) != nil {
			continue
		}
		switch vendor {
		case "deepgram":
			var typ, text string
			_ = json.Unmarshal(m["type"], &typ)
			_ = json.Unmarshal(m["text"], &text)
			if typ == "Speak" {
				characters += utf8.RuneCountInString(text)
			}
		case "cartesia":
			var modelID, transcript string
			_ = json.Unmarshal(m["model_id"], &modelID)
			_ = json.Unmarshal(m["transcript"], &transcript)
			if modelID != "" {
				model = modelID
			}
			characters += utf8.RuneCountInString(transcript)
			// The voice is an ID, or an object with one.
			var id string
			var obj struct {
				ID string `json:"id"`
			}
			if json.Unmarshal(m["voice"], &id) == nil && id != "" {
				voice = id
			} else if json.Unmarshal(m["voice"], &obj) == nil && obj.ID != "" {
				voice = obj.ID
			}
		}
	}
	return model, voice, characters
}

func saneSynthesisModel(t testing.TB, vendor, model, voice string, s *Session) {
	t.Helper()
	switch vendor {
	case "deepgram":
		// The Aura name is both: aura-2-thalia-en is voice thalia-en of
		// aura-2, priced by the model.
		got := strings.TrimPrefix(s.Model(), vendor+"/") + "-" + s.Voice()
		if s.Model() == "" || s.Voice() == "" || got != model {
			t.Errorf("deepgram synthesizes with %s, but the session records model %q and voice %q", model, s.Model(), s.Voice())
		}
	default:
		if model != "" && s.Model() != vendor+"/"+model {
			t.Errorf("%s synthesizes with %s, but the session records model %q", vendor, model, s.Model())
		}
		if voice != "" && s.Voice() != voice {
			t.Errorf("voice %q the client named is not recorded (got %q)", voice, s.Voice())
		}
	}
}

// TestContractVendorAPI checks the gateway against each vendor's lists: it
// relays every speech endpoint the vendor documents and none it does not, it
// meters every raw transcription encoding and refuses the compressed ones, and
// it splits every Deepgram voice name into a model it can price and a voice.
// A list member the test does not know fails it, so a refreshed
// vendor_api.json shows what the vendor added.
func TestContractVendorAPI(t *testing.T) {
	api := readVendorAPI(t)
	relayed := map[string]map[string]bool{}
	for _, e := range Endpoints() {
		if relayed[e.Vendor] == nil {
			relayed[e.Vendor] = map[string]bool{}
		}
		relayed[e.Vendor][e.Path] = true
	}
	for vendor := range relayed {
		if _, ok := api.Vendors[vendor]; !ok {
			t.Errorf("the gateway relays %s, which vendor_api.json does not describe", vendor)
		}
	}

	c := contracttest.Case{Name: "vendor_api", SanityGaps: api.SanityGaps}
	contracttest.ExpectGaps(t, c, func(tb testing.TB) {
		for _, vendor := range sortedKeys(api.Vendors) {
			v := api.Vendors[vendor]
			for _, path := range sortedKeys(v.Endpoints) {
				if !relayed[vendor][path] {
					tb.Errorf("%s documents %s (%s), which the gateway does not relay", vendor, path, v.Endpoints[path])
				}
			}
			for _, path := range sortedKeys(relayed[vendor]) {
				if _, ok := v.Endpoints[path]; !ok {
					tb.Errorf("the gateway relays /%s%s, which %s does not document", vendor, path, vendor)
				}
			}
			checkEncodings(tb, vendor, v)
			checkVoiceNames(tb, vendor, v)
		}
	})
}

func checkEncodings(t testing.TB, vendor string, v vendorAPI) {
	t.Helper()
	var path string
	for _, p := range sortedKeys(v.Endpoints) {
		if v.Endpoints[p] == Transcription {
			path = p
			break
		}
	}
	p := newProvider(t, vendor, v)
	for _, enc := range v.TranscriptionEncodings {
		width, raw := rawSampleWidth[enc]
		if !raw && !compressedEncodings[enc] {
			t.Errorf("%s documents transcription encoding %q, which this test does not know: add it to rawSampleWidth or compressedEncodings", vendor, enc)
			continue
		}
		const rate = 16000
		s, err := p.Open(path, url.Values{"encoding": {enc}, "sample_rate": {strconv.Itoa(rate)}}, http.Header{})
		var apiErr *models.APIError
		refused := errors.As(err, &apiErr) && apiErr.Code == "unmeterable_audio"
		if !raw {
			if !refused {
				t.Errorf("compressed encoding %q: Open = %v, want the 400 unmeterable_audio refusal", enc, err)
			}
			continue
		}
		if err != nil {
			t.Errorf("raw encoding %q, which %s documents, is refused: %v", enc, vendor, err)
			continue
		}
		_ = s.ClientMessage(true, make([]byte, width*rate))
		if s.AudioSeconds() != 1 {
			t.Errorf("one second of %s audio is metered as %v seconds", enc, s.AudioSeconds())
		}
	}
}

func checkVoiceNames(t testing.TB, vendor string, v vendorAPI) {
	t.Helper()
	if len(v.SynthesisModels) == 0 {
		return
	}
	p := newProvider(t, vendor, v)
	for _, name := range append([]string{v.SynthesisDefaultModel}, v.SynthesisModels...) {
		s, err := p.Open("/v1/speak", url.Values{"model": {name}}, http.Header{})
		if err != nil {
			t.Errorf("model %s: Open: %v", name, err)
			continue
		}
		model := strings.TrimPrefix(s.Model(), vendor+"/")
		if model == "" || s.Voice() == "" || model+"-"+s.Voice() != name {
			t.Errorf("%s model %s splits into model %q and voice %q, not a model to price and its voice", vendor, name, model, s.Voice())
		}
	}
}

// TestContractCartesiaModelAdmission pins the check on a model a Cartesia
// synthesis names mid-session: ClientMessage asks AdmitModel about each model
// change in the gateway's form (cartesia/sonic-3), and a refused message is
// neither metered nor allowed to change the session's model.
func TestContractCartesiaModelAdmission(t *testing.T) {
	api := readVendorAPI(t)
	s, err := newProvider(t, "cartesia", api.Vendors["cartesia"]).Open("/tts/websocket", url.Values{}, http.Header{})
	if err != nil {
		t.Fatal(err)
	}
	var asked []string
	refusal := models.ErrForbidden("API key does not have access to model: cartesia/sonic-latest")
	s.AdmitModel = func(model string) error {
		asked = append(asked, model)
		if model == "cartesia/sonic-latest" {
			return refusal
		}
		return nil
	}
	request := func(model, transcript string) []byte {
		return []byte(fmt.Sprintf(`{"context_id":"c1","model_id":%q,"transcript":%q,"voice":{"mode":"id","id":"v1"},`+
			`"output_format":{"container":"raw","encoding":"pcm_s16le","sample_rate":44100},"continue":true}`, model, transcript))
	}
	for _, step := range []struct {
		model, transcript string
		refused           bool
	}{
		{"sonic-3", "Hello ", false},
		{"sonic-3", "there.", false},
		{"sonic-latest", "Refused text.", true},
		{"sonic-3", "Again.", false},
	} {
		err := s.ClientMessage(false, request(step.model, step.transcript))
		if (err != nil) != step.refused || (step.refused && !errors.Is(err, refusal)) {
			t.Errorf("model %s: ClientMessage = %v, refused want %v", step.model, err, step.refused)
		}
	}
	if want := []string{"cartesia/sonic-3", "cartesia/sonic-latest"}; strings.Join(asked, ",") != strings.Join(want, ",") {
		t.Errorf("AdmitModel asked about %v, want %v (each model change, once)", asked, want)
	}
	if s.Model() != "cartesia/sonic-3" || s.Characters() != len("Hello there.Again.") {
		t.Errorf("after a refused message: model %q, characters %d; want cartesia/sonic-3 and %d", s.Model(), s.Characters(), len("Hello there.Again."))
	}
}

// TestContractClientKeyStaysDownstream pins that a key the client sends in the
// query, where the gateway accepts a Cartesia client's key (speechClientKey in
// internal/server), does not travel upstream; the rest of the query does.
func TestContractClientKeyStaysDownstream(t *testing.T) {
	api := readVendorAPI(t)
	q := url.Values{"api_key": {clientKey}, "encoding": {"pcm_s16le"}, "sample_rate": {"16000"}, "model": {"ink-2"}}
	s, err := newProvider(t, "cartesia", api.Vendors["cartesia"]).Open("/stt/websocket", q, http.Header{})
	if err != nil {
		t.Fatal(err)
	}
	if want := "wss://api.cartesia.ai/stt/websocket?encoding=pcm_s16le&model=ink-2&sample_rate=16000"; s.URL != want {
		t.Errorf("upstream URL %s, want %s", s.URL, want)
	}
	if q.Get("api_key") != clientKey {
		t.Errorf("Open changed the client's query")
	}
}

func sortedKeys[V any](m map[string]V) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}
