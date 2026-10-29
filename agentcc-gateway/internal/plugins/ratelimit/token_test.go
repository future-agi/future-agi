package ratelimit

import (
	"context"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

// newKeyStoreWithTokenLimit creates a KeyStore holding one key with a token
// limit and no request limit, so only the token budget is under test.
func newKeyStoreWithTokenLimit(rawKey string, tpm int) (*auth.KeyStore, string) {
	ks := auth.NewKeyStore(config.AuthConfig{
		Enabled: true,
		Keys:    []config.AuthKeyConfig{{Name: "tpm-key", Key: rawKey, RateLimitTPM: tpm}},
	})
	key := ks.Authenticate(rawKey)
	if key == nil {
		panic("failed to authenticate test key")
	}
	return ks, key.ID
}

func newRequestContext(keyID string) *models.RequestContext {
	rc := models.AcquireRequestContext()
	rc.Metadata["auth_key_id"] = keyID
	return rc
}

// answered records what a request cost, the way a provider response does.
func answered(rc *models.RequestContext, promptTokens, completionTokens int) {
	rc.Response = &models.ChatCompletionResponse{
		Usage: &models.Usage{
			PromptTokens:     promptTokens,
			CompletionTokens: completionTokens,
			TotalTokens:      promptTokens + completionTokens,
		},
	}
}

// rate_limit_tpm was accepted on a key, carried into the key store and read by
// nobody, so an operator who set a token ceiling had none.
func TestTokenLimit_RefusesOnceTheWindowIsSpent(t *testing.T) {
	ks, keyID := newKeyStoreWithTokenLimit("sk-tpm-1", 1000)
	p := New(config.RateLimitConfig{Enabled: true}, ks, NewRateLimiter())
	defer p.limiter.Stop()

	// First request: nothing spent yet, so it is allowed and then charged.
	rc := newRequestContext(keyID)
	defer rc.Release()
	if res := p.ProcessRequest(context.Background(), rc); res.Error != nil {
		t.Fatalf("first request was refused: %v", res.Error)
	}
	answered(rc, 600, 500) // 1100 tokens, over the 1000 limit
	p.ProcessResponse(context.Background(), rc)

	// Second request: the window is spent.
	next := newRequestContext(keyID)
	defer next.Release()
	res := p.ProcessRequest(context.Background(), next)
	if res.Error == nil {
		t.Fatal("the second request was allowed with the window spent")
	}
	apiErr := res.Error
	if apiErr.Status != http.StatusTooManyRequests {
		t.Errorf("status = %d, want %d", apiErr.Status, http.StatusTooManyRequests)
	}
	if apiErr.Code != "token_rate_limit_exceeded" {
		t.Errorf("code = %q, want %q", apiErr.Code, "token_rate_limit_exceeded")
	}
}

// Under the limit the request goes through, and the headers report the budget.
func TestTokenLimit_UnderTheLimitReportsWhatIsLeft(t *testing.T) {
	ks, keyID := newKeyStoreWithTokenLimit("sk-tpm-2", 1000)
	p := New(config.RateLimitConfig{Enabled: true}, ks, NewRateLimiter())
	defer p.limiter.Stop()

	rc := newRequestContext(keyID)
	defer rc.Release()
	if res := p.ProcessRequest(context.Background(), rc); res.Error != nil {
		t.Fatalf("refused: %v", res.Error)
	}
	assertMetadata(t, rc, "ratelimit_tokens_limit", "1000")
	assertMetadata(t, rc, "ratelimit_tokens_remaining", "1000")

	answered(rc, 100, 50)
	p.ProcessResponse(context.Background(), rc)

	next := newRequestContext(keyID)
	defer next.Release()
	if res := p.ProcessRequest(context.Background(), next); res.Error != nil {
		t.Fatalf("the second request was refused with budget left: %v", res.Error)
	}
	assertMetadata(t, next, "ratelimit_tokens_remaining", "850")
}

// A key with no limit of its own falls back to the organization's, the same
// precedence RPM uses.
func TestTokenLimit_FallsBackToTheOrgLimit(t *testing.T) {
	ks, keyID := newKeyStoreWithTokenLimit("sk-tpm-3", 0)
	p := New(config.RateLimitConfig{Enabled: true}, ks, NewRateLimiter())
	defer p.limiter.Stop()

	rc := newRequestContext(keyID)
	defer rc.Release()
	rc.Metadata["org_rate_limit_tpm"] = "500"

	if res := p.ProcessRequest(context.Background(), rc); res.Error != nil {
		t.Fatalf("refused: %v", res.Error)
	}
	assertMetadata(t, rc, "ratelimit_tokens_limit", "500")

	answered(rc, 400, 200)
	p.ProcessResponse(context.Background(), rc)

	next := newRequestContext(keyID)
	defer next.Release()
	next.Metadata["org_rate_limit_tpm"] = "500"
	if res := p.ProcessRequest(context.Background(), next); res.Error == nil {
		t.Fatal("the org token limit was not enforced")
	}
}

// With no token limit set, nothing is counted and nothing is reported.
func TestTokenLimit_UnsetChargesNothing(t *testing.T) {
	ks, keyID := newKeyStoreWithTokenLimit("sk-tpm-4", 0)
	p := New(config.RateLimitConfig{Enabled: true}, ks, NewRateLimiter())
	defer p.limiter.Stop()

	for range 3 {
		rc := newRequestContext(keyID)
		if res := p.ProcessRequest(context.Background(), rc); res.Error != nil {
			t.Fatalf("refused with no token limit set: %v", res.Error)
		}
		if _, set := rc.Metadata["ratelimit_tokens_limit"]; set {
			t.Error("a token limit was reported when none is configured")
		}
		answered(rc, 100000, 100000)
		p.ProcessResponse(context.Background(), rc)
		rc.Release()
	}
}

// An embedding request reports its tokens on another response, and has to be
// charged for them too.
func TestTokenLimit_ChargesEmbeddingTokens(t *testing.T) {
	ks, keyID := newKeyStoreWithTokenLimit("sk-tpm-5", 100)
	p := New(config.RateLimitConfig{Enabled: true}, ks, NewRateLimiter())
	defer p.limiter.Stop()

	rc := newRequestContext(keyID)
	defer rc.Release()
	if res := p.ProcessRequest(context.Background(), rc); res.Error != nil {
		t.Fatalf("refused: %v", res.Error)
	}
	rc.EndpointType = "embedding"
	rc.EmbeddingResponse = &models.EmbeddingResponse{
		Usage: &models.EmbeddingUsage{PromptTokens: 150, TotalTokens: 150},
	}
	p.ProcessResponse(context.Background(), rc)

	next := newRequestContext(keyID)
	defer next.Release()
	if res := p.ProcessRequest(context.Background(), next); res.Error == nil {
		t.Fatal("embedding tokens were not charged against the window")
	}
}

// The check must not charge: the cost is unknown until the provider answers,
// so a reading that incremented would bill every request one token.
func TestLimiter_AllowTokensDoesNotCharge(t *testing.T) {
	rl := NewRateLimiter()
	defer rl.Stop()

	for range 5 {
		allowed, remaining, _ := rl.AllowTokens("k", 10)
		if !allowed || remaining != 10 {
			t.Fatalf("allowed = %v, remaining = %d, want true and 10", allowed, remaining)
		}
	}

	rl.AddTokens("k", 4)
	if _, remaining, _ := rl.AllowTokens("k", 10); remaining != 6 {
		t.Errorf("remaining after charging 4 = %d, want 6", remaining)
	}
	rl.AddTokens("k", 6)
	if allowed, _, _ := rl.AllowTokens("k", 10); allowed {
		t.Error("the window was still open after it was fully spent")
	}
}

// The request window and the token window are separate counters.
func TestLimiter_TokenWindowIsSeparateFromTheRequestWindow(t *testing.T) {
	rl := NewRateLimiter()
	defer rl.Stop()

	rl.AddTokens(tokenWindowKey("k"), 5000)

	// The request budget for the same key is untouched.
	if allowed, remaining, _ := rl.Allow("k", 10); !allowed || remaining != 9 {
		t.Errorf("Allow = %v with %d remaining, want true and 9", allowed, remaining)
	}
}

var _ TokenLimiter = (*RateLimiter)(nil)

var _ pipeline.Plugin = (*Plugin)(nil)

// A served cache hit spends no upstream tokens, so it must not be charged.
// Charging it exhausts the window on repeated identical requests and then
// refuses the next real one — a 429 for a budget that was never spent.
func TestTokenLimit_ACacheHitIsNotCharged(t *testing.T) {
	ks, keyID := newKeyStoreWithTokenLimit("sk-tpm-cache", 100)
	p := New(config.RateLimitConfig{Enabled: true}, ks, NewRateLimiter())
	defer p.limiter.Stop()

	// A real provider call that costs 60 tokens.
	first := newRequestContext(keyID)
	defer first.Release()
	if res := p.ProcessRequest(context.Background(), first); res.Error != nil {
		t.Fatalf("first request refused: %v", res.Error)
	}
	answered(first, 40, 20)
	p.ProcessResponse(context.Background(), first)

	// The same request again, served from cache. Every hit kind must be free,
	// not only the exact one the pipeline's skip interface covers.
	for _, status := range []string{"hit_exact", "hit_conditional", "hit_semantic"} {
		cached := newRequestContext(keyID)
		if res := p.ProcessRequest(context.Background(), cached); res.Error != nil {
			t.Fatalf("%s: a cached request was refused with 60 of 100 spent: %v", status, res.Error)
		}
		answered(cached, 40, 20) // the cached response still reports its usage
		cached.Flags.CacheHit = true
		cached.Metadata["cache_status"] = status
		p.ProcessResponse(context.Background(), cached)
		cached.Release()
	}

	// Only the one real call counts, so a new request still has budget.
	next := newRequestContext(keyID)
	defer next.Release()
	if res := p.ProcessRequest(context.Background(), next); res.Error != nil {
		t.Fatalf("a new request was refused after three free cache hits: %v", res.Error)
	}
	assertMetadata(t, next, "ratelimit_tokens_remaining", "40")
}
