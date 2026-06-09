package models

import (
	"encoding/json"
	"testing"
)

func TestRerankRequestMarshalRoundTrip(t *testing.T) {
	topN := 3
	req := RerankRequest{
		Model:           "rerank-english-v3.0",
		Query:           "What is the capital of France?",
		Documents:       []string{"Paris is the capital.", "Berlin is in Germany.", "France is in Europe."},
		TopN:            &topN,
		ReturnDocuments: true,
	}

	data, err := json.Marshal(&req)
	if err != nil {
		t.Fatalf("Marshal error: %v", err)
	}

	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		t.Fatalf("Unmarshal raw error: %v", err)
	}

	for _, key := range []string{"model", "query", "documents", "top_n", "return_documents"} {
		if _, ok := raw[key]; !ok {
			t.Errorf("expected key %q in marshaled JSON", key)
		}
	}
}

func TestRerankRequestUnmarshal(t *testing.T) {
	input := `{
		"model": "rerank-multilingual-v3.0",
		"query": "machine learning",
		"documents": ["doc one", "doc two", "doc three"],
		"top_n": 2,
		"return_documents": true
	}`

	var req RerankRequest
	if err := json.Unmarshal([]byte(input), &req); err != nil {
		t.Fatalf("Unmarshal error: %v", err)
	}

	if req.Model != "rerank-multilingual-v3.0" {
		t.Errorf("Model = %q, want %q", req.Model, "rerank-multilingual-v3.0")
	}
	if req.Query != "machine learning" {
		t.Errorf("Query = %q, want %q", req.Query, "machine learning")
	}
	if len(req.Documents) != 3 {
		t.Fatalf("Documents length = %d, want 3", len(req.Documents))
	}
	if req.Documents[0] != "doc one" {
		t.Errorf("Documents[0] = %q, want %q", req.Documents[0], "doc one")
	}
	if req.TopN == nil || *req.TopN != 2 {
		t.Errorf("TopN = %v, want 2", req.TopN)
	}
	if !req.ReturnDocuments {
		t.Error("ReturnDocuments should be true")
	}
}

func TestRerankRequestOmitEmpty(t *testing.T) {
	req := RerankRequest{
		Model:     "rerank-english-v3.0",
		Query:     "test query",
		Documents: []string{"doc"},
	}

	data, err := json.Marshal(&req)
	if err != nil {
		t.Fatalf("Marshal error: %v", err)
	}

	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		t.Fatalf("Unmarshal raw error: %v", err)
	}

	for _, key := range []string{"top_n", "return_documents"} {
		if _, ok := raw[key]; ok {
			t.Errorf("key %q should be omitted when zero/nil", key)
		}
	}

	// Required fields must be present.
	for _, key := range []string{"model", "query", "documents"} {
		if _, ok := raw[key]; !ok {
			t.Errorf("required key %q should be present", key)
		}
	}
}

func TestRerankResponseMarshalRoundTrip(t *testing.T) {
	resp := RerankResponse{
		ID: "rerank-abc123",
		Results: []RerankResult{
			{
				Index:          0,
				RelevanceScore: 0.95,
				Document:       &RerankDocument{Text: "Paris is the capital."},
			},
			{
				Index:          2,
				RelevanceScore: 0.72,
				Document:       &RerankDocument{Text: "France is in Europe."},
			},
		},
		Meta: &RerankMeta{
			BilledUnits: map[string]int{"search_units": 1},
		},
	}

	data, err := json.Marshal(&resp)
	if err != nil {
		t.Fatalf("Marshal error: %v", err)
	}

	var got RerankResponse
	if err := json.Unmarshal(data, &got); err != nil {
		t.Fatalf("Unmarshal error: %v", err)
	}

	if got.ID != "rerank-abc123" {
		t.Errorf("ID = %q, want %q", got.ID, "rerank-abc123")
	}
	if len(got.Results) != 2 {
		t.Fatalf("Results length = %d, want 2", len(got.Results))
	}
	if got.Results[0].Index != 0 {
		t.Errorf("Results[0].Index = %d, want 0", got.Results[0].Index)
	}
	if got.Results[0].RelevanceScore != 0.95 {
		t.Errorf("Results[0].RelevanceScore = %f, want 0.95", got.Results[0].RelevanceScore)
	}
	if got.Results[0].Document == nil || got.Results[0].Document.Text != "Paris is the capital." {
		t.Errorf("Results[0].Document.Text = %v, want %q", got.Results[0].Document, "Paris is the capital.")
	}
	if got.Results[1].Index != 2 {
		t.Errorf("Results[1].Index = %d, want 2", got.Results[1].Index)
	}
	if got.Meta == nil {
		t.Fatal("Meta should not be nil")
	}
	if got.Meta.BilledUnits["search_units"] != 1 {
		t.Errorf("Meta.BilledUnits[search_units] = %d, want 1", got.Meta.BilledUnits["search_units"])
	}
}

func TestRerankResponseUnmarshal(t *testing.T) {
	input := `{
		"id": "rerank-xyz",
		"results": [
			{"index": 1, "relevance_score": 0.88, "document": {"text": "relevant doc"}},
			{"index": 0, "relevance_score": 0.55}
		],
		"meta": {"billed_units": {"search_units": 2}}
	}`

	var resp RerankResponse
	if err := json.Unmarshal([]byte(input), &resp); err != nil {
		t.Fatalf("Unmarshal error: %v", err)
	}

	if resp.ID != "rerank-xyz" {
		t.Errorf("ID = %q, want %q", resp.ID, "rerank-xyz")
	}
	if len(resp.Results) != 2 {
		t.Fatalf("Results length = %d, want 2", len(resp.Results))
	}
	if resp.Results[0].RelevanceScore != 0.88 {
		t.Errorf("Results[0].RelevanceScore = %f, want 0.88", resp.Results[0].RelevanceScore)
	}
	if resp.Results[0].Document == nil || resp.Results[0].Document.Text != "relevant doc" {
		t.Errorf("Results[0].Document.Text = %v, want %q", resp.Results[0].Document, "relevant doc")
	}
	if resp.Results[1].Document != nil {
		t.Error("Results[1].Document should be nil when not provided")
	}
	if resp.Meta == nil || resp.Meta.BilledUnits["search_units"] != 2 {
		t.Errorf("Meta.BilledUnits = %v, want search_units=2", resp.Meta)
	}
}

func TestRerankResponseOmitEmpty(t *testing.T) {
	resp := RerankResponse{
		ID: "rerank-minimal",
		Results: []RerankResult{
			{Index: 0, RelevanceScore: 0.5},
		},
	}

	data, err := json.Marshal(&resp)
	if err != nil {
		t.Fatalf("Marshal error: %v", err)
	}

	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		t.Fatalf("Unmarshal raw error: %v", err)
	}

	if _, ok := raw["meta"]; ok {
		t.Error("meta should be omitted when nil")
	}

	// Check nested result omitempty.
	var results []map[string]json.RawMessage
	if err := json.Unmarshal(raw["results"], &results); err != nil {
		t.Fatalf("Unmarshal results error: %v", err)
	}
	if len(results) != 1 {
		t.Fatalf("results length = %d, want 1", len(results))
	}
	if _, ok := results[0]["document"]; ok {
		t.Error("document should be omitted when nil")
	}

	// Required fields must be present.
	for _, key := range []string{"id", "results"} {
		if _, ok := raw[key]; !ok {
			t.Errorf("required key %q should be present", key)
		}
	}
}

func TestRerankMetaOmitEmpty(t *testing.T) {
	meta := RerankMeta{}

	data, err := json.Marshal(&meta)
	if err != nil {
		t.Fatalf("Marshal error: %v", err)
	}

	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		t.Fatalf("Unmarshal raw error: %v", err)
	}

	if _, ok := raw["billed_units"]; ok {
		t.Error("billed_units should be omitted when nil map")
	}
}
