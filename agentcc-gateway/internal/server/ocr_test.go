package server

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// ocrUpstreamBody is a recorded Mistral OCR response with the fields the
// gateway does not model: a document annotation, page headers and footers,
// layout blocks, confidence scores and a field newer than the gateway.
const ocrUpstreamBody = `{"pages":[{"index":0,"markdown":"# Invoice 42","images":[{"id":"img-0.jpeg","top_left_x":10,"top_left_y":20,"bottom_right_x":110,"bottom_right_y":220,"image_annotation":"{\"kind\":\"logo\"}"}],"dimensions":{"dpi":200,"height":2200,"width":1700},"header":"ACME Corp","footer":"Page 1 of 2","blocks":[{"type":"text","bbox":[0,0,10,10],"confidence":0.98}],"confidence_scores":{"page":0.97}},{"index":1,"markdown":"Total: 42","images":[],"dimensions":{"dpi":200,"height":2200,"width":1700},"header":null,"footer":"Page 2 of 2"}],"model":"mistral-ocr-4-0","document_annotation":"{\"invoice_number\":\"42\",\"total\":42}","usage_info":{"pages_processed":2,"doc_size_bytes":48213},"x_future_field":{"kept":true}}`

// ocrUpstream is a Mistral-shaped /v1/ocr upstream that answers
// ocrUpstreamBody and records every body it was sent.
type ocrUpstream struct {
	*httptest.Server
	mu     sync.Mutex
	bodies [][]byte
}

func startOCRUpstream(t *testing.T) *ocrUpstream {
	t.Helper()
	u := &ocrUpstream{}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/ocr" {
			http.NotFound(w, r)
			return
		}
		body, _ := io.ReadAll(r.Body)
		u.mu.Lock()
		u.bodies = append(u.bodies, body)
		u.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		io.WriteString(w, ocrUpstreamBody)
	}))
	t.Cleanup(u.Close)
	return u
}

// received is every body the upstream was sent, in order.
func (u *ocrUpstream) received() [][]byte {
	u.mu.Lock()
	defer u.mu.Unlock()
	return append([][]byte(nil), u.bodies...)
}

// newOCRTestServer is a gateway whose "mistral" provider (the OpenAI-
// compatible adapter, as the mistral preset configures it) serves
// mistral-ocr-4-0 from upstreamURL, running plugins.
func newOCRTestServer(t *testing.T, upstreamURL string, plugins ...pipeline.Plugin) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Providers["mistral"] = config.ProviderConfig{
		BaseURL:   upstreamURL,
		APIFormat: "openai",
		Models:    []string{"mistral-ocr-4-0"},
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("registry: %v", err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(plugins...), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	return srv
}

func postOCR(t *testing.T, srv *Server, body string, headers ...string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest("POST", "/v1/ocr", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	for i := 0; i+1 < len(headers); i += 2 {
		req.Header.Set(headers[i], headers[i+1])
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

// fieldOf is the raw JSON of body's top-level field name.
func fieldOf(t *testing.T, body []byte, name string) string {
	t.Helper()
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(body, &fields); err != nil {
		t.Fatalf("upstream body is not a JSON object: %v: %s", err, body)
	}
	return string(fields[name])
}

// Mistral takes pages as one index, a list of indexes or a range string.
// Each reaches the upstream exactly as
// sent, with every option the gateway does not model
// (OCR 3+ blocks, confidence scores, annotations, tables, header and footer
// extraction). The bodies are compact, so the forwarded bytes equal the sent
// ones; a malformed selection is refused before any upstream call.
func TestOCRPagesListAndRangeForwardedVerbatim(t *testing.T) {
	const options = `"include_blocks":true,"confidence_scores_granularity":"page",` +
		`"bbox_annotation_format":{"type":"json_schema","json_schema":{"name":"img","schema":{"type":"object"}}},` +
		`"document_annotation_format":{"type":"json_schema","json_schema":{"name":"doc","schema":{"type":"object"}}},` +
		`"document_annotation_prompt":"Extract the invoice number","table_format":"html",` +
		`"extract_header":true,"extract_footer":true`
	for _, pages := range []string{`[0,1,2]`, `"0-5"`, `"0-2,7"`, `"3"`, `3`, `0`} {
		t.Run(pages, func(t *testing.T) {
			up := startOCRUpstream(t)
			srv := newOCRTestServer(t, up.URL)
			body := `{"model":"mistral-ocr-4-0","document":{"type":"document_url","document_url":"https://example.com/invoice.pdf"},"pages":` +
				pages + `,` + options + `}`

			w := postOCR(t, srv, body)
			if w.Code != http.StatusOK {
				t.Fatalf("status = %d, want 200: %s", w.Code, w.Body.String())
			}
			got := up.received()
			if len(got) != 1 {
				t.Fatalf("upstream calls = %d, want 1", len(got))
			}
			if string(got[0]) != body {
				t.Errorf("upstream body differs from the request:\n got %s\nwant %s", got[0], body)
			}
			if p := fieldOf(t, got[0], "pages"); p != pages {
				t.Errorf("upstream pages = %s, want %s", p, pages)
			}
		})
	}

	for _, pages := range []string{`-1`, `1.5`, `1e3`, `[-1]`, `[]`, `[0,1.5]`, `"5-2"`, `"abc"`, `"0-"`, `{}`, `true`} {
		t.Run("refused "+pages, func(t *testing.T) {
			up := startOCRUpstream(t)
			srv := newOCRTestServer(t, up.URL)
			w := postOCR(t, srv, `{"model":"mistral-ocr-4-0","document":{"type":"document_url","document_url":"https://example.com/a.pdf"},"pages":`+pages+`}`)
			if w.Code != http.StatusBadRequest {
				t.Fatalf("status = %d, want 400: %s", w.Code, w.Body.String())
			}
			var e models.ErrorResponse
			_ = json.Unmarshal(w.Body.Bytes(), &e)
			if e.Error.Code != "invalid_pages" {
				t.Errorf("code = %q, want invalid_pages", e.Error.Code)
			}
			if n := len(up.received()); n != 0 {
				t.Errorf("upstream calls = %d, want 0", n)
			}
		})
	}
}

// The client gets the provider's own body: the gateway decodes it only to
// meter it, so the document annotation, headers, footers, blocks,
// confidence scores and fields newer than the gateway all survive.
func TestOCRRawBodyKeepsAnnotation(t *testing.T) {
	up := startOCRUpstream(t)
	srv := newOCRTestServer(t, up.URL)

	w := postOCR(t, srv, `{"model":"mistral-ocr-4-0","document":{"type":"document_url","document_url":"https://example.com/invoice.pdf"}}`)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200: %s", w.Code, w.Body.String())
	}
	if got := w.Body.String(); got != ocrUpstreamBody {
		t.Errorf("response is not the upstream body:\n got %s\nwant %s", got, ocrUpstreamBody)
	}
	if ct := w.Header().Get("Content-Type"); ct != "application/json" {
		t.Errorf("Content-Type = %q, want application/json", ct)
	}
}

// A document uploaded to the provider is read through Mistral's "file"
// chunk; it is forwarded as sent, and one without a file id is refused.
func TestOCRFileChunkAccepted(t *testing.T) {
	up := startOCRUpstream(t)
	srv := newOCRTestServer(t, up.URL)

	body := `{"model":"mistral-ocr-4-0","document":{"type":"file","file_id":"8a1f3c2e-file"}}`
	w := postOCR(t, srv, body)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200: %s", w.Code, w.Body.String())
	}
	got := up.received()
	if len(got) != 1 || string(got[0]) != body {
		t.Fatalf("upstream bodies = %q, want exactly %s", got, body)
	}

	w = postOCR(t, srv, `{"model":"mistral-ocr-4-0","document":{"type":"file"}}`)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("file chunk without file_id: status = %d, want 400", w.Code)
	}
	var e models.ErrorResponse
	_ = json.Unmarshal(w.Body.Bytes(), &e)
	if e.Error.Code != "missing_file_id" {
		t.Errorf("code = %q, want missing_file_id", e.Error.Code)
	}
	if n := len(up.received()); n != 1 {
		t.Errorf("upstream calls = %d, want 1 (the refused request never left)", n)
	}
}

// The body reaches the upstream as sent, so a field the gateway reads may
// appear once, spelled exactly: the model the key's allowlist, pricing and
// log see is the model the upstream runs. The baseline proves the key's model allowlist is
// in the pipeline; every ambiguous body is refused before any upstream call.
func TestOCRAmbiguousFieldRefused(t *testing.T) {
	up := startOCRUpstream(t)
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "ocr-4-only", Key: "sk-agentcc-ocr4-only", Owner: "user", KeyType: "byok",
		Models: []string{"mistral-ocr-4-0"}, Metadata: map[string]string{"org_id": "org-ocr"}}}
	both := []string{"mistral-ocr-4-0", "mistral-ocr-premium"}
	cfg.Providers["mistral"] = config.ProviderConfig{BaseURL: up.URL, APIFormat: "openai", Models: both}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	tenants := tenant.NewStore()
	tenants.Set("org-ocr", &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"mistral": {APIKey: "org-ocr-byok-key", Models: both, Enabled: true},
	}})
	keys := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keys, true)), keys, nil, nil, nil, testModelDBPtr(), tenants, nil)
	srv.ready.Store(true)
	bearer := []string{"Authorization", "Bearer sk-agentcc-ocr4-only"}
	const doc = `"document":{"type":"file","file_id":"f-1"}`

	if w := postOCR(t, srv, `{"model":"mistral-ocr-4-0",`+doc+`}`, bearer...); w.Code != http.StatusOK {
		t.Fatalf("allowed model: status = %d, want 200: %s", w.Code, w.Body.String())
	}
	if w := postOCR(t, srv, `{"model":"mistral-ocr-premium",`+doc+`}`, bearer...); w.Code != http.StatusForbidden {
		t.Fatalf("model outside the key's allowlist: status = %d, want 403: %s", w.Code, w.Body.String())
	}
	if n := len(up.received()); n != 1 {
		t.Fatalf("upstream calls = %d after the baseline, want 1", n)
	}

	for name, tc := range map[string]struct{ body, param string }{
		"case-variant model after":   {`{"model":"mistral-ocr-premium",` + doc + `,"Model":"mistral-ocr-4-0"}`, "Model"},
		"case-variant model before":  {`{"MODEL":"mistral-ocr-4-0","model":"mistral-ocr-premium",` + doc + `}`, "MODEL"},
		"case-variant model alone":   {`{"Model":"mistral-ocr-4-0",` + doc + `}`, "Model"},
		"model twice":                {`{"model":"mistral-ocr-premium",` + doc + `,"model":"mistral-ocr-4-0"}`, "model"},
		"model twice, one escaped":   {`{"model":"mistral-ocr-premium",` + doc + `,"mod\u0065l":"mistral-ocr-4-0"}`, "model"},
		"case-variant document":      {`{"model":"mistral-ocr-4-0",` + doc + `,"Document":{"type":"file","file_id":"f-2"}}`, "Document"},
		"pages folded (long s)":      {`{"model":"mistral-ocr-4-0",` + doc + `,"pages":"0-1","pageſ":[7]}`, "pageſ"},
		"image_limit twice":          {`{"model":"mistral-ocr-4-0",` + doc + `,"image_limit":1,"image_limit":9}`, "image_limit"},
		"include_image_base64 cased": {`{"model":"mistral-ocr-4-0",` + doc + `,"Include_Image_Base64":true}`, "Include_Image_Base64"},
		"document field cased":       {`{"model":"mistral-ocr-4-0","document":{"type":"document_url","document_url":"https://example.com/a.pdf","Document_URL":"https://example.com/b.pdf"}}`, "document.Document_URL"},
		"document type twice":        {`{"model":"mistral-ocr-4-0","document":{"type":"file","file_id":"f-1","type":"document_url"}}`, "document.type"},
		"image_url url cased":        {`{"model":"mistral-ocr-4-0","document":{"type":"image_url","image_url":{"url":"https://example.com/a.png","URL":"https://example.com/b.png"}}}`, "document.image_url.URL"},
	} {
		t.Run(name, func(t *testing.T) {
			w := postOCR(t, srv, tc.body, bearer...)
			if w.Code != http.StatusBadRequest {
				t.Fatalf("status = %d, want 400: %s", w.Code, w.Body.String())
			}
			var e models.ErrorResponse
			if err := json.Unmarshal(w.Body.Bytes(), &e); err != nil {
				t.Fatalf("error body: %v: %s", err, w.Body.String())
			}
			if e.Error.Code != "duplicate_field" {
				t.Errorf("code = %q, want duplicate_field (%s)", e.Error.Code, e.Error.Message)
			}
			if e.Error.Param == nil || *e.Error.Param != tc.param {
				t.Errorf("param = %v, want %q", e.Error.Param, tc.param)
			}
		})
	}
	if n := len(up.received()); n != 1 {
		t.Errorf("upstream calls = %d, want 1 (no ambiguous body left the gateway)", n)
	}

	// Fields the gateway does not read are the upstream's business: an
	// option spelled twice in two cases is forwarded as sent.
	body := `{"model":"mistral-ocr-4-0",` + doc + `,"table_format":"html","Table_Format":"markdown"}`
	if w := postOCR(t, srv, body, bearer...); w.Code != http.StatusOK {
		t.Fatalf("unread option in two cases: status = %d, want 200: %s", w.Code, w.Body.String())
	}
	if got := up.received(); len(got) != 2 || string(got[1]) != body {
		t.Errorf("upstream bodies = %q, want the last exactly %s", got, body)
	}
}

// Mistral also takes image_url as an {"url": ...} object.
func TestOCRImageURLObjectAccepted(t *testing.T) {
	up := startOCRUpstream(t)
	srv := newOCRTestServer(t, up.URL)

	body := `{"model":"mistral-ocr-4-0","document":{"type":"image_url","image_url":{"url":"https://example.com/receipt.png"}}}`
	if w := postOCR(t, srv, body); w.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200: %s", w.Code, w.Body.String())
	}
	if got := up.received(); len(got) != 1 || string(got[0]) != body {
		t.Fatalf("upstream bodies = %q, want exactly %s", got, body)
	}
}

// metadataProbe is a post plugin that runs after cost and keeps the request
// metadata it saw.
type metadataProbe struct {
	mu   sync.Mutex
	seen map[string]string
}

func (p *metadataProbe) Name() string      { return "probe" }
func (p *metadataProbe) Priority() int     { return 900 }
func (p *metadataProbe) PostPriority() int { return 900 }
func (p *metadataProbe) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
func (p *metadataProbe) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.seen = make(map[string]string, len(rc.Metadata))
	for k, v := range rc.Metadata {
		p.seen[k] = v
	}
	return pipeline.ResultContinue()
}

// get is the value key had in the last request's metadata.
func (p *metadataProbe) get(key string) (string, bool) {
	p.mu.Lock()
	defer p.mu.Unlock()
	v, ok := p.seen[key]
	return v, ok
}

// Through the real handler and cost plugin, an OCR call is metered on the
// pages its provider processed (2, both covered by the document annotation:
// 2 x $0.004 + 2 x $0.005), and a caller cannot plant its own page count.
func TestOCRMeteredThroughThePipeline(t *testing.T) {
	const body = `{"model":"mistral-ocr-4-0","document":{"type":"document_url","document_url":"https://example.com/invoice.pdf"}}`
	up := startOCRUpstream(t)
	probe := &metadataProbe{}
	srv := newOCRTestServer(t, up.URL, costplugin.New(true, testModelDBPtr().Load, nil, nil), probe)

	w := postOCR(t, srv, body, "x-agentcc-metadata", `{"ocr_pages":"999","team":"docs"}`)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200: %s", w.Code, w.Body.String())
	}
	if got, _ := probe.get(models.OCRPagesMetadataKey); got != "2" {
		t.Errorf("%s = %q, want 2 (the provider's pages_processed, not the caller's 999)", models.OCRPagesMetadataKey, got)
	}
	if got, _ := probe.get("cost"); got != "0.018000" {
		t.Errorf("cost = %q, want 0.018000", got)
	}
	if got, _ := probe.get("team"); got != "docs" {
		t.Errorf("caller dimension team = %q, want docs (only ocr_pages is gateway-owned)", got)
	}

	// A call the upstream failed processed nothing, and the caller's count
	// does not stand in for one.
	down := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		io.WriteString(w, `{"error":{"message":"upstream down"}}`)
	}))
	defer down.Close()
	srv = newOCRTestServer(t, down.URL, costplugin.New(true, testModelDBPtr().Load, nil, nil), probe)
	if w := postOCR(t, srv, body, "x-agentcc-metadata", `{"ocr_pages":"999"}`); w.Code == http.StatusOK {
		t.Fatalf("failed upstream answered 200: %s", w.Body.String())
	}
	if got, ok := probe.get(models.OCRPagesMetadataKey); ok {
		t.Errorf("unserved request carries %s = %q, want none", models.OCRPagesMetadataKey, got)
	}
}

// A decoded request is forwarded as the body it came from; if the gateway
// rewrites its model, only "model" changes and every other field survives.
// A request built in code is encoded from its fields.
func TestOCRRewrittenModelIsPatchedIntoTheBody(t *testing.T) {
	const sent = `{"model":"mistral-ocr-latest","document":{"type":"file","file_id":"f-1"},"pages":"0-2","table_format":"html"}`
	var req models.OCRRequest
	if err := json.Unmarshal([]byte(sent), &req); err != nil {
		t.Fatal(err)
	}
	out, err := json.Marshal(&req)
	if err != nil || string(out) != sent {
		t.Fatalf("unchanged request forwarded as %s (err %v), want %s", out, err, sent)
	}

	req.Model = "mistral-ocr-4-0"
	out, err = json.Marshal(&req)
	if err != nil {
		t.Fatal(err)
	}
	var got map[string]json.RawMessage
	if err := json.Unmarshal(out, &got); err != nil {
		t.Fatal(err)
	}
	for field, want := range map[string]string{
		"model": `"mistral-ocr-4-0"`, "pages": `"0-2"`, "table_format": `"html"`, "document": `{"type":"file","file_id":"f-1"}`,
	} {
		if string(got[field]) != want {
			t.Errorf("rewritten request %s = %s, want %s", field, got[field], want)
		}
	}

	built := models.OCRRequest{Model: "m", Document: models.OCRDocument{Type: "document_url", DocumentURL: "https://x/a.pdf"}}
	out, err = json.Marshal(&built)
	if err != nil || string(out) != `{"model":"m","document":{"type":"document_url","document_url":"https://x/a.pdf"},"include_image_base64":false}` {
		t.Errorf("built request encoded as %s (err %v)", out, err)
	}
}

// keepTextProbe is a post plugin that keeps an OCR request's text opt-in and
// resolved content mode.
type keepTextProbe struct {
	mu       sync.Mutex
	keepText bool
	mode     models.ContentMode
}

func (p *keepTextProbe) Name() string  { return "keep-text-probe" }
func (p *keepTextProbe) Priority() int { return 900 }
func (p *keepTextProbe) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
func (p *keepTextProbe) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.keepText = rc.OCRRequest != nil && rc.OCRRequest.KeepText
	p.mode = rc.Privacy.ContentMode
	return pipeline.ResultContinue()
}

// The extracted text may be recorded only when the request resolves to the
// full content mode and the org set privacy.content_mode=full itself: the
// default org, an org that only keeps the default, one that stores no bodies
// and a key or header that tightens the mode all keep metadata only.
func TestOCRTextOptInNeedsTheOrgsOwnFullMode(t *testing.T) {
	up := startOCRUpstream(t)
	off := false
	cases := []struct {
		name    string
		privacy *tenant.PrivacyConfig
		header  string
		want    bool
	}{
		{"org opted in", &tenant.PrivacyConfig{ContentMode: "full"}, "", true},
		{"org opted in, header tightens", &tenant.PrivacyConfig{ContentMode: "full"}, "metadata", false},
		{"no privacy block", nil, "", false},
		{"privacy block without a mode", &tenant.PrivacyConfig{}, "", false},
		{"full but no stored bodies", &tenant.PrivacyConfig{ContentMode: "full", Retention: &tenant.PrivacyRetentionConfig{StoreBodies: &off}}, "", false},
		{"metadata", &tenant.PrivacyConfig{ContentMode: "metadata"}, "", false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			cfg := config.DefaultConfig()
			cfg.Auth.Enabled = true
			cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "ocr-user", Key: "sk-agentcc-ocr-org", Owner: "user", KeyType: "byok",
				Metadata: map[string]string{"org_id": "org-ocr"}}}
			cfg.Providers["mistral"] = config.ProviderConfig{BaseURL: up.URL, APIFormat: "openai", Models: []string{"mistral-ocr-4-0"}}
			registry, err := providers.NewRegistry(cfg)
			if err != nil {
				t.Fatal(err)
			}
			tenants := tenant.NewStore()
			tenants.Set("org-ocr", &tenant.OrgConfig{Privacy: tc.privacy, Providers: map[string]*tenant.ProviderConfig{
				"mistral": {APIKey: "org-ocr-byok-key", Models: []string{"mistral-ocr-4-0"}, Enabled: true},
			}})
			probe := &keepTextProbe{}
			srv := New(cfg, "", registry, pipeline.NewEngine(probe), auth.NewKeyStore(cfg.Auth), nil, nil, nil, testModelDBPtr(), tenants, nil)
			srv.ready.Store(true)

			headers := []string{"Authorization", "Bearer sk-agentcc-ocr-org"}
			if tc.header != "" {
				headers = append(headers, "x-agentcc-log-content", tc.header)
			}
			w := postOCR(t, srv, `{"model":"mistral-ocr-4-0","document":{"type":"document_url","document_url":"https://example.com/a.pdf"}}`, headers...)
			if w.Code != http.StatusOK {
				t.Fatalf("status = %d, want 200: %s", w.Code, w.Body.String())
			}
			probe.mu.Lock()
			defer probe.mu.Unlock()
			if probe.keepText != tc.want {
				t.Errorf("KeepText = %v (content mode %q), want %v", probe.keepText, probe.mode, tc.want)
			}
			if probe.mode == "" {
				t.Error("the handler resolved no content mode")
			}
		})
	}
}
