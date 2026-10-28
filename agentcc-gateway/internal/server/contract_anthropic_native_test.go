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
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

// The native /v1/messages passthrough parses its upstream body in this
// package, not in the adapter: the handler bills from it (applyAnthropicUsage,
// and a StreamUsageScanner fed every relayed event). The anthropic adapter's
// contract suite (internal/providers/anthropic/contract_native_test.go)
// replays each anthropic_messages fixture through the adapter and pins the
// billing anthropicfmt extracts in the case's golden.json. This test replays
// the same fixtures through the whole gateway, handler included, and checks
// the handler hands the cost plugin exactly that billing, and the client
// exactly the upstream's answer. The adapter suite owns the fixtures and the
// goldens; nothing here writes either.

// anthropicContracts is the anthropic adapter's fixture tree.
const anthropicContracts = "../providers/anthropic/testdata/contracts"

// nativeHandlerGaps are the failures checkNativeHandler is known to report,
// by case, each with its gap.
var nativeHandlerGaps = map[string][]contracttest.SanityGap{
	"claude-opus-5-5/native_error_rate_limit": {
		{Failure: "the client's response has no retry-after", Reason: retryAfterGap},
	},
	"claude-opus-5-5/stream_native_error_rate_limit": {
		{Failure: "upstream status 429 became 502 at the client", Reason: "known gap X7: StreamAnthropicMessage turns every upstream error status into a 502 ErrUpstreamProvider, and the handler answers with it, so a streaming /v1/messages client (Claude Code) gets a retryable 502 api_error instead of Anthropic's 429 rate_limit_error and its body"},
		{Failure: "the client's response has no retry-after", Reason: retryAfterGap},
	},
}

const retryAfterGap = "known gap X9: the native passthrough does not forward the upstream's response headers (CreateAnthropicMessage and StreamAnthropicMessage return only a body and a status), so a client such as Claude Code, whose SDK honours retry-after, never sees it, nor anthropic-ratelimit-* or request-id"

// nativeCase is one anthropic_messages or anthropic_count_tokens fixture.
type nativeCase struct {
	c       contracttest.Case
	request []byte
	golden  struct {
		Status  int                       `json:"status"`
		Error   *contracttest.ErrorResult `json:"error"`
		Billing *struct {
			Model        string `json:"model"`
			InputTokens  int    `json:"input_tokens"`
			OutputTokens int    `json:"output_tokens"`
		} `json:"billing"`
	}
}

// loadNativeCases reads the anthropic fixtures whose endpoint is one of the
// native passthroughs.
func loadNativeCases(t *testing.T) []nativeCase {
	t.Helper()
	modelDirs, err := os.ReadDir(anthropicContracts)
	if err != nil {
		t.Fatalf("reading the anthropic fixtures: %v", err)
	}
	var out []nativeCase
	for _, md := range modelDirs {
		if !md.IsDir() {
			continue
		}
		caseDirs, err := os.ReadDir(filepath.Join(anthropicContracts, md.Name()))
		if err != nil {
			t.Fatal(err)
		}
		for _, cd := range caseDirs {
			if !cd.IsDir() {
				continue
			}
			dir := filepath.Join(anthropicContracts, md.Name(), cd.Name())
			spec, err := contracttest.ReadCaseSpec(dir)
			if err != nil {
				t.Fatal(err)
			}
			if spec.Endpoint != contracttest.EndpointAnthropicMessages && spec.Endpoint != contracttest.EndpointAnthropicCountTokens {
				continue
			}
			out = append(out, readNativeCase(t, md.Name(), cd.Name(), dir, spec))
		}
	}
	if len(out) == 0 {
		t.Fatalf("no anthropic_messages fixtures under %s", anthropicContracts)
	}
	return out
}

func readNativeCase(t *testing.T, modelDir, name, dir string, spec contracttest.CaseSpec) nativeCase {
	t.Helper()
	nc := nativeCase{c: contracttest.Case{
		Name: name, Model: modelDir, Dir: dir, Endpoint: spec.Endpoint, Status: spec.Status,
		Headers: spec.Headers, ContentType: spec.ContentType, SanityGaps: nativeHandlerGaps[modelDir+"/"+name],
	}}
	if nc.c.Status == 0 {
		nc.c.Status = http.StatusOK
	}
	for file, contentType := range map[string]string{
		contracttest.ResponseJSONFile: "application/json",
		contracttest.ResponseSSEFile:  "text/event-stream",
	} {
		body, err := os.ReadFile(filepath.Join(dir, file))
		if err != nil {
			continue
		}
		nc.c.Body = body
		if nc.c.ContentType == "" {
			nc.c.ContentType = contentType
		}
	}
	if nc.c.Body == nil {
		t.Fatalf("%s: no response.json or response.sse", dir)
	}
	var err error
	if nc.request, err = os.ReadFile(filepath.Join(dir, contracttest.RequestFile)); err != nil {
		t.Fatal(err)
	}
	var head struct {
		Stream bool `json:"stream"`
	}
	if err := json.Unmarshal(nc.request, &head); err != nil {
		t.Fatalf("%s: %v", dir, err)
	}
	nc.c.IsStream = head.Stream
	raw, err := os.ReadFile(nc.c.GoldenPath())
	if err != nil {
		t.Fatalf("%v (run the anthropic contract suite with -update first)", err)
	}
	if err := json.Unmarshal(raw, &nc.golden); err != nil {
		t.Fatalf("%s: %v", nc.c.GoldenPath(), err)
	}
	return nc
}

// billingCapture records what the post-plugins (the cost plugin among them)
// are handed.
type billingCapture struct {
	mu     sync.Mutex
	runs   int
	model  string
	usage  *models.Usage
	errors []error
}

func (p *billingCapture) Name() string  { return "billing_capture" }
func (p *billingCapture) Priority() int { return 100 }
func (p *billingCapture) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
func (p *billingCapture) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.runs++
	p.model = rc.ResolvedModel
	if rc.Response != nil && rc.Response.Usage != nil {
		u := *rc.Response.Usage
		p.usage = &u
	}
	p.errors = append([]error(nil), rc.Errors...)
	return pipeline.ResultContinue()
}

func TestContractAnthropicNativePassthrough(t *testing.T) {
	for _, nc := range loadNativeCases(t) {
		t.Run(nc.c.ID(), func(t *testing.T) {
			t.Parallel()
			upstream := contracttest.ReplayServer(t, nc.c)
			capture := &billingCapture{}
			cfg := config.DefaultConfig()
			cfg.Providers["anthropic"] = config.ProviderConfig{
				BaseURL: upstream.URL, APIKey: "test-key", APIFormat: "anthropic", Models: []string{nc.c.Model},
			}
			registry, err := providers.NewRegistry(cfg)
			if err != nil {
				t.Fatalf("creating registry: %v", err)
			}
			srv := New(cfg, "", registry, pipeline.NewEngine(capture), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
			srv.ready.Store(true)

			path := "/v1/messages"
			if nc.c.Endpoint == contracttest.EndpointAnthropicCountTokens {
				path = "/v1/messages/count_tokens"
			}
			req := httptest.NewRequest(http.MethodPost, path, bytes.NewReader(nc.request))
			req.Header.Set("Content-Type", "application/json")
			req.Header.Set("anthropic-version", "2023-06-01")
			rec := httptest.NewRecorder()
			srv.httpServer.Handler.ServeHTTP(rec, req)

			// The registry probes a local upstream with GET /v1/models; count
			// only the passthrough's own requests.
			sent := 0
			for _, r := range upstream.Requests() {
				if r.Method == http.MethodPost {
					sent++
				}
			}
			if sent != 1 {
				t.Fatalf("the gateway sent %d POST requests upstream, want 1", sent)
			}
			checkNativeHandler(t, nc, rec, capture)
		})
	}
}

// checkNativeHandler checks what the client got and what the handler billed
// against the upstream's answer and the adapter suite's golden.
func checkNativeHandler(t *testing.T, nc nativeCase, rec *httptest.ResponseRecorder, capture *billingCapture) {
	t.Helper()
	c := nc.c
	contracttest.ExpectGaps(t, c, func(t testing.TB) {
		switch {
		case rec.Code != c.Status:
			t.Errorf("upstream status %d became %d at the client", c.Status, rec.Code)
		case !bytes.Equal(rec.Body.Bytes(), c.Body):
			t.Errorf("the client got a body that is not the upstream's:\n%s", rec.Body.String())
		}
		if v, ok := c.Headers["retry-after"]; ok && rec.Header().Get("Retry-After") == "" {
			t.Errorf("the upstream sent retry-after: %s, but the client's response has no retry-after", v)
		}
	})
	if c.Endpoint == contracttest.EndpointAnthropicCountTokens {
		return
	}

	capture.mu.Lock()
	defer capture.mu.Unlock()
	if capture.runs != 1 {
		t.Fatalf("the post-plugins ran %d times, want once", capture.runs)
	}
	b := nc.golden.Billing
	switch {
	case b == nil && capture.usage != nil:
		t.Errorf("the handler billed %+v; the adapter suite's golden (%s) bills nothing", *capture.usage, c.GoldenPath())
	case b == nil:
	case capture.usage == nil:
		t.Errorf("the handler billed nothing; the adapter suite's golden (%s) bills %+v", c.GoldenPath(), *b)
	default:
		wantPrompt, wantCompletion := b.InputTokens, b.OutputTokens
		if streamEndsWithoutDelta(c) {
			// applyAnthropicStreamUsage estimates the output of a stream cut
			// before message_delta from what was relayed.
			wantCompletion = capture.usage.CompletionTokens
		}
		if capture.usage.PromptTokens != wantPrompt || capture.usage.CompletionTokens != wantCompletion ||
			capture.usage.TotalTokens != capture.usage.PromptTokens+capture.usage.CompletionTokens {
			t.Errorf("the handler bills %d prompt + %d completion tokens; the adapter suite's golden (%s) says %d + %d. "+
				"If the handler changed, change billNative in internal/providers/anthropic/contract_native_test.go to match and rerun it with -update",
				capture.usage.PromptTokens, capture.usage.CompletionTokens, c.GoldenPath(), wantPrompt, wantCompletion)
		}
		if capture.model != b.Model {
			t.Errorf("the handler records model %q; the golden says %q", capture.model, b.Model)
		}
	}
	// A failure the upstream reports inside a 200 stream must still be
	// recorded as one.
	if c.IsStream && !c.IsError() && hasErrorEvent(c.Body) && len(capture.errors) == 0 {
		t.Errorf("the upstream's error event was relayed but the request is recorded as a success")
	}
}

// streamEndsWithoutDelta reports whether a stream case never reaches
// message_delta.
func streamEndsWithoutDelta(c contracttest.Case) bool {
	if !c.IsStream || c.IsError() {
		return false
	}
	for _, ev := range contracttest.ParseSSE(c.Body) {
		if ev.Name == "message_delta" {
			return false
		}
	}
	return true
}

func hasErrorEvent(body []byte) bool {
	for _, ev := range contracttest.ParseSSE(body) {
		if ev.Name == "error" {
			return true
		}
	}
	return false
}

// TestContractAnthropicNativeHandlerGapsExist keeps nativeHandlerGaps from
// naming cases that do not exist.
func TestContractAnthropicNativeHandlerGapsExist(t *testing.T) {
	have := map[string]bool{}
	for _, nc := range loadNativeCases(t) {
		have[nc.c.ID()] = true
	}
	var missing []string
	for id := range nativeHandlerGaps {
		if !have[id] {
			missing = append(missing, id)
		}
	}
	sort.Strings(missing)
	if len(missing) > 0 {
		t.Errorf("nativeHandlerGaps names cases the anthropic fixtures do not have: %q", missing)
	}
}
