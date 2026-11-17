package cost

import (
	"context"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestDecisionIncompleteUsageRemainsUnpriced(t *testing.T) {
	db := modeldb.New(modeldb.BundledModels, nil)
	p := New(true, func() *modeldb.ModelDB { return db }, nil, nil)
	for _, model := range []string{"jev-latest", "synthetic-unknown-decision"} {
		for _, body := range []string{
			`{}`, `{"usage":{}}`, `{"usage":null}`,
			`{"usage":{"output_tokens":0}}`, `{"usage":{"input_tokens":0}}`,
			`{"usage":{"input_tokens":null,"output_tokens":null}}`,
			`{"usage":{"input_tokens":-1,"output_tokens":-1}}`,
			`{"usage":{"input_tokens":1000}}`, `{"usage":{"output_tokens":3}}`,
		} {
			t.Run(model+"/"+body, func(t *testing.T) {
				resp, ok := models.ParseDecisionResponse([]byte(body))
				if !ok {
					t.Fatal("invalid synthetic response")
				}
				rc := models.AcquireRequestContext()
				defer rc.Release()
				rc.EndpointType, rc.Model, rc.ResolvedModel = "decision", model, model
				rc.DecisionResponse = resp
				rc.Response = &models.ChatCompletionResponse{Usage: resp.TokenUsage()}
				p.ProcessResponse(context.Background(), rc)
				if rc.Metadata["cost"] != "" || rc.Metadata["cost_source"] != CostSourceUnpriced || rc.Billing.CostSource != models.CostSourceUnpriced {
					t.Fatalf("incomplete usage priced: metadata=%v billing=%+v", rc.Metadata, rc.Billing)
				}
			})
		}
	}
	// A missing Usage pointer must also stay unknown, not take a zero-use exemption.
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.EndpointType, rc.Model = "decision", "synthetic-unknown-decision"
	rc.Response = &models.ChatCompletionResponse{}
	p.ProcessResponse(context.Background(), rc)
	if rc.Metadata["cost_source"] != CostSourceUnpriced || rc.Metadata["cost"] != "" {
		t.Fatalf("nil usage priced: %v", rc.Metadata)
	}
}

func TestDecisionMeasuredUsageStillPrices(t *testing.T) {
	db := modeldb.New(modeldb.BundledModels, nil)
	p := New(true, func() *modeldb.ModelDB { return db }, nil, nil)
	for _, tc := range []struct {
		model, body, cost, source string
	}{
		{"jev-latest", `{"usage":{"input_tokens":0,"output_tokens":0}}`, "0.000000", "modeldb"},
		{"jev-latest", `{"usage":{"input_tokens":1000,"output_tokens":0}}`, "0.000042", "modeldb"},
		{"jev-latest", `{"usage":{"input_tokens":null,"prompt_tokens":1000,"output_tokens":-1,"completion_tokens":0}}`, "0.000042", "modeldb"},
		{"synthetic-unknown-decision", `{"usage":{"input_tokens":0,"output_tokens":0}}`, "", ""},
		{"synthetic-unknown-decision", `{"usage":{"input_tokens":1000,"output_tokens":0}}`, "", CostSourceUnpriced},
	} {
		t.Run(tc.model+"/"+tc.body, func(t *testing.T) {
			resp, ok := models.ParseDecisionResponse([]byte(tc.body))
			if !ok {
				t.Fatal("invalid synthetic response")
			}
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.EndpointType, rc.Model, rc.ResolvedModel = "decision", tc.model, tc.model
			rc.Response = &models.ChatCompletionResponse{Usage: resp.TokenUsage()}
			p.ProcessResponse(context.Background(), rc)
			if rc.Metadata["cost"] != tc.cost || rc.Metadata["cost_source"] != tc.source {
				t.Fatalf("metadata = %v, want cost=%q source=%q", rc.Metadata, tc.cost, tc.source)
			}
		})
	}
}
