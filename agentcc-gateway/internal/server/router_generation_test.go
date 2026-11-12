package server

import (
	"context"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestRouterGenerationBoundToAttempt(t *testing.T) {
	g := newAttemptsGateway(t, attemptsGatewayOpts{})
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = "generation-attempts"
	rc.Request = &models.ChatCompletionRequest{Model: "openrouter/auto"}
	h := g.srv.handlers
	_, finish := h.beginAttempt(context.Background(), rc, models.AttemptPrimary, "", "first", "openrouter/auto", models.CredentialBYOK)
	finish(&models.ChatCompletionResponse{ID: "gen-one", Model: "test/one"}, nil, nil)
	_, failed := h.beginAttempt(context.Background(), rc, models.AttemptRetry, "", "first", "openrouter/auto", models.CredentialBYOK)
	failed(nil, nil, &models.APIError{Status: 502, BillingEvidence: &models.ProviderBillingEvidence{Generation: models.GenerationEvidence{ID: "gen-two", ServedModel: "test/two"}}})
	_, absent := h.beginAttempt(context.Background(), rc, models.AttemptRetry, "", "first", "openrouter/auto", models.CredentialBYOK)
	absent(nil, nil, &models.APIError{Status: 502})
	attempts := rc.AttemptsSnapshot()
	if len(attempts) != 3 || attempts[0].Generation.ID != "gen-one" || attempts[0].Generation.ServedModel != "test/one" || attempts[1].Generation.ID != "gen-two" || attempts[1].Generation.ServedModel != "test/two" || attempts[2].Generation.ID != "" || attempts[2].Generation.ServedModel != "" {
		t.Fatalf("identity crossed attempt boundary: %+v", attempts)
	}
	for _, a := range attempts {
		if a.Model != "openrouter/auto" {
			t.Fatal("served identity replaced requested model")
		}
	}
}

func TestRouterTerminalIdentityObservedWithoutChoices(t *testing.T) {
	var output streamedOutput
	output.observe(models.StreamChunk{ID: "gen-terminal", Model: "test/served"})
	output.observe(models.StreamChunk{})
	if output.generation.ID != "gen-terminal" || output.generation.ServedModel != "test/served" || output.chars != 0 {
		t.Fatalf("terminal evidence=%+v", output)
	}
	output.observe(models.StreamChunk{ID: "gen-conflict"})
	if !output.generation.Conflict {
		t.Fatal("conflicting generation was silently accepted")
	}
}
