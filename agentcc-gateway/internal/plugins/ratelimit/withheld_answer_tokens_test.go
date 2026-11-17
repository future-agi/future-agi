package ratelimit

import (
	"context"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// A post-stage check that withholds an answer drops rc.Response and keeps the
// answer's usage on rc.WithheldUsage: the provider gave the answer and counted
// its tokens, so they count toward the token limits as a served answer's do.

// withheld records an answer the provider gave and a check then withheld.
func withheld(rc *models.RequestContext, promptTokens, completionTokens int) {
	rc.Response = nil
	rc.WithheldUsage = &models.Usage{
		PromptTokens:     promptTokens,
		CompletionTokens: completionTokens,
		TotalTokens:      promptTokens + completionTokens,
	}
	rc.Flags.AnswerRefused = true
	rc.AddError(&models.APIError{Status: http.StatusForbidden, Type: models.ErrTypeGuardrail,
		Code: "content_blocked", Message: "Request blocked by guardrail: answer-blocker"})
}

// The key's own window (rate_limit_tpm).
func TestTokenLimit_AWithheldAnswerSpendsTheKeysWindow(t *testing.T) {
	ks, keyID := newKeyStoreWithTokenLimit("sk-tpm-withheld", 1000)
	p := New(config.RateLimitConfig{Enabled: true}, ks, NewRateLimiter())
	defer p.limiter.Stop()

	rc := newRequestContext(keyID)
	defer rc.Release()
	if res := p.ProcessRequest(context.Background(), rc); res.Error != nil {
		t.Fatalf("first request was refused: %v", res.Error)
	}
	withheld(rc, 600, 500) // 1100 tokens, over the 1000 limit
	p.ProcessResponse(context.Background(), rc)

	next := newRequestContext(keyID)
	defer next.Release()
	res := p.ProcessRequest(context.Background(), next)
	if res.Error == nil || res.Error.Code != "token_rate_limit_exceeded" {
		t.Fatalf("the next request after a withheld answer of 1100 tokens: %+v, want token_rate_limit_exceeded", res.Error)
	}
}

// The org's global_tpm: the withheld answer's real usage settles the hold,
// where it used to give the estimate back as for a failed call.
func TestR7OrgTPMCountsAWithheldAnswer(t *testing.T) {
	p := r7Plugin()
	ctx := context.Background()
	mk := func(key string) *models.RequestContext {
		rc := r7RC("org-a", key, "m-chat")
		rc.Metadata["org_rate_limit_tpm"] = "1000"
		return rc
	}
	rc1 := mk("k1")
	r7Allowed(t, "first call", p.ProcessRequest(ctx, rc1))
	withheld(rc1, 1000, 1000)
	p.ProcessResponse(ctx, rc1)
	r7Refused(t, "next call after a withheld answer of 2000 tokens", p.ProcessRequest(ctx, mk("k2")), "global_tpm")
}
