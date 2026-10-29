package server

import (
	"bytes"
	"context"
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

// The native Google GenAI passthrough on Vertex AI: the route of
// contract_gemini_genai_test.go (/v1beta/models/{model}:generateContent and
// :streamGenerateContent), with the model resolved to a gemini provider
// configured for Vertex AI, so the adapter relays the body to the
// publisher-model URL. The gemini adapter suite replays each vertex@<model>
// genai_generate fixture through the adapter and pins, in the case's
// golden.json, the billing genaifmt extracts ("billed"), and in
// upstream_request.golden.json the call it makes. This test replays the same
// fixtures through the whole gateway and checks the handler sends that call,
// hands the cost plugin exactly that billing, and hands the client exactly
// the upstream's answer. The adapter suite owns the fixtures and the goldens;
// nothing here writes either.

// vertexFixtureDir marks a Vertex AI fixture directory: vertex@<model> as
// contracttest.ModelDir encodes it.
const vertexFixtureDir = "vertex%40"

// The Vertex AI provider the test configures, as the gemini adapter suite
// does (internal/providers/gemini/contract_test.go newContractProvider): the
// real host, so the adapter's own Vertex AI detection runs, and the
// x-gcp-project / x-gcp-location headers it builds the project path from.
const (
	vertexGenAIBaseURL  = "http://aiplatform.googleapis.com"
	vertexGenAIProject  = "test-project"
	vertexGenAILocation = "global"
)

// vertexGenAIHandlerGaps are the failures checkGenAIHandler is known to
// report for the Vertex AI cases, by case, each with its gap.
var vertexGenAIHandlerGaps = map[string][]contracttest.SanityGap{
	"vertex@gemini-3.8-flash/stream_genai_error_rate_limit": {
		{Failure: "upstream status 429 became 502 at the client", Reason: "known gap G22: StreamGenerateContent turns an " +
			"upstream 4xx into a 502 ErrUpstreamProvider whose message is the raw body, and the handler answers with it, so a " +
			"streaming google-genai client gets a 502 INTERNAL instead of Vertex AI's 429 RESOURCE_EXHAUSTED"},
	},
}

// loadVertexGenAICases reads the gemini fixtures filed as vertex@<model>
// whose endpoint is genai_generate.
func loadVertexGenAICases(t *testing.T) []genaiCase {
	t.Helper()
	modelDirs, err := os.ReadDir(geminiContracts)
	if err != nil {
		t.Fatalf("reading the gemini fixtures: %v", err)
	}
	var out []genaiCase
	for _, md := range modelDirs {
		if !md.IsDir() || !strings.HasPrefix(md.Name(), vertexFixtureDir) {
			continue
		}
		key, err := url.PathUnescape(md.Name())
		if err != nil {
			t.Fatalf("%s: %v", md.Name(), err)
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
			gc := readGenAICase(t, key, cd.Name(), dir, spec)
			gc.c.SanityGaps = vertexGenAIHandlerGaps[gc.c.ID()]
			out = append(out, gc)
		}
	}
	if len(out) == 0 {
		t.Fatalf("no vertex@ genai_generate fixtures under %s", geminiContracts)
	}
	return out
}

func TestContractGeminiVertexGenAIPassthrough(t *testing.T) {
	for _, gc := range loadVertexGenAICases(t) {
		t.Run(gc.c.ID(), func(t *testing.T) {
			t.Parallel()
			model := strings.TrimPrefix(gc.c.Model, "vertex@")
			upstream := contracttest.ReplayServer(t, gc.c)
			replay, err := url.Parse(upstream.URL)
			if err != nil {
				t.Fatalf("replay URL: %v", err)
			}
			var d net.Dialer
			capture := &genaiBillingCapture{}
			cfg := config.DefaultConfig()
			cfg.Providers["vertex"] = config.ProviderConfig{
				BaseURL: vertexGenAIBaseURL, APIKey: "test-key", APIFormat: "gemini", Models: []string{model},
				Headers: map[string]string{"x-gcp-project": vertexGenAIProject, "x-gcp-location": vertexGenAILocation},
				// Every connection goes to the replay server, whatever host the
				// adapter dials.
				DialContext: func(ctx context.Context, network, _ string) (net.Conn, error) {
					return d.DialContext(ctx, network, replay.Host)
				},
			}
			registry, err := providers.NewRegistry(cfg)
			if err != nil {
				t.Fatalf("creating registry: %v", err)
			}
			srv := New(cfg, "", registry, pipeline.NewEngine(capture), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
			srv.ready.Store(true)

			path := "/v1beta/models/" + model + ":generateContent"
			if gc.c.IsStream {
				path = "/v1beta/models/" + model + ":streamGenerateContent?alt=sse"
			}
			req := httptest.NewRequest(http.MethodPost, path, bytes.NewReader(gc.request))
			req.Header.Set("Content-Type", "application/json")
			rec := httptest.NewRecorder()
			srv.httpServer.Handler.ServeHTTP(rec, req)

			var sent []contracttest.RecordedRequest
			for _, r := range upstream.Requests() {
				if r.Method == http.MethodPost {
					sent = append(sent, r)
				}
			}
			if len(sent) != 1 {
				t.Fatalf("the gateway sent %d POST requests upstream, want 1", len(sent))
			}
			checkVertexGenAICall(t, gc.c, sent[0])
			checkGenAIHandler(t, gc, rec, capture)
		})
	}
}

// checkVertexGenAICall checks the handler made the call the adapter suite
// pins in the case's upstream_request.golden.json: Vertex AI's
// publisher-model URL, the query, and the client's body as sent.
func checkVertexGenAICall(t *testing.T, c contracttest.Case, got contracttest.RecordedRequest) {
	t.Helper()
	golden := filepath.Join(c.Dir, contracttest.UpstreamGoldenFile)
	raw, err := os.ReadFile(golden)
	if err != nil {
		t.Fatalf("%v (run the gemini contract suite with -update first)", err)
	}
	var want contracttest.RecordedRequest
	if err := json.Unmarshal(raw, &want); err != nil {
		t.Fatalf("%s: %v", golden, err)
	}
	if got.Method != want.Method || got.Path != want.Path || got.Query != want.Query {
		t.Errorf("the gateway called %s %s?%s; the adapter suite's golden (%s) pins %s %s?%s", got.Method, got.Path, got.Query,
			golden, want.Method, want.Path, want.Query)
	}
	if !reflect.DeepEqual(got.Body, want.Body) {
		t.Errorf("the gateway sent upstream a body that is not the one the adapter suite's golden (%s) pins", golden)
	}
}

// TestContractGeminiVertexGenAIHandlerGapsExist keeps vertexGenAIHandlerGaps
// from naming cases that do not exist.
func TestContractGeminiVertexGenAIHandlerGapsExist(t *testing.T) {
	have := map[string]bool{}
	for _, gc := range loadVertexGenAICases(t) {
		have[gc.c.ID()] = true
	}
	var missing []string
	for id := range vertexGenAIHandlerGaps {
		if !have[id] {
			missing = append(missing, id)
		}
	}
	sort.Strings(missing)
	if len(missing) > 0 {
		t.Errorf("vertexGenAIHandlerGaps names cases the gemini fixtures do not have: %q", missing)
	}
}
