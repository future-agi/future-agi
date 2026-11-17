package server

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"io"
	"math"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	budgetpkg "github.com/futureagi/agentcc-gateway/internal/budget"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	budgetplugin "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	loggingplugin "github.com/futureagi/agentcc-gateway/internal/plugins/logging"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
)

// Since the dev merge
// a route that overrides the model serves a
// non-chat request with the route's model. Embeddings and rerank record their
// upstream call as an attempt on the model they sent. Image generation, OCR,
// speech, translation and transcription record no attempt, so the cost plugin
// prices them on rc.ResolvedModel, else rc.Model; and these five handlers set
// it to the requested model (speech, translation, transcription) or not at
// all (image, OCR). The upstream ran the route's model while the gateway
// reported (x-agentcc-model-used), logged, priced and budgeted (the budget
// plugin reads the cost the cost plugin wrote) the requested one. Each of the
// five now records the model it sent, as the streamed speech handler already
// did.

// The two image models are injected with a flat per-image price each, so
// neither the shutdown calendar nor a price update can move them. The
// other endpoints are priced from the bundled per-unit prices, as in
// production.
const (
	r5ImageAsked  = "r5-image-asked"
	r5ImageRouted = "r5-image-routed"
	r5InternalKey = "sk-r5-internal"
	r5Org         = "org-r5"
)

func r5ModelDB() *atomic.Pointer[modeldb.ModelDB] {
	db := modeldb.New(modeldb.WithExtra(modeldb.BundledModels, map[string]*modeldb.ModelInfo{
		r5ImageAsked:  {Mode: modeldb.ModeImageGeneration, Pricing: modeldb.PricingInfo{OutputPerImage: 0.01}},
		r5ImageRouted: {Mode: modeldb.ModeImageGeneration, Pricing: modeldb.PricingInfo{OutputPerImage: 0.05}},
	}), nil)
	var p atomic.Pointer[modeldb.ModelDB]
	p.Store(db)
	return &p
}

// r5Case is one of the five endpoints. Without a route, provider "asked"
// serves asked; the route sends the request to provider as routed. askedCost
// and routedCost are the request's cost on each model (they differ, so a
// request priced on the wrong one cannot pass), and unpriced is a name the
// route could send instead (a deployment name) that has no price at all.
type r5Case struct {
	name, path              string
	asked, provider, routed string
	askedCost, routedCost   string
	unpriced                string
	body                    func(t *testing.T, model string) (contentType string, body []byte)
}

// r5SpeechInput is 100 characters: tts-1 is $0.000015 a character, tts-1-hd
// $0.00003.
var r5SpeechInput = strings.Repeat("0123456789", 10)

func r5JSON(format string) func(*testing.T, string) (string, []byte) {
	return func(t *testing.T, model string) (string, []byte) {
		return "application/json", []byte(strings.Replace(format, "@MODEL@", model, 1))
	}
}

// r5AudioForm is a multipart upload of a 2-second WAV (whisper-1 is $0.0001 a
// second, gpt-transcribe $0.000075).
func r5AudioForm(t *testing.T, model string) (string, []byte) {
	t.Helper()
	var b bytes.Buffer
	mw := multipart.NewWriter(&b)
	fw, err := mw.CreateFormFile("file", "r5.wav")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := fw.Write(r5WAV(2)); err != nil {
		t.Fatal(err)
	}
	if err := mw.WriteField("model", model); err != nil {
		t.Fatal(err)
	}
	if err := mw.Close(); err != nil {
		t.Fatal(err)
	}
	return mw.FormDataContentType(), b.Bytes()
}

// r5WAV is a PCM WAV of secs seconds (8 kHz, mono, 8-bit) whose length the
// gateway can read (wavDurationSeconds).
func r5WAV(secs int) []byte {
	const rate = 8000
	data := make([]byte, secs*rate)
	var b bytes.Buffer
	b.WriteString("RIFF")
	_ = binary.Write(&b, binary.LittleEndian, uint32(36+len(data)))
	b.WriteString("WAVEfmt ")
	for _, v := range []any{uint32(16), uint16(1), uint16(1), uint32(rate), uint32(rate), uint16(1), uint16(8)} {
		_ = binary.Write(&b, binary.LittleEndian, v)
	}
	b.WriteString("data")
	_ = binary.Write(&b, binary.LittleEndian, uint32(len(data)))
	b.Write(data)
	return b.Bytes()
}

var r5Cases = []r5Case{
	{
		name: "image", path: "/v1/images/generations",
		asked: r5ImageAsked, provider: "routed", routed: r5ImageRouted,
		askedCost: "0.010000", routedCost: "0.050000", unpriced: "r5-image-deployment",
		body: r5JSON(`{"model":"@MODEL@","prompt":"a lighthouse at dusk"}`),
	},
	{
		name: "ocr", path: "/v1/ocr",
		// mistral/mistral-ocr-4-0 is $0.004 a page and $0.005 an annotated
		// page, mistral/mistral-ocr-2512 $0.002 and $0.003; the upstream
		// processed 2 pages, both annotated (ocrUpstreamBody).
		asked: "mistral-ocr-4-0", provider: "mistral", routed: "mistral-ocr-2512",
		askedCost: "0.018000", routedCost: "0.010000", unpriced: "r5-ocr-deployment",
		body: r5JSON(`{"model":"@MODEL@","document":{"type":"document_url","document_url":"https://docs.example.com/r5.pdf"}}`),
	},
	{
		name: "speech", path: "/v1/audio/speech",
		asked: "tts-1", provider: "routed", routed: "tts-1-hd",
		askedCost: "0.001500", routedCost: "0.003000", unpriced: "r5-tts-deployment",
		body: r5JSON(`{"model":"@MODEL@","input":"` + r5SpeechInput + `","voice":"alloy"}`),
	},
	{
		name: "translation", path: "/v1/audio/translations",
		asked: "whisper-1", provider: "routed", routed: "gpt-transcribe",
		askedCost: "0.000200", routedCost: "0.000150", unpriced: "r5-stt-deployment",
		body: r5AudioForm,
	},
	{
		name: "transcription", path: "/v1/audio/transcriptions",
		asked: "whisper-1", provider: "routed", routed: "gpt-transcribe",
		askedCost: "0.000200", routedCost: "0.000150", unpriced: "r5-stt-deployment",
		body: r5AudioForm,
	},
}

// r5Upstream is one fake upstream for the five endpoints. It answers each the
// way OpenAI (Mistral, for OCR) does and keeps, for every request, its path
// and the model it named.
type r5Upstream struct {
	*httptest.Server
	mu   sync.Mutex
	seen []string // "<path> <model>"
}

func startR5Upstream(t *testing.T) *r5Upstream {
	t.Helper()
	u := &r5Upstream{}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost { // the gateway's own model discovery (GET /v1/models)
			w.Header().Set("Content-Type", "application/json")
			io.WriteString(w, `{"object":"list","data":[]}`)
			return
		}
		var model string
		if strings.HasPrefix(r.Header.Get("Content-Type"), "multipart/") {
			if err := r.ParseMultipartForm(32 << 20); err == nil {
				model = r.FormValue("model")
			}
		} else {
			var body struct {
				Model string `json:"model"`
			}
			_ = json.NewDecoder(r.Body).Decode(&body)
			model = body.Model
		}
		u.mu.Lock()
		u.seen = append(u.seen, r.URL.Path+" "+model)
		u.mu.Unlock()
		switch r.URL.Path {
		case "/v1/images/generations":
			w.Header().Set("Content-Type", "application/json")
			io.WriteString(w, `{"created":1760000000,"data":[{"url":"https://images.example.com/r5.png"}]}`)
		case "/v1/ocr":
			w.Header().Set("Content-Type", "application/json")
			io.WriteString(w, ocrUpstreamBody)
		case "/v1/audio/speech":
			w.Header().Set("Content-Type", "audio/mpeg")
			io.WriteString(w, "r5-not-really-audio")
		case "/v1/audio/translations", "/v1/audio/transcriptions":
			w.Header().Set("Content-Type", "application/json")
			io.WriteString(w, `{"text":"hello"}`)
		default:
			http.NotFound(w, r)
		}
	}))
	t.Cleanup(u.Close)
	return u
}

func (u *r5Upstream) received() []string {
	u.mu.Lock()
	defer u.mu.Unlock()
	return append([]string(nil), u.seen...)
}

// r5Gateway is a gateway with providers "asked", "routed" and "mistral", each
// on an upstream of its own, the cost plugin, the budget plugin (an org
// budget, which counts the cost the cost plugin wrote), a metadata probe
// after them, and request logging to a webhook sink. With keys it is built as
// a shipped gateway is, with a key store, and its requests carry an internal
// key: on a shipped gateway only an internal key (or a license-authorized
// request) reads conditional routes, since every other key is held to its
// org's own providers. Without keys it has no key store, so its requests both
// read routes and reach the request log (which leaves out internal keys).
type r5Gateway struct {
	srv       *Server
	upstreams map[string]*r5Upstream
	probe     *metadataProbe
	logs      *capcoreLogSink
	flusher   *loggingplugin.LogFlusher
	budget    *budgetpkg.Tracker
	key       string
}

func newR5Gateway(t *testing.T, withKeys bool) *r5Gateway {
	t.Helper()
	cfg := config.DefaultConfig()
	g := &r5Gateway{upstreams: map[string]*r5Upstream{}, probe: &metadataProbe{}, logs: startCapcoreLogSink(t)}
	served := map[string][]string{
		"asked":   {r5ImageAsked, "mistral-ocr-4-0", "tts-1", "whisper-1"},
		"routed":  {r5ImageRouted, "tts-1-hd", "gpt-transcribe"},
		"mistral": {"mistral-ocr-2512"},
	}
	for id, ms := range served {
		u := startR5Upstream(t)
		g.upstreams[id] = u
		cfg.Providers[id] = config.ProviderConfig{BaseURL: u.URL, APIKey: "operator-" + id, APIFormat: "openai", Models: ms}
	}
	var keyStore *auth.KeyStore
	var plugins []pipeline.Plugin
	if withKeys {
		cfg.Auth.Enabled = true
		cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "internal", Key: r5InternalKey, Owner: "t", KeyType: "internal", Metadata: map[string]string{"org_id": r5Org}}}
		keyStore = auth.NewKeyStore(cfg.Auth)
		plugins = append(plugins, authplugin.New(keyStore, true))
		g.key = r5InternalKey
	}
	mdb := r5ModelDB()
	g.flusher = loggingplugin.NewLogFlusher(g.logs.URL, "", time.Hour, 100)
	logPlugin := loggingplugin.New(config.RequestLoggingConfig{Enabled: true}, nil)
	logPlugin.SetFlusher(g.flusher)
	g.budget = budgetpkg.NewTracker(config.BudgetsConfig{Enabled: true, Org: &config.BudgetLevelConfig{Limit: 1000}})
	plugins = append(plugins, costplugin.New(true, mdb.Load, nil, nil), budgetplugin.New(g.budget, nil, true, nil), g.probe, logPlugin)
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("registry: %v", err)
	}
	g.srv = New(cfg, "", registry, pipeline.NewEngine(plugins...), keyStore, nil, nil, nil, mdb, nil, nil)
	releaseTestServer(t, g.srv, logPlugin.Close)
	g.srv.ready.Store(true)
	return g
}

// route sends every request for c.asked to c.provider as model.
func (g *r5Gateway) route(t *testing.T, c r5Case, model string) {
	t.Helper()
	cr, err := routing.NewConditionalRouter([]config.ConditionalRouteConfig{{
		Name: "r5-" + c.name, Priority: 10,
		Condition: config.ConditionConfig{Field: "model", Op: "$eq", Value: c.asked},
		Action:    config.RouteActionConfig{Provider: c.provider, ModelOverride: model},
	}})
	if err != nil {
		t.Fatal(err)
	}
	g.srv.handlers.conditionalRouter.Store(cr)
}

// send posts c's request for c.asked.
func (g *r5Gateway) send(t *testing.T, c r5Case) *httptest.ResponseRecorder {
	t.Helper()
	ct, body := c.body(t, c.asked)
	req := httptest.NewRequest(http.MethodPost, c.path, bytes.NewReader(body))
	req.Header.Set("Content-Type", ct)
	if g.key != "" {
		req.Header.Set("Authorization", "Bearer "+g.key)
	}
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("%s: status = %d %s, want 200", c.path, w.Code, w.Body.String())
	}
	return w
}

// checkSentUpstream checks that the route's provider got the request, once,
// with model, and that provider "asked" got nothing.
func (g *r5Gateway) checkSentUpstream(t *testing.T, c r5Case, model string) {
	t.Helper()
	if got := g.upstreams[c.provider].received(); len(got) != 1 || got[0] != c.path+" "+model {
		t.Errorf("%s received %q, want one %s request for %s", c.provider, got, c.path, model)
	}
	if got := g.upstreams["asked"].received(); len(got) != 0 {
		t.Errorf("asked received %q; the route sends the request to %s", got, c.provider)
	}
}

// checkBudget checks that the org budget counted cost, once, under the
// requested model: the budget plugin keys a request's spend on the model the
// client asked for (rc.Model), which no route changes.
func (g *r5Gateway) checkBudget(t *testing.T, c r5Case, cost string) {
	t.Helper()
	want, err := strconv.ParseFloat(cost, 64)
	if err != nil {
		t.Fatal(err)
	}
	total, perModel := g.budget.GetSpend("org", "")
	if math.Abs(total-want) > 1e-12 || math.Abs(perModel[c.asked]-want) > 1e-12 {
		t.Errorf("org budget spend = %v, per model %v; want %v, counted under the requested %s", total, perModel, want, c.asked)
	}
}

// A request on a route that overrides its model is sent with the route's
// model, and the gateway reports that model (x-agentcc-model-used) and prices
// the request on it: the cost the client is shown and the budget plugin
// records is the route's model's, not the requested one's. The budget counts
// it under the requested model.
func TestNonChatRouteReportsAndPricesTheRoutedModel(t *testing.T) {
	for _, c := range r5Cases {
		t.Run(c.name, func(t *testing.T) {
			if c.askedCost == c.routedCost {
				t.Fatalf("the two models cost the same (%s); the case could not tell them apart", c.askedCost)
			}
			g := newR5Gateway(t, true)
			g.route(t, c, c.routed)
			w := g.send(t, c)
			g.checkSentUpstream(t, c, c.routed)
			if got := w.Header().Get("x-agentcc-model-used"); got != c.routed {
				t.Errorf("x-agentcc-model-used = %q, want the routed model %s (requested %s)", got, c.routed, c.asked)
			}
			if got := w.Header().Get("x-agentcc-cost"); got != c.routedCost {
				t.Errorf("x-agentcc-cost = %q, want %s, the cost on %s (on the requested %s it is %s)", got, c.routedCost, c.routed, c.asked, c.askedCost)
			}
			if got, _ := g.probe.get("cost"); got != c.routedCost {
				t.Errorf("cost metadata = %q, want %s (on the requested %s it is %s)", got, c.routedCost, c.asked, c.askedCost)
			}
			g.checkBudget(t, c, c.routedCost)
		})
	}
}

// The request log entry of such a request names the requested model as
// model, the route's model as resolved_model, and the cost on the route's
// model. The gateway has no key store here: with one, the only requests that
// read routes carry an internal key, which the request log leaves out.
func TestNonChatRouteLogsTheRoutedModel(t *testing.T) {
	for _, c := range r5Cases {
		t.Run(c.name, func(t *testing.T) {
			g := newR5Gateway(t, false)
			g.route(t, c, c.routed)
			w := g.send(t, c)
			g.checkSentUpstream(t, c, c.routed)
			id := w.Header().Get("x-agentcc-request-id")
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			g.flusher.Close(ctx)
			g.logs.mu.Lock()
			entries := append([]map[string]any(nil), g.logs.entries...)
			g.logs.mu.Unlock()
			if len(entries) != 1 || entries[0]["request_id"] != id {
				t.Fatalf("log entries = %v, want exactly the one for request %s", entries, id)
			}
			e := entries[0]
			if e["model"] != c.asked || e["resolved_model"] != c.routed || e["provider"] != c.provider {
				t.Errorf("log entry model %v, resolved_model %v, provider %v; want %s, %s, %s",
					e["model"], e["resolved_model"], e["provider"], c.asked, c.routed, c.provider)
			}
			meta, _ := e["metadata"].(map[string]any)
			if meta["cost"] != c.routedCost {
				t.Errorf("log entry metadata.cost = %v, want %s, the cost on %s (on the requested %s it is %s)", meta["cost"], c.routedCost, c.routed, c.asked, c.askedCost)
			}
		})
	}
}

// A route that sends a model name with no price (an Azure deployment name,
// say) is priced on the requested model, as chat is (the cost plugin's
// modelCandidates) and as the platform prices a log entry that carries no
// cost (resolved_model, then model). The cost estimate, which budgets count,
// is the requested model's, cost_source names the source of that price, and
// the budget counts the request under the requested model, as it does on
// every endpoint. The gateway still reports the name it sent.
func TestNonChatRouteToAModelWithNoPriceIsPricedOnTheRequestedModel(t *testing.T) {
	for _, c := range r5Cases {
		t.Run(c.name, func(t *testing.T) {
			g := newR5Gateway(t, true)
			g.route(t, c, c.unpriced)
			w := g.send(t, c)
			g.checkSentUpstream(t, c, c.unpriced)
			if got := w.Header().Get("x-agentcc-model-used"); got != c.unpriced {
				t.Errorf("x-agentcc-model-used = %q, want the routed name %s", got, c.unpriced)
			}
			if got := w.Header().Get("x-agentcc-cost"); got != c.askedCost {
				t.Errorf("x-agentcc-cost = %q, want %s, the cost on the requested %s: %s has no price", got, c.askedCost, c.asked, c.unpriced)
			}
			if got, _ := g.probe.get("cost"); got != c.askedCost {
				t.Errorf("cost metadata = %q, want %s, the cost on the requested %s", got, c.askedCost, c.asked)
			}
			if got, _ := g.probe.get("cost_source"); got != "modeldb" {
				t.Errorf("cost_source = %q, want modeldb, where the requested %s's price comes from", got, c.asked)
			}
			g.checkBudget(t, c, c.askedCost)
		})
	}
}
