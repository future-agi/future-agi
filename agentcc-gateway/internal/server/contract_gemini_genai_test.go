package server

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

// The native Google GenAI passthrough (/v1beta/models/{model}:generateContent
// and :streamGenerateContent) relays the upstream body to a google-genai
// client and bills from it in this package: applyGenAIUsage, and a
// genaifmt.StreamUsageScanner fed every relayed event. The gemini adapter's
// contract suite (internal/providers/gemini/contract_surfaces_test.go)
// replays each genai_generate fixture through the adapter and pins, in the
// case's golden.json, the billing genaifmt extracts ("billed"). This test
// replays the same fixtures through the whole gateway, handler included, and
// checks the handler hands the cost plugin exactly that billing, and the
// client exactly the upstream's answer. The adapter suite owns the fixtures
// and the goldens; nothing here writes either.

// geminiContracts is the gemini adapter's fixture tree.
const geminiContracts = "../providers/gemini/testdata/contracts"

// genaiHandlerGaps are the failures checkGenAIHandler is known to report, by
// case, each with its gap.
var genaiHandlerGaps = map[string][]contracttest.SanityGap{
	"gemini-3.8-flash/stream_genai_error_rate_limit": {
		{Failure: "upstream status 429 became 502 at the client", Reason: "known gap G22: StreamGenerateContent turns an " +
			"upstream 4xx into a 502 ErrUpstreamProvider whose message is the raw body, and the handler answers with it, so a " +
			"streaming google-genai client gets a 502 INTERNAL instead of the upstream's 429 RESOURCE_EXHAUSTED and its RetryInfo"},
	},
}

// genaiCase is one genai_generate fixture.
type genaiCase struct {
	c       contracttest.Case
	request []byte
	golden  struct {
		Status int                       `json:"status"`
		Error  *contracttest.ErrorResult `json:"error"`
		Billed *struct {
			InputTokens  int    `json:"input_tokens"`
			OutputTokens int    `json:"output_tokens"`
			Model        string `json:"model"`
		} `json:"billed"`
	}
}

// loadGenAICases reads the gemini fixtures whose endpoint is genai_generate.
// Vertex AI keys (vertex@<model>) are left out: the route resolves a bare
// model ID to a provider, and the suite has no Vertex AI passthrough case.
func loadGenAICases(t *testing.T) []genaiCase {
	t.Helper()
	modelDirs, err := os.ReadDir(geminiContracts)
	if err != nil {
		t.Fatalf("reading the gemini fixtures: %v", err)
	}
	var out []genaiCase
	for _, md := range modelDirs {
		if !md.IsDir() || strings.HasPrefix(md.Name(), "vertex%40") {
			continue
		}
		caseDirs, err := os.ReadDir(filepath.Join(geminiContracts, md.Name()))
		if err != nil {
			t.Fatal(err)
		}
		for _, cd := range caseDirs {
			if !cd.IsDir() {
				continue
			}
			dir := filepath.Join(geminiContracts, md.Name(), cd.Name())
			spec, err := contracttest.ReadCaseSpec(dir)
			if err != nil {
				t.Fatal(err)
			}
			if spec.Endpoint != contracttest.EndpointGenAIGenerate {
				continue
			}
			out = append(out, readGenAICase(t, md.Name(), cd.Name(), dir, spec))
		}
	}
	if len(out) == 0 {
		t.Fatalf("no genai_generate fixtures under %s", geminiContracts)
	}
	return out
}

func readGenAICase(t *testing.T, model, name, dir string, spec contracttest.CaseSpec) genaiCase {
	t.Helper()
	gc := genaiCase{c: contracttest.Case{
		Name: name, Model: model, Dir: dir, Endpoint: spec.Endpoint, Status: spec.Status,
		Headers: spec.Headers, ContentType: spec.ContentType, SanityGaps: genaiHandlerGaps[model+"/"+name],
	}}
	if gc.c.Status == 0 {
		gc.c.Status = http.StatusOK
	}
	for file, contentType := range map[string]string{
		contracttest.ResponseJSONFile: "application/json",
		contracttest.ResponseSSEFile:  "text/event-stream",
	} {
		body, err := os.ReadFile(filepath.Join(dir, file))
		if err != nil {
			continue
		}
		gc.c.Body = body
		gc.c.IsStream = file == contracttest.ResponseSSEFile
		if gc.c.ContentType == "" {
			gc.c.ContentType = contentType
		}
	}
	if gc.c.Body == nil {
		t.Fatalf("%s: no response.json or response.sse", dir)
	}
	// A native body has no stream key: an error case streams when its name
	// says so, as the adapter suite and the recorder decide.
	gc.c.IsStream = gc.c.IsStream || strings.HasPrefix(name, "stream")
	var err error
	if gc.request, err = os.ReadFile(filepath.Join(dir, contracttest.RequestFile)); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(gc.c.GoldenPath())
	if err != nil {
		t.Fatalf("%v (run the gemini contract suite with -update first)", err)
	}
	if err := json.Unmarshal(raw, &gc.golden); err != nil {
		t.Fatalf("%s: %v", gc.c.GoldenPath(), err)
	}
	return gc
}

// genaiBillingCapture records what the post-plugins (the cost plugin among
// them) are handed.
type genaiBillingCapture struct {
	mu    sync.Mutex
	runs  int
	model string
	usage *models.Usage
}

func (p *genaiBillingCapture) Name() string  { return "genai_billing_capture" }
func (p *genaiBillingCapture) Priority() int { return 100 }
func (p *genaiBillingCapture) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
func (p *genaiBillingCapture) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.runs++
	p.model = rc.ResolvedModel
	if rc.Response != nil && rc.Response.Usage != nil {
		u := *rc.Response.Usage
		p.usage = &u
	}
	return pipeline.ResultContinue()
}

func TestContractGeminiGenAIPassthrough(t *testing.T) {
	for _, gc := range loadGenAICases(t) {
		t.Run(gc.c.ID(), func(t *testing.T) {
			t.Parallel()
			upstream := contracttest.ReplayServer(t, gc.c)
			capture := &genaiBillingCapture{}
			cfg := config.DefaultConfig()
			cfg.Providers["gemini"] = config.ProviderConfig{
				BaseURL: upstream.URL, APIKey: "test-key", APIFormat: "gemini", Models: []string{gc.c.Model},
			}
			registry, err := providers.NewRegistry(cfg)
			if err != nil {
				t.Fatalf("creating registry: %v", err)
			}
			srv := New(cfg, "", registry, pipeline.NewEngine(capture), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
			srv.ready.Store(true)

			path := "/v1beta/models/" + gc.c.Model + ":generateContent"
			if gc.c.IsStream {
				path = "/v1beta/models/" + gc.c.Model + ":streamGenerateContent?alt=sse"
			}
			req := httptest.NewRequest(http.MethodPost, path, bytes.NewReader(gc.request))
			req.Header.Set("Content-Type", "application/json")
			rec := httptest.NewRecorder()
			srv.httpServer.Handler.ServeHTTP(rec, req)

			// The registry may probe a local upstream with a GET; count only
			// the passthrough's own requests.
			sent := 0
			for _, r := range upstream.Requests() {
				if r.Method == http.MethodPost {
					sent++
				}
			}
			if sent != 1 {
				t.Fatalf("the gateway sent %d POST requests upstream, want 1", sent)
			}
			checkGenAIHandler(t, gc, rec, capture)
		})
	}
}

// checkGenAIHandler checks what the client got and what the handler billed
// against the upstream's answer and the adapter suite's golden.
func checkGenAIHandler(t *testing.T, gc genaiCase, rec *httptest.ResponseRecorder, capture *genaiBillingCapture) {
	t.Helper()
	c := gc.c
	contracttest.ExpectGaps(t, c, func(t testing.TB) {
		switch {
		case rec.Code != c.Status:
			t.Errorf("upstream status %d became %d at the client", c.Status, rec.Code)
		case !bytes.Equal(rec.Body.Bytes(), c.Body):
			t.Errorf("the client got a body that is not the upstream's:\n%s", rec.Body.String())
		}
	})

	capture.mu.Lock()
	defer capture.mu.Unlock()
	b := gc.golden.Billed
	switch {
	case b == nil && capture.usage != nil:
		t.Errorf("the handler billed %+v; the adapter suite's golden (%s) bills nothing", *capture.usage, c.GoldenPath())
	case b == nil:
	case capture.runs != 1:
		t.Fatalf("the post-plugins ran %d times, want once", capture.runs)
	case capture.usage == nil:
		t.Errorf("the handler billed nothing; the adapter suite's golden (%s) bills %+v", c.GoldenPath(), *b)
	default:
		if capture.usage.PromptTokens != b.InputTokens || capture.usage.CompletionTokens != b.OutputTokens ||
			capture.usage.TotalTokens != capture.usage.PromptTokens+capture.usage.CompletionTokens {
			t.Errorf("the handler bills %d prompt + %d completion tokens; the adapter suite's golden (%s) says %d + %d. "+
				"If the handler changed, change billGenAI in internal/providers/gemini/contract_surfaces_test.go to match "+
				"and rerun that suite with -update", capture.usage.PromptTokens, capture.usage.CompletionTokens,
				c.GoldenPath(), b.InputTokens, b.OutputTokens)
		}
		if capture.model != b.Model {
			t.Errorf("the handler records model %q; the golden says %q", capture.model, b.Model)
		}
	}
}

// TestContractGeminiGenAIHandlerGapsExist keeps genaiHandlerGaps from naming
// cases that do not exist.
func TestContractGeminiGenAIHandlerGapsExist(t *testing.T) {
	have := map[string]bool{}
	for _, gc := range loadGenAICases(t) {
		have[gc.c.ID()] = true
	}
	var missing []string
	for id := range genaiHandlerGaps {
		if !have[id] {
			missing = append(missing, id)
		}
	}
	sort.Strings(missing)
	if len(missing) > 0 {
		t.Errorf("genaiHandlerGaps names cases the gemini fixtures do not have: %q", missing)
	}
}
