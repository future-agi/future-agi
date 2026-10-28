package pipeline

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestReflectionRequestFeedbackRequiresPolicyIntent(t *testing.T) {
	original := &models.ChatCompletionRequest{Model: "model", Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"original request"`)}}}
	response := &models.ChatCompletionResponse{Choices: []models.Choice{{Message: models.Message{Role: "assistant", Content: json.RawMessage(`"rejected answer"`)}, FinishReason: "stop"}}}
	request, err := reflectionRequest(original, response, "data-leakage-prevention: Data leakage detected: path_traversal, system_info_leak")
	if err != nil {
		t.Fatal(err)
	}
	const expected = "The preceding answer failed the response checks below. Produce a corrected answer to the original request. Comply with the intent of these checks. Do not use alternate spellings, lookalike or invisible characters, spacing, encodings, or other obfuscation to bypass them. If the original request cannot be answered within these checks, say so briefly instead. Do not repeat the rejected answer.\ndata-leakage-prevention: Data leakage detected: path_traversal, system_info_leak"
	var feedback string
	if len(request.Messages) != 3 || json.Unmarshal(request.Messages[2].Content, &feedback) != nil || feedback != expected || request.Messages[2].Role != "user" {
		t.Fatalf("unsafe or mismatched repair instruction: %q", feedback)
	}
	if len(original.Messages) != 1 || request.Messages[1].Role != "assistant" || string(request.Messages[1].Content) != `"rejected answer"` {
		t.Fatal("private repair request did not preserve conversation ownership")
	}
}

func TestCandidateResponseErrorAcceptsNullEnvelope(t *testing.T) {
	response := &models.ChatCompletionResponse{Choices: []models.Choice{{FinishReason: "stop"}}, Extra: map[string]json.RawMessage{"error": json.RawMessage(" null ")}}
	if err := candidateResponseError(response); err != nil {
		t.Fatalf("null extension was mistaken for an error: %v", err)
	}
}
