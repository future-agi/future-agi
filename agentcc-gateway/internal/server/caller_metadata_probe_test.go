package server

import (
	"bytes"
	"context"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	budgetpkg "github.com/futureagi/agentcc-gateway/internal/budget"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	budgetplugin "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	loggingplugin "github.com/futureagi/agentcc-gateway/internal/plugins/logging"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// RF14's probes, the assessment's own (authn-A, POL-1, B3), through the real
// handlers and plugins: before the fix a caller-set reserved key changed the
// recorded cost, the org budget, the request log or the gateway's response
// headers; after it, the caller's value changes none of them.

// rf14NotWAV is an upload the gateway cannot measure (an MP3 frame header),
// so the transcription handler has no duration of its own to record.
var rf14NotWAV = append([]byte("ID3\x04\x00\x00\x00\x00\x00\x00\xff\xfb\x90\x64"), bytes.Repeat([]byte{0}, 256)...)

// rf14AudioForm is a multipart upload of data as file name, for model.
func rf14AudioForm(t *testing.T, model, name string, data []byte) (string, []byte) {
	t.Helper()
	var b bytes.Buffer
	mw := multipart.NewWriter(&b)
	fw, err := mw.CreateFormFile("file", name)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := fw.Write(data); err != nil {
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

// newRF14AudioGateway is newR5Gateway with extra plugins ahead of the cost
// plugin: the same providers, the cost and budget plugins, the metadata probe
// and request logging to a webhook sink.
func newRF14AudioGateway(t *testing.T, withKeys bool, extra ...pipeline.Plugin) *r5Gateway {
	t.Helper()
	cfg := config.DefaultConfig()
	g := &r5Gateway{upstreams: map[string]*r5Upstream{}, probe: &metadataProbe{}, logs: startCapcoreLogSink(t)}
	u := startR5Upstream(t)
	g.upstreams["asked"] = u
	cfg.Providers["asked"] = config.ProviderConfig{BaseURL: u.URL, APIKey: "operator-asked", APIFormat: "openai", Models: []string{"whisper-1"}}
	var keyStore *auth.KeyStore
	plugins := append([]pipeline.Plugin(nil), extra...)
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
	g.srv.ready.Store(true)
	return g
}

// sendAudio posts an upload to path with the caller metadata header meta
// (none when empty) and wants a 200.
func (g *r5Gateway) sendAudio(t *testing.T, path, name string, data []byte, meta string) *httptest.ResponseRecorder {
	t.Helper()
	ct, body := rf14AudioForm(t, "whisper-1", name, data)
	req := httptest.NewRequest(http.MethodPost, path, bytes.NewReader(body))
	req.Header.Set("Content-Type", ct)
	if meta != "" {
		req.Header.Set("x-agentcc-metadata", meta)
	}
	if g.key != "" {
		req.Header.Set("Authorization", "Bearer "+g.key)
	}
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("%s: status = %d %s, want 200", path, w.Code, w.Body.String())
	}
	return w
}

// orgSpend is what the org budget counted.
func (g *r5Gateway) orgSpend() float64 {
	total, _ := g.budget.GetSpend("org", "")
	return total
}

// flushedEntries delivers the buffered request logs and returns them.
func (g *r5Gateway) flushedEntries(t *testing.T) []map[string]any {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	g.flusher.Close(ctx)
	g.logs.mu.Lock()
	defer g.logs.mu.Unlock()
	return append([]map[string]any(nil), g.logs.entries...)
}

var rf14AudioPaths = []string{"/v1/audio/transcriptions", "/v1/audio/translations"}

// authn-A / POL-1: a caller's audio_seconds on an upload the gateway cannot
// measure priced the request on the caller's number (600 s of whisper-1 is
// $0.06), which the org budget then counted. Now the caller's value is
// dropped, the request stays unpriced as any unmeasurable upload is, and the
// budget counts nothing.
func TestCallerAudioSecondsDoNotPriceAnUnmeasurableUpload(t *testing.T) {
	for _, path := range rf14AudioPaths {
		t.Run(path, func(t *testing.T) {
			g := newRF14AudioGateway(t, true)
			w := g.sendAudio(t, path, "speech.mp3", rf14NotWAV, `{"audio_seconds":"600","tenant":"acme"}`)
			if got := w.Header().Get("x-agentcc-cost"); got != "" {
				t.Errorf("x-agentcc-cost = %q, want none: the upload's length is unknown and the caller's 600 s is not the gateway's measure", got)
			}
			if got, ok := g.probe.get("audio_seconds"); ok {
				t.Errorf("audio_seconds = %q on the request, want none", got)
			}
			if got, ok := g.probe.get("cost"); ok {
				t.Errorf("cost metadata = %q, want none", got)
			}
			if got, _ := g.probe.get("tenant"); got != "acme" {
				t.Errorf("tenant = %q, want the caller's acme: harmless dimensions still land", got)
			}
			if spend := g.orgSpend(); spend != 0 {
				t.Errorf("org budget spend = %v, want 0", spend)
			}
		})
	}
}

// The control: a WAV the gateway can measure is priced on its own length
// (2 s, $0.0002), whatever the caller sends, before and after the fix.
func TestCallerAudioSecondsDoNotOverrideAMeasuredWAV(t *testing.T) {
	for _, path := range rf14AudioPaths {
		t.Run(path, func(t *testing.T) {
			g := newRF14AudioGateway(t, true)
			w := g.sendAudio(t, path, "speech.wav", r5WAV(2), `{"audio_seconds":"600"}`)
			if got := w.Header().Get("x-agentcc-cost"); got != "0.000200" {
				t.Errorf("x-agentcc-cost = %q, want 0.000200 (2 s measured)", got)
			}
			if got, _ := g.probe.get("audio_seconds"); got != "2.000000" {
				t.Errorf("audio_seconds = %q, want the measured 2.000000", got)
			}
		})
	}
}

// The request log entry carries neither the caller's audio_seconds nor a cost
// priced on it (the gateway has no key store here, so the entry is logged).
func TestCallerAudioSecondsNeverReachTheRequestLog(t *testing.T) {
	g := newRF14AudioGateway(t, false)
	w := g.sendAudio(t, "/v1/audio/transcriptions", "speech.mp3", rf14NotWAV, `{"audio_seconds":"600","tenant":"acme"}`)
	id := w.Header().Get("x-agentcc-request-id")
	entries := g.flushedEntries(t)
	if len(entries) != 1 || entries[0]["request_id"] != id {
		t.Fatalf("log entries = %v, want exactly the one for request %s", entries, id)
	}
	meta, _ := entries[0]["metadata"].(map[string]any)
	if v, ok := meta["audio_seconds"]; ok {
		t.Errorf("log metadata.audio_seconds = %v, want none", v)
	}
	if v, ok := meta["cost"]; ok {
		t.Errorf("log metadata.cost = %v, want none", v)
	}
	if c, _ := entries[0]["cost"].(float64); c != 0 {
		t.Errorf("log cost = %v, want 0", c)
	}
	if meta["tenant"] != "acme" {
		t.Errorf("log metadata.tenant = %v, want the caller's acme", meta["tenant"])
	}
}

// rf14Planter is a pre-plugin that writes key=value on every request: a
// stand-in for any value that reached the request's metadata before the
// upstream call by some route other than the caller channel.
type rf14Planter struct{ key, value string }

func (p rf14Planter) Name() string  { return "rf14-planter" }
func (p rf14Planter) Priority() int { return 1 }
func (p rf14Planter) ProcessRequest(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	rc.SetMetadata(p.key, p.value)
	return pipeline.ResultContinue()
}
func (p rf14Planter) ProcessResponse(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

// POL-1's second half: the handler cleared nothing when it could not read the
// upload, so whatever audio_seconds the request held was priced. Now an
// upload it cannot measure clears audio_seconds, so a non-WAV upload is
// never billed on a number the gateway did not measure.
func TestUnmeasurableUploadClearsAudioSeconds(t *testing.T) {
	for _, path := range rf14AudioPaths {
		t.Run(path, func(t *testing.T) {
			g := newRF14AudioGateway(t, true, rf14Planter{"audio_seconds", "600"})
			w := g.sendAudio(t, path, "speech.mp3", rf14NotWAV, "")
			if got := w.Header().Get("x-agentcc-cost"); got != "" {
				t.Errorf("x-agentcc-cost = %q, want none: an unmeasured upload is not priced", got)
			}
			if got, ok := g.probe.get("audio_seconds"); ok {
				t.Errorf("audio_seconds = %q after an unmeasurable upload, want it cleared", got)
			}
			if spend := g.orgSpend(); spend != 0 {
				t.Errorf("org budget spend = %v, want 0", spend)
			}
		})
	}
}

// B3: a caller's model_alias applied the operator's alias cost factor (here
// 0.1 for the rf14-cheap prefix) to a BYOK token price, recording a tenth of
// the request's cost. Now the request costs the same with or without it, from
// either channel.
func TestCallerModelAliasDoesNotScaleTheCost(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()
	probe := &metadataProbe{}
	srv := createAuthRequiredTestServerWithPlugins(t, mock.URL,
		costplugin.New(true, testModelDBPtr().Load, nil, map[string]float64{"rf14-cheap": 0.1}), probe)

	send := func(meta, bodyMeta string) string {
		t.Helper()
		body := `{"model":"gpt-4o","messages":[{"role":"user","content":"hello"}]` + bodyMeta + `}`
		req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer sk-test-key-001")
		if meta != "" {
			req.Header.Set("x-agentcc-metadata", meta)
		}
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)
		if w.Code != http.StatusOK {
			t.Fatalf("status = %d %s, want 200", w.Code, w.Body.String())
		}
		return w.Header().Get("x-agentcc-cost")
	}

	honest := send("", "")
	if c, err := strconv.ParseFloat(honest, 64); err != nil || c <= 0 {
		t.Fatalf("honest x-agentcc-cost = %q, want a positive cost to compare against", honest)
	}
	for name, got := range map[string]string{
		"header": send(`{"model_alias":"rf14-cheap"}`, ""),
		"body":   send("", `,"metadata":{"model_alias":"rf14-cheap"}`),
	} {
		if got != honest {
			t.Errorf("%s: x-agentcc-cost = %q with a caller model_alias, want the honest %q", name, got, honest)
		}
	}
	if got, ok := probe.get("model_alias"); ok {
		t.Errorf("model_alias = %q on the request, want none: no alias resolved", got)
	}
}

// authn-A's identity and routing half: the routing evidence the gateway
// reports as its own x-agentcc-* response headers, and logs, came from the
// caller when the gateway made no such decision. Now none of it does.
func TestCallerCannotForgeRoutingEvidenceHeaders(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()
	probe := &metadataProbe{}
	srv := createAuthRequiredTestServerWithPlugins(t, mock.URL, probe)

	forged := map[string]string{
		"routing_strategy":     "rf14-forged-strategy",
		"provider_locked":      "true",
		"provider_lock_target": "rf14-forged-provider",
		"complexity_tier":      "rf14-forged-tier",
		"race_winner":          "rf14-forged-winner",
		"fallback_model":       "rf14-forged-model",
		"original_model":       "rf14-forged-model",
		"access_group_matched": "rf14-forged-group",
		"endpoint_type":        "rf14-forged-endpoint",
	}
	var hdr []string
	for k, v := range forged {
		hdr = append(hdr, strconv.Quote(k)+":"+strconv.Quote(v))
	}
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions",
		strings.NewReader(`{"model":"gpt-4o","messages":[{"role":"user","content":"hello"}]}`))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer sk-test-key-001")
	req.Header.Set("x-agentcc-metadata", "{"+strings.Join(hdr, ",")+"}")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d %s, want 200", w.Code, w.Body.String())
	}
	for _, h := range []string{"x-agentcc-routing-strategy", "x-agentcc-provider-locked", "x-agentcc-complexity-tier", "x-agentcc-race-winner"} {
		if got := w.Header().Get(h); strings.HasPrefix(got, "rf14-forged") {
			t.Errorf("%s = %q: the gateway reported the caller's forged evidence as its own", h, got)
		}
	}
	for k, v := range forged {
		if got, _ := probe.get(k); got == v {
			t.Errorf("%s = %q on the request: the caller's forged value reached the log", k, got)
		}
	}
}
