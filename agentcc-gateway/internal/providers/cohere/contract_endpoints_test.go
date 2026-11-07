package cohere

import (
	"bytes"
	"context"
	"encoding/json"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

// The non-chat surfaces of the adapter (case.json "endpoint"):
//
//   - embeddings: CreateEmbedding (POST /v2/embed);
//   - rerank: Rerank (POST /v2/rerank), whose billed_units drive rerank cost;
//   - list_models: ListModels.
//
// Embed and rerank cases live in model directories of their own (an embed or
// rerank model), so their bodies are rooted at "$" like a chat response.
// The list of models shares command-a-03-2025's directory and is rooted at
// "models:$".

// embedStrict is how CreateEmbedding decodes a /v2/embed body.
var embedStrict = &contracttest.Strict{
	Docs: endpointDocs(reflect.TypeFor[cohereEmbedResponse]()),
	Enums: []contracttest.EnumSpec{
		{Spec: "EmbedByTypeResponse.response_type", Paths: []string{"$.response_type"}, Values: []string{"embeddings_floats", "embeddings_by_type"}},
	},
}

// rerankStrict is how Rerank decodes a /v2/rerank body.
var rerankStrict = &contracttest.Strict{Docs: endpointDocs(reflect.TypeFor[cohereRerankResponse]())}

// listModelsStrict: ListModels reads nothing (gap K10), so the list is
// opaque.
var listModelsStrict = &contracttest.Strict{Docs: func(c contracttest.Case) []contracttest.Doc {
	if c.IsError() {
		return errorDocs(c)
	}
	return []contracttest.Doc{{Root: "models:$", Data: c.Body}}
}}

func endpointDocs(typ reflect.Type) func(c contracttest.Case) []contracttest.Doc {
	return func(c contracttest.Case) []contracttest.Doc {
		if c.IsError() {
			return errorDocs(c)
		}
		return []contracttest.Doc{{Root: "$", Data: c.Body, Type: typ}}
	}
}

// runEndpoint drives a non-chat case.
func runEndpoint(t testing.TB, c contracttest.Case, p *Provider, ctx context.Context, out *caseOutput) {
	t.Helper()
	switch c.Endpoint {
	case contracttest.EndpointEmbeddings:
		var req models.EmbeddingRequest
		decodeRequest(t, c, &req)
		out.embed, out.err = p.CreateEmbedding(ctx, &req)
	case contracttest.EndpointRerank:
		var req models.RerankRequest
		decodeRequest(t, c, &req)
		out.rerank, out.err = p.Rerank(ctx, &req)
	case contracttest.EndpointListModels:
		out.models, out.err = p.ListModels(ctx)
	}
}

func decodeRequest(t testing.TB, c contracttest.Case, v any) {
	t.Helper()
	dec := json.NewDecoder(bytes.NewReader(c.RawRequest))
	dec.DisallowUnknownFields()
	if err := dec.Decode(v); err != nil {
		t.Fatalf("request.json is not a %T: %v", v, err)
	}
}

// listModelsResult is the golden form of a ListModels call.
type listModelsResult struct {
	Models []models.ModelObject `json:"models"`
	Error  string               `json:"error,omitempty"`
}

func assertEndpointGoldens(t *testing.T, c contracttest.Case, out caseOutput) {
	t.Helper()
	switch {
	case c.Endpoint == contracttest.EndpointListModels:
		res := listModelsResult{Models: out.models}
		if out.err != nil {
			res.Error = out.err.Error()
		}
		contracttest.AssertGolden(t, c.GoldenPath(), res)
		// ListModels sends no request today (gap K10); pin whatever it sends.
		reqs := out.upstream.Requests()
		if reqs == nil {
			reqs = []contracttest.RecordedRequest{}
		}
		contracttest.AssertGolden(t, c.UpstreamGoldenPath(), reqs)
		return
	case c.IsError():
		contracttest.AssertGolden(t, c.GoldenPath(), contracttest.ErrorGolden(out.err))
	case c.Endpoint == contracttest.EndpointEmbeddings:
		contracttest.AssertGolden(t, c.GoldenPath(), out.embed)
	case c.Endpoint == contracttest.EndpointRerank:
		contracttest.AssertGolden(t, c.GoldenPath(), out.rerank)
	}
	contracttest.AssertGolden(t, c.UpstreamGoldenPath(), out.upstream.Request(t))
}

// checkEndpoint is checkCase for the non-chat endpoints.
func checkEndpoint(t testing.TB, c contracttest.Case, out caseOutput) {
	t.Helper()
	strict := strictFor(t, c)
	switch c.Endpoint {
	case contracttest.EndpointEmbeddings:
		checkUpstreamModel(t, c, out.upstream, "/v2/embed")
	case contracttest.EndpointRerank:
		checkUpstreamModel(t, c, out.upstream, "/v2/rerank")
	}
	switch {
	case c.IsError():
		contracttest.AssertSaneError(t, c, out.err)
	case out.err != nil:
		t.Fatalf("adapter error: %v", out.err)
	case c.Endpoint == contracttest.EndpointEmbeddings:
		contracttest.ExpectGaps(t, c, func(t testing.TB) { checkEmbedding(t, c, out.embed) })
	case c.Endpoint == contracttest.EndpointRerank:
		contracttest.ExpectGaps(t, c, func(t testing.TB) { checkRerank(t, c, out.rerank) })
	case c.Endpoint == contracttest.EndpointListModels:
		contracttest.ExpectGaps(t, c, func(t testing.TB) { checkListedModels(t, c, out) })
	}
	strict.Check(t, c)
	contracttest.AssertNoUnknownShapes(t, c, out.shapes)
}

// checkEmbedding checks the canonical embeddings are the upstream's float
// vectors, in order, with its billed tokens, in the encoding the client
// asked for.
func checkEmbedding(t testing.TB, c contracttest.Case, got *models.EmbeddingResponse) {
	t.Helper()
	var up struct {
		Embeddings struct {
			Float [][]float64 `json:"float"`
		} `json:"embeddings"`
		Meta struct {
			BilledUnits struct {
				InputTokens int `json:"input_tokens"`
			} `json:"billed_units"`
		} `json:"meta"`
	}
	if err := json.Unmarshal(c.Body, &up); err != nil {
		t.Errorf("the upstream body is not an embed response: %v", err)
		return
	}
	var req models.EmbeddingRequest
	_ = json.Unmarshal(c.RawRequest, &req)
	if len(got.Data) != len(up.Embeddings.Float) {
		t.Errorf("%d embeddings reach the client; the upstream returned %d", len(got.Data), len(up.Embeddings.Float))
		return
	}
	for i, d := range got.Data {
		if d.Index != i {
			t.Errorf("embedding %d has index %d", i, d.Index)
		}
		var s string
		if req.EncodingFormat == "base64" && json.Unmarshal(d.Embedding, &s) != nil {
			t.Errorf("the client asked for encoding_format base64, but embedding %d is not a base64 string: %.40s", i, d.Embedding)
			continue
		}
		var vec []float64
		if req.EncodingFormat != "base64" && (json.Unmarshal(d.Embedding, &vec) != nil || !reflect.DeepEqual(vec, up.Embeddings.Float[i])) {
			t.Errorf("embedding %d is not the upstream's float vector", i)
		}
	}
	if up.Meta.BilledUnits.InputTokens == 0 {
		t.Errorf("the upstream bills no input tokens: a renamed billed_units bills nothing")
	}
	if got.Usage == nil || got.Usage.PromptTokens != up.Meta.BilledUnits.InputTokens || got.Usage.TotalTokens != got.Usage.PromptTokens {
		t.Errorf("usage %+v; the upstream billed %d input tokens", got.Usage, up.Meta.BilledUnits.InputTokens)
	}
}

// checkRerank checks the canonical results are the upstream's, and that the
// billed search units the cost plugin reads (meta.billed_units.search_units)
// are the ones Cohere billed.
func checkRerank(t testing.TB, c contracttest.Case, got *models.RerankResponse) {
	t.Helper()
	var up struct {
		Results []struct {
			Index          int     `json:"index"`
			RelevanceScore float64 `json:"relevance_score"`
		} `json:"results"`
		Meta struct {
			BilledUnits struct {
				SearchUnits int `json:"search_units"`
				InputTokens int `json:"input_tokens"`
			} `json:"billed_units"`
		} `json:"meta"`
	}
	if err := json.Unmarshal(c.Body, &up); err != nil {
		t.Errorf("the upstream body is not a rerank response: %v", err)
		return
	}
	if len(got.Results) != len(up.Results) {
		t.Errorf("%d results reach the client; the upstream returned %d", len(got.Results), len(up.Results))
	} else {
		for i, r := range got.Results {
			if r.Index != up.Results[i].Index || r.RelevanceScore != up.Results[i].RelevanceScore {
				t.Errorf("result %d is %d/%v; the upstream's is %d/%v", i, r.Index, r.RelevanceScore, up.Results[i].Index, up.Results[i].RelevanceScore)
			}
		}
	}
	if up.Meta.BilledUnits.SearchUnits == 0 && up.Meta.BilledUnits.InputTokens == 0 {
		t.Errorf("the upstream bills neither search_units nor input_tokens: a renamed billed_units bills nothing")
	}
	var billed int
	if got.Meta != nil {
		billed = got.Meta.BilledUnits["search_units"]
	}
	if billed != up.Meta.BilledUnits.SearchUnits {
		t.Errorf("billed search_units %d reach the cost plugin; the upstream billed search_units %d (and input_tokens %d)",
			billed, up.Meta.BilledUnits.SearchUnits, up.Meta.BilledUnits.InputTokens)
	}
}

// checkListedModels checks ListModels returns what the upstream lists.
func checkListedModels(t testing.TB, c contracttest.Case, out caseOutput) {
	t.Helper()
	var list struct {
		Models []struct {
			Name string `json:"name"`
		} `json:"models"`
	}
	if err := json.Unmarshal(c.Body, &list); err != nil {
		t.Errorf("the upstream's list is not a model list: %v", err)
		return
	}
	if len(out.models) != len(list.Models) {
		t.Errorf("ListModels returned %d models (after %d upstream requests); the upstream lists %d",
			len(out.models), len(out.upstream.Requests()), len(list.Models))
		return
	}
	for i, m := range out.models {
		if m.ID != list.Models[i].Name {
			t.Errorf("ListModels model %d is %q; the upstream lists %q", i, m.ID, list.Models[i].Name)
		}
	}
}
