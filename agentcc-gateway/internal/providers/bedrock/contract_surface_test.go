package bedrock

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The contract suite (contract_test.go) covers every upstream API this
// adapter parses. These tests pin the two facts that decide what that is:
// which provider capabilities the adapter implements, and which upstream
// API a model ID is routed to.

// capabilityMethods are the methods of the capability interfaces in
// internal/providers/provider.go, each of which calls an upstream API a
// contract case would have to cover (contracttest.Endpoints).
var capabilityMethods = []string{
	"CreateEmbedding", "CreateImage", "CreateSpeech", "CreateTranscription", "CreateTranslation",
	"Rerank", "OCR", "TextCompletion", "CreateResponse", "ChatCompletionViaResponses",
	"ProxyAssistantsRequest", "ProxyVectorStoresRequest", "CreateAnthropicMessage", "CountAnthropicTokens",
	"GenerateContent", "GenAICountTokens", "EmbedContent", "Search", "SubmitVideo", "Open",
}

// The adapter implements chat only: Bedrock embeddings (Titan, Cohere on
// Bedrock), image models and rerank go through InvokeModel with
// model-specific bodies the adapter does not build. When a capability is
// added, this fails, and the new upstream API needs contract cases
// (case.json "endpoint") before it is listed here.
func TestContractSurfaceIsChatOnly(t *testing.T) {
	typ := reflect.TypeFor[*Provider]()
	for _, m := range capabilityMethods {
		if _, has := typ.MethodByName(m); has {
			t.Errorf("*Provider now has %s: the upstream API it calls needs contract cases (case.json endpoint), and this list an update", m)
		}
	}
}

// ListModels answers from nothing: it calls no upstream (Bedrock's
// ListFoundationModels and ListInferenceProfiles are on the control-plane
// host), so it has no response shape to pin. If it starts calling one, this
// fails and the call needs a list_models contract case.
func TestContractListModelsCallsNoUpstream(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Errorf("ListModels called the upstream: %s %s", r.Method, r.URL.Path)
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer srv.Close()
	p := newTestProvider(t, srv.URL)
	t.Cleanup(func() { _ = p.Close() })
	got, err := p.ListModels(context.Background())
	if err != nil || got != nil {
		t.Errorf("ListModels = %v, %v; want nil, nil", got, err)
	}
}

// Cross-region inference profile IDs are a geography prefix plus the model
// ID. The prefixes are the values of CrossRegionInferenceProfileRegion in the
// AWS CDK (github.com/aws/aws-cdk, packages/@aws-cdk/aws-bedrock-alpha/
// bedrock/inference-profiles/cross-region-inference-profile.ts, main, read
// 2026-10-28): global, us, eu, us-gov, apac, jp, au. AWS's own documentation
// pages were not reachable from the authoring session; third-party gateways
// also list ca. and in., which are not in the CDK and are left out here.
//
// A profile ID should go to Converse (isInferenceProfile), like every
// inference-profile, application-inference-profile and foundation-model ARN. The prefix list
// knows ap., which no AWS profile uses, and lacks apac., jp., au. and
// us-gov.: those profiles go to InvokeModel with an Anthropic Messages body
// (known gap B14). A Claude profile then still answers a non-stream request
// (InvokeModel accepts profile IDs), but its stream is empty (gap B1); a
// non-Claude profile (Nova, Llama) gets a body it rejects (gap B13).
//
// The table pins today's routing. Fixing B14 flips the rows marked so.
//
// surfaceGaps defines the gaps this file and the request side name, in the
// "known gap <ID>: <reason>" form the contract fixtures use (the coverage
// package's KNOWN_GAPS.md reads them from here). Every ID a routing row
// names must be one of them.
var surfaceGaps = map[string]string{
	"B10": "known gap B10: request side: the Converse request (converseRequest) has no additionalModelRequestFields or additionalModelResponseFieldPaths, so a model-specific parameter (Claude's thinking budget, Nova's reasoning config) cannot reach a Converse model, and the model-specific response fields it would return never come back",
	"B13": "known gap B13: every model ID that is not an inference profile goes to InvokeModel with an Anthropic Messages body, so a base model ID of another vendor (amazon.nova-*, meta.llama*) gets a body it rejects",
	"B14": "known gap B14: isInferenceProfile knows the us., eu., ap. and global. prefixes only, so apac., jp., au. and us-gov. profiles go to InvokeModel with an Anthropic Messages body (a Claude profile's stream is then empty, gap B1; any other vendor's profile rejects the body, gap B13), and ap. matches no AWS geography",
	"B18": "known gap B18: a bare ARN (application-inference-profile, inference-profile or foundation-model) is cut at its first \"/\" as if its head were a gateway provider name, so only the resource ID goes upstream, to InvokeModel, which knows no such model",
}

func TestContractInferenceProfileRouting(t *testing.T) {
	tests := []struct {
		model string
		// converse is where the adapter sends the model today.
		converse bool
		// gap names the known gap when today's routing is wrong.
		gap string
	}{
		{model: "global.anthropic.claude-sonnet-4-6", converse: true},
		{model: "us.anthropic.claude-sonnet-4-6", converse: true},
		{model: "eu.anthropic.claude-sonnet-4-6", converse: true},
		{model: "apac.anthropic.claude-sonnet-4-6", converse: false, gap: "B14"},
		{model: "jp.anthropic.claude-sonnet-4-6", converse: false, gap: "B14"},
		{model: "au.anthropic.claude-sonnet-4-6", converse: false, gap: "B14"},
		{model: "us-gov.anthropic.claude-sonnet-4-6", converse: false, gap: "B14"},
		{model: "us.amazon.nova-2-lite-v1:0", converse: true},
		{model: "apac.amazon.nova-2-lite-v1:0", converse: false, gap: "B14, then B13"},
		{model: "jp.amazon.nova-2-lite-v1:0", converse: false, gap: "B14, then B13"},
		// The gateway's provider prefix is stripped, so a prefixed ARN keeps
		// its "/" and goes to Converse...
		{model: "bedrock/us.anthropic.claude-sonnet-4-6", converse: true},
		{model: "bedrock/arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/a1b2c3d4e5f6", converse: true},
		// ...but a bare ARN, as a model_map entry or a client sends it, is
		// cut at its first "/" as if "arn:...:application-inference-profile"
		// were a provider (gap B18): the resource ID alone goes to
		// InvokeModel, which knows no such model. A foundation-model ARN
		// happens to reduce to its base model ID.
		{model: "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/a1b2c3d4e5f6", converse: false, gap: "B18"},
		{model: "arn:aws:bedrock:us-east-1:123456789012:inference-profile/apac.anthropic.claude-sonnet-4-6", converse: false, gap: "B18, then B14"},
		{model: "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-opus-5-5", converse: false, gap: "B18"},
		// Base model IDs go to InvokeModel, which is right for Claude and
		// wrong for anything else (gap B13: it gets an Anthropic body).
		{model: "anthropic.claude-opus-5-5", converse: false},
		{model: "amazon.nova-2-lite-v1:0", converse: false, gap: "B13"},
		// "ap." is in the prefix list but is no AWS geography: nothing real
		// takes this row. Drop "ap." when fixing B14.
		{model: "ap.anthropic.claude-sonnet-4-6", converse: true, gap: "B14 (dead prefix)"},
	}

	var mu sync.Mutex
	var paths []string
	var bodies []map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		var body map[string]any
		_ = json.Unmarshal(raw, &body)
		mu.Lock()
		paths, bodies = append(paths, r.URL.Path), append(bodies, body)
		mu.Unlock()
		w.Header().Set("x-amzn-ErrorType", "ValidationException")
		w.WriteHeader(http.StatusBadRequest)
		_, _ = io.WriteString(w, `{"message":"routing probe"}`)
	}))
	defer srv.Close()
	p := newTestProvider(t, srv.URL)
	t.Cleanup(func() { _ = p.Close() })

	for _, tt := range tests {
		for _, id := range strings.FieldsFunc(tt.gap, func(r rune) bool { return r == ',' || r == ' ' || r == '(' || r == ')' }) {
			if strings.HasPrefix(id, "B") && surfaceGaps[id] == "" && id != "B1" {
				t.Errorf("%s: gap %s is not defined in surfaceGaps", tt.model, id)
			}
		}
		t.Run(tt.model, func(t *testing.T) {
			if got := isInferenceProfile(resolveModelName(tt.model)); got != tt.converse {
				t.Errorf("isInferenceProfile = %v, want %v (today's routing; gap %q)", got, tt.converse, tt.gap)
			}

			mu.Lock()
			paths, bodies = nil, nil
			mu.Unlock()
			_, _ = p.ChatCompletion(context.Background(), &models.ChatCompletionRequest{
				Model:    tt.model,
				Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}},
			})
			mu.Lock()
			defer mu.Unlock()
			if len(paths) != 1 {
				t.Fatalf("upstream got %d requests, want 1", len(paths))
			}
			op, anthropicBody := "/invoke", true
			if tt.converse {
				op, anthropicBody = "/converse", false
			}
			if !strings.HasSuffix(paths[0], op) {
				t.Errorf("upstream path = %q, want the %s operation", paths[0], op)
			}
			if _, has := bodies[0]["anthropic_version"]; has != anthropicBody {
				t.Errorf("upstream body has anthropic_version = %v, want %v (an Anthropic Messages body goes with InvokeModel)", has, anthropicBody)
			}
		})
	}
}
