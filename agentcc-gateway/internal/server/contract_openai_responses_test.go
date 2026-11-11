package server

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
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

// TestContractOpenAIResponsesUsage replays the openai adapter's /v1/responses
// contract fixtures (case.json endpoint "responses", under
// internal/providers/openai/testdata/contracts) through the gateway's own
// /v1/responses handler, with the real adapter behind it, and checks what the
// gateway prices the request from: the model and the token counts
// applyResponsesUsage takes from the response (non-stream) or from the
// terminal event (stream). The adapter passes the body through; this is the
// one place the gateway reads it, so drift in usage shows up as wrong
// billing, not as a broken client. The provider-side suite checks the same
// fixtures' framing and value sets.
func TestContractOpenAIResponsesUsage(t *testing.T) {
	cases := openAIResponsesCases(t)
	seen := map[string]bool{}
	ran := 0
	var mu sync.Mutex
	t.Run("cases", func(t *testing.T) {
		for _, c := range cases {
			t.Run(c.ID(), func(t *testing.T) {
				t.Parallel()
				paths := checkResponsesUsage(t, c)
				mu.Lock()
				defer mu.Unlock()
				ran++
				for _, p := range paths {
					seen[p] = true
				}
			})
		}
	})
	// Like a stale allowlist entry: judged only when every case ran.
	if ran != len(cases) {
		return
	}
	var stale []string
	for p := range responsesUsageNotRead {
		if !seen[p] {
			stale = append(stale, p)
		}
	}
	if len(stale) > 0 {
		sort.Strings(stale)
		t.Errorf("responsesUsageNotRead entries no fixture produced (remove them): %q", stale)
	}
}

// openAIResponsesContracts is the openai adapter's fixture root.
var openAIResponsesContracts = filepath.Join("..", "providers", "openai", "testdata", "contracts")

// responsesUsageNotRead are usage fields of OpenAI's Responses usage
// (ResponseUsage) that responsesPricedFields does not read, each with its
// reason. An unknown usage field outside this list fails: a counter OpenAI
// added is a counter the cost plugin cannot see.
var responsesUsageNotRead = map[string]string{
	"$.usage.input_tokens_details": "known gap O17: cached_tokens and cache_write_tokens are ignored, so a cached prompt is " +
		"priced at the full input rate (cached input is discounted) and cache writes, billed separately for gpt-5.6 and later, " +
		"are not priced at all",
	"$.usage.output_tokens_details": "known gap O17: reasoning_tokens is ignored; it is part of output_tokens, so cost is " +
		"right, but the usage the gateway records has no reasoning breakdown",
	// OpenAI-compatible presets (the openai adapter's <preset>@<model>
	// fixtures).
	"$.usage.prompt_tokens":     fireworksUsageNames,
	"$.usage.completion_tokens": fireworksUsageNames,
	"$.usage.tool_calls_details": "known gap P3: Perplexity's Responses usage reports what its tools cost (tool_calls_details: " +
		"each tool's invocations, cost_usd and token usage); applyResponsesUsage reads input, output and total tokens alone, so " +
		"the tool fees Perplexity bills are not priced",
	"$.usage.num_sources_used":           xaiServerSideUsage,
	"$.usage.num_server_side_tools_used": xaiServerSideUsage,
	"$.usage.cost_in_nano_usd":           xaiServerSideUsage,
	"$.usage.cost_in_usd_ticks":          xaiServerSideUsage,
}

// fireworksUsageNames and xaiServerSideUsage are each one gap, read at
// several usage paths.
const (
	fireworksUsageNames = "known gap FW2: Fireworks reports Responses usage under chat's names, prompt_tokens and " +
		"completion_tokens, with input_tokens and output_tokens null; applyResponsesUsage reads input_tokens and output_tokens " +
		"alone, so a Fireworks Responses request is priced with no input or output tokens (total_tokens alone)"
	xaiServerSideUsage = "known gap XA4: xAI's Responses usage reports its server-side tool use and its own charge " +
		"(num_sources_used, num_server_side_tools_used, cost_in_nano_usd, cost_in_usd_ticks); applyResponsesUsage reads input, " +
		"output and total tokens alone, so the sources and tool calls xAI bills per use are not priced and its charge is not " +
		"recorded"
)

// xaiReasoningOutsideOutput is the openai fixtures' XA1 (contract_preset_xai
// and testdata/contracts/xai@*): xAI's usage counts reasoning in total_tokens
// but not in output_tokens.
const xaiReasoningOutsideOutput = "known gap XA1: xAI's completion_tokens (output_tokens in Responses) leave out the reasoning " +
	"tokens, which total_tokens counts (completion_tokens_details.reasoning_tokens): the gateway prices completion_tokens, so a " +
	"reasoning model's reasoning, which xAI bills as output, is not priced"

// responsesUsageGaps are, per case, the checks known to fail because of a
// gap; contracttest.ExpectGaps fails once one stops failing.
var responsesUsageGaps = map[string][]contracttest.SanityGap{
	"gpt-6-sol/responses_reasoning_cached": {
		{Failure: "cached_tokens", Reason: responsesUsageNotRead["$.usage.input_tokens_details"]},
		{Failure: "cache_write_tokens", Reason: responsesUsageNotRead["$.usage.input_tokens_details"]},
		{Failure: "reasoning_tokens", Reason: responsesUsageNotRead["$.usage.output_tokens_details"]},
	},
	// Keyed by the request's model: the openai fixtures' xai@latest,
	// xai@grok-4.6 and fireworks@accounts/fireworks/models/llama-v3p1-8b-instruct.
	"latest/responses_text": {
		{Failure: "cached_tokens", Reason: responsesUsageNotRead["$.usage.input_tokens_details"]},
		{Failure: "reasoning_tokens", Reason: responsesUsageNotRead["$.usage.output_tokens_details"]},
		{Failure: "is not input", Reason: xaiReasoningOutsideOutput},
	},
	"grok-4.6/stream_responses_text": {
		{Failure: "cached_tokens", Reason: responsesUsageNotRead["$.usage.input_tokens_details"]},
		{Failure: "reasoning_tokens", Reason: responsesUsageNotRead["$.usage.output_tokens_details"]},
		{Failure: "is not input", Reason: xaiReasoningOutsideOutput},
	},
	"accounts/fireworks/models/llama-v3p1-8b-instruct/responses_text": {
		{Failure: "is not input", Reason: responsesUsageNotRead["$.usage.prompt_tokens"]},
	},
	"accounts/fireworks/models/llama-v3p1-8b-instruct/stream_responses_text": {
		{Failure: "is not input", Reason: responsesUsageNotRead["$.usage.prompt_tokens"]},
	},
}

// openAIResponsesCases loads the fixtures whose case.json drives the
// /v1/responses passthrough. contracttest.Discover reads the package's own
// testdata only, so the cases are assembled here from the same files.
func openAIResponsesCases(t *testing.T) []contracttest.Case {
	t.Helper()
	modelDirs, err := os.ReadDir(openAIResponsesContracts)
	if err != nil {
		t.Fatalf("reading the openai fixtures: %v", err)
	}
	var cases []contracttest.Case
	for _, md := range modelDirs {
		if !md.IsDir() {
			continue
		}
		caseDirs, err := os.ReadDir(filepath.Join(openAIResponsesContracts, md.Name()))
		if err != nil {
			t.Fatal(err)
		}
		for _, cd := range caseDirs {
			dir := filepath.Join(openAIResponsesContracts, md.Name(), cd.Name())
			if !cd.IsDir() {
				continue
			}
			spec, err := contracttest.ReadCaseSpec(dir)
			if err != nil {
				t.Fatal(err)
			}
			if spec.Endpoint != contracttest.EndpointResponses || spec.Status >= 400 {
				continue
			}
			c := contracttest.Case{Name: cd.Name(), Dir: dir, Endpoint: spec.Endpoint, Status: 200, Headers: spec.Headers}
			if c.RawRequest, err = os.ReadFile(filepath.Join(dir, contracttest.RequestFile)); err != nil {
				t.Fatal(err)
			}
			if c.Body, err = os.ReadFile(filepath.Join(dir, contracttest.ResponseSSEFile)); err == nil {
				c.IsStream, c.ContentType = true, "text/event-stream"
			} else if c.Body, err = os.ReadFile(filepath.Join(dir, contracttest.ResponseJSONFile)); err == nil {
				c.ContentType = "application/json"
			} else {
				t.Fatalf("%s: no response.json or response.sse", dir)
			}
			var req struct {
				Model string `json:"model"`
			}
			if err := json.Unmarshal(c.RawRequest, &req); err != nil {
				t.Fatalf("%s: %v", dir, err)
			}
			c.Model = req.Model
			c.SanityGaps = responsesUsageGaps[c.Model+"/"+c.Name]
			cases = append(cases, c)
		}
	}
	if len(cases) == 0 {
		t.Fatalf("no responses fixtures under %s", openAIResponsesContracts)
	}
	for id := range responsesUsageGaps {
		found := false
		for _, c := range cases {
			found = found || c.ID() == id
		}
		if !found {
			t.Errorf("responsesUsageGaps names %s, which is not a responses fixture", id)
		}
	}
	return cases
}

// responsesCapture records what the post plugins saw: what the cost plugin
// prices.
type responsesCapture struct {
	mu    sync.Mutex
	usage *models.Usage
	model string
	ran   bool
}

func (p *responsesCapture) Name() string  { return "contract_responses_capture" }
func (p *responsesCapture) Priority() int { return 500 }
func (p *responsesCapture) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
func (p *responsesCapture) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.ran = true
	p.model = rc.ResolvedModel
	if rc.Response != nil && rc.Response.Usage != nil {
		u := *rc.Response.Usage
		p.usage = &u
	}
	return pipeline.ResultContinue()
}

// checkResponsesUsage runs one case through the handler and returns the
// usage paths responsesPricedFields does not read.
func checkResponsesUsage(t *testing.T, c contracttest.Case) []string {
	upstream := contracttest.ReplayServer(t, c)
	cfg := config.DefaultConfig()
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: upstream.URL, APIKey: "test-key", APIFormat: "openai", Models: []string{c.Model}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	capture := &responsesCapture{}
	srv := New(cfg, "", registry, pipeline.NewEngine(capture), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)

	req := httptest.NewRequest(http.MethodPost, "/v1/responses", bytes.NewReader(c.RawRequest))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("status %d: %s", w.Code, w.Body.String())
	}

	priced := c.Body
	if c.IsStream {
		priced = nil
		for _, ev := range contracttest.ParseSSE(c.Body) {
			// An event's type is its name, or the type in its data for an
			// upstream that sends data lines alone (OpenRouter), as
			// responsesEventType reads it.
			if responsesTerminalEvents[ev.Type()] {
				var terminal struct {
					Response json.RawMessage `json:"response"`
				}
				if err := json.Unmarshal(ev.Data, &terminal); err != nil {
					t.Fatalf("terminal event %s: %v", ev.Type(), err)
				}
				priced = terminal.Response
			}
		}
		if priced == nil {
			t.Fatalf("the stream fixture has no terminal event")
		}
		for _, ev := range contracttest.ParseSSE(c.Body) {
			if !bytes.Contains(w.Body.Bytes(), ev.Data) {
				t.Errorf("event %s was not relayed to the client", ev.Type())
			}
			if responsesTerminalEvents[ev.Type()] {
				break // endRelay drains what follows the terminal event, such as OpenRouter's [DONE] sentinel
			}
		}
	} else if !bytes.Equal(w.Body.Bytes(), c.Body) {
		t.Errorf("the client got a different body than the upstream sent")
	}

	var unread []string
	for _, p := range contracttest.UnknownFieldsAt(priced, reflect.TypeFor[responsesPricedFields](), "$") {
		if p == "$.usage" || strings.HasPrefix(p, "$.usage.") {
			unread = append(unread, p)
		}
	}
	for _, p := range unread {
		if _, ok := responsesUsageNotRead[p]; !ok {
			t.Errorf("OpenAI's usage has %s, which applyResponsesUsage does not read: a counter the cost plugin cannot see", p)
		}
	}

	var want struct {
		Model string `json:"model"`
		Usage *struct {
			InputTokens        int `json:"input_tokens"`
			OutputTokens       int `json:"output_tokens"`
			TotalTokens        int `json:"total_tokens"`
			InputTokensDetails struct {
				CachedTokens     int `json:"cached_tokens"`
				CacheWriteTokens int `json:"cache_write_tokens"`
			} `json:"input_tokens_details"`
			OutputTokensDetails struct {
				ReasoningTokens int `json:"reasoning_tokens"`
			} `json:"output_tokens_details"`
		} `json:"usage"`
	}
	if err := json.Unmarshal(priced, &want); err != nil {
		t.Fatalf("decoding the fixture's response: %v", err)
	}
	capture.mu.Lock()
	got, model, ran := capture.usage, capture.model, capture.ran
	capture.mu.Unlock()
	if !ran {
		t.Fatalf("the post plugins never ran, so nothing was priced")
	}
	contracttest.ExpectGaps(t, c, func(t testing.TB) {
		if model != want.Model {
			t.Errorf("priced model = %q, the response says %q", model, want.Model)
		}
		switch {
		case want.Usage == nil && got != nil:
			t.Errorf("the response has no usage but %+v was priced", got)
		case want.Usage == nil:
		case got == nil:
			t.Errorf("the response's usage was not priced")
		default:
			u := want.Usage
			if got.PromptTokens != u.InputTokens || got.CompletionTokens != u.OutputTokens || got.TotalTokens != u.TotalTokens {
				t.Errorf("priced usage %d/%d/%d, the response says input %d, output %d, total %d",
					got.PromptTokens, got.CompletionTokens, got.TotalTokens, u.InputTokens, u.OutputTokens, u.TotalTokens)
			}
			// The cost plugin prices input and output tokens: tokens the
			// total counts outside them go unpriced.
			if u.TotalTokens != u.InputTokens+u.OutputTokens {
				t.Errorf("the response's total_tokens %d is not input %d + output %d, so %d tokens are not priced",
					u.TotalTokens, u.InputTokens, u.OutputTokens, u.TotalTokens-u.InputTokens-u.OutputTokens)
			}
			checkDetail(t, "cached_tokens", u.InputTokensDetails.CachedTokens, got.PromptTokensDetails)
			checkDetail(t, "cache_write_tokens", u.InputTokensDetails.CacheWriteTokens, got.PromptTokensDetails)
			checkDetail(t, "reasoning_tokens", u.OutputTokensDetails.ReasoningTokens, got.CompletionTokensDetails)
		}
	})
	return unread
}

// checkDetail checks a non-zero token counter of the response reaches the
// priced usage's details under the same name.
func checkDetail(t testing.TB, name string, want int, details *json.RawMessage) {
	t.Helper()
	if want == 0 {
		return
	}
	var got map[string]int
	if details != nil {
		_ = json.Unmarshal(*details, &got)
	}
	if got[name] != want {
		t.Errorf("the response reports %s %d, but the priced usage carries %d", name, want, got[name])
	}
}
