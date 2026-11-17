package ratelimit

import (
	"context"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// r7SpyLimiter counts the limiter calls, each of which is one Redis round
// trip on the Redis-backed limiter.
type r7SpyLimiter struct {
	*RateLimiter
	allow, allowN, settle atomic.Int32
}

func (s *r7SpyLimiter) Allow(key string, limit int) (bool, int, int) {
	s.allow.Add(1)
	return s.RateLimiter.Allow(key, limit)
}

func (s *r7SpyLimiter) AllowN(c []redisstate.LimitCheck) redisstate.LimitDecision {
	s.allowN.Add(1)
	return s.RateLimiter.AllowN(c)
}

func (s *r7SpyLimiter) Settle(keys []string, window, delta int64, local bool) {
	s.settle.Add(1)
	s.RateLimiter.Settle(keys, window, delta, local)
}

func newR7Spy(t *testing.T) *r7SpyLimiter {
	s := &r7SpyLimiter{RateLimiter: NewRateLimiter()}
	t.Cleanup(s.Stop)
	return s
}

// An org without limits (and a key without one) takes no limiter call and no
// allocation, before or after the provider call.
func TestR7OrgWideRPMNoLimitsNoRoundTripOrAlloc(t *testing.T) {
	spy := newR7Spy(t)
	p := New(config.RateLimitConfig{Enabled: true}, nil, spy)
	rc := r7RC("org-a", "k1", "m-chat")
	rc.UserID = "u1"
	rc.Response = usage(10)
	ctx := context.Background()
	allocs := testing.AllocsPerRun(200, func() {
		p.ProcessRequest(ctx, rc)
		p.ProcessResponse(ctx, rc)
	})
	if allocs != 0 {
		t.Errorf("an org without limits: %.1f allocs per request, want 0", allocs)
	}
	if n := spy.allow.Load() + spy.allowN.Load() + spy.settle.Load(); n != 0 {
		t.Errorf("an org without limits made %d limiter calls, want 0", n)
	}
}

// A key with only its per-key RPM keeps the single counter call it always
// made.
func TestR7OrgWideRPMPerKeyOnlyKeepsOneCall(t *testing.T) {
	spy := newR7Spy(t)
	p := New(config.RateLimitConfig{Enabled: true}, nil, spy)
	rc := r7RC("org-a", "k1", "m-chat")
	rc.Metadata["org_rate_limit_per_key_rpm"] = "100"
	r7Allowed(t, "per-key only", p.ProcessRequest(context.Background(), rc))
	if spy.allow.Load() != 1 || spy.allowN.Load() != 0 {
		t.Errorf("per-key only: Allow %d AllowN %d, want 1 and 0", spy.allow.Load(), spy.allowN.Load())
	}
}

// Every limit the org set is checked in one call (one Redis round trip), and
// a token reservation is settled in one more after the call.
func TestR7OrgTPMAllLimitsOneCallEachWay(t *testing.T) {
	spy := newR7Spy(t)
	p := New(config.RateLimitConfig{Enabled: true}, nil, spy)
	rc := r7RC("org-a", "k1", "m-chat")
	rc.UserID = "u1"
	for k, v := range map[string]string{
		"org_rate_limit_rpm": "100", "org_rate_limit_tpm": "100000", "org_rate_limit_per_key_rpm": "100",
		"org_rate_limit_per_key_tpm": "100000", "org_rate_limit_per_user_rpm": "100",
		"org_rate_limit_per_model_rpm": `{"m-chat":100}`,
	} {
		rc.Metadata[k] = v
	}
	ctx := context.Background()
	r7Allowed(t, "every limit", p.ProcessRequest(ctx, rc))
	rc.Response = usage(500)
	p.ProcessResponse(ctx, rc)
	if spy.allow.Load() != 0 || spy.allowN.Load() != 1 || spy.settle.Load() != 1 {
		t.Errorf("Allow %d AllowN %d Settle %d, want 0, 1 and 1", spy.allow.Load(), spy.allowN.Load(), spy.settle.Load())
	}
	if _, ok := rc.Metadata[metaTPMHold]; ok {
		t.Errorf("the token hold is still on the request after it settled")
	}
	// A second ProcessResponse (no hold left) settles nothing.
	p.ProcessResponse(ctx, rc)
	if spy.settle.Load() != 1 {
		t.Errorf("settled twice")
	}
}

// keyPart keeps short plain values and hashes the rest, so no two (org, kind,
// value) counters share a key.
func TestR7PerModelRPMCounterKeysAreUnambiguous(t *testing.T) {
	long := string(make([]byte, 65))
	for _, c := range []struct {
		in   string
		kept bool
	}{
		{"gpt-4o", true}, {"org-a", true}, {"u1", true},
		{"ft:gpt-4o:acme:x", false}, {"{tag}", false}, {"#abc", false}, {long, false}, {"", false},
	} {
		got := keyPart(c.in)
		if (got == c.in) != c.kept {
			t.Errorf("keyPart(%q) = %q, kept=%v want %v", c.in, got, got == c.in, c.kept)
		}
		if !c.kept && (len(got) != 33 || got[0] != '#') {
			t.Errorf("keyPart(%q) = %q, want # and 32 hex characters", c.in, got)
		}
	}
	a := orgCounterFor("a:model:b", "model", "c", "rpm")
	b := orgCounterFor("a", "model", "b:model:c", "rpm")
	if a == b {
		t.Errorf("two (org, model) pairs share the counter %q", a)
	}
	if orgCounter("org-a", "rpm") == orgCounter("org-b", "rpm") {
		t.Errorf("two orgs share a counter")
	}
}

// The token hold survives its round trip through the metadata.
func TestR7OrgTPMHoldEncoding(t *testing.T) {
	h := tokenHold{estimate: 512, window: 1729551600, local: true, scope: scopeOrgTPM | scopeKeyTPM, remaining: 9488}
	got, ok := decodeTokenHold(h.encode())
	if !ok || got != h {
		t.Errorf("decode(encode(%+v)) = %+v, %v", h, got, ok)
	}
	for _, bad := range []string{"", "1|2|3", "x|1|0|1|1", "1|2|0|1|y"} {
		if _, ok := decodeTokenHold(bad); ok {
			t.Errorf("decodeTokenHold(%q) accepted", bad)
		}
	}
}

// An embedding request holds an estimate from its input and settles on the
// embedding usage.
func TestR7OrgTPMEmbeddings(t *testing.T) {
	p := r7Plugin()
	ctx := context.Background()
	mk := func() *models.RequestContext {
		rc := r7RC("org-a", "k1", "m-embed")
		rc.Request = nil
		rc.EmbeddingRequest = &models.EmbeddingRequest{Model: "m-embed", Input: []byte(`"hello world"`)}
		rc.Metadata["org_rate_limit_tpm"] = "100"
		return rc
	}
	rc := mk()
	r7Allowed(t, "embedding #1", p.ProcessRequest(ctx, rc))
	rc.EmbeddingResponse = &models.EmbeddingResponse{Usage: &models.EmbeddingUsage{PromptTokens: 150, TotalTokens: 150}}
	p.ProcessResponse(ctx, rc)
	r7Refused(t, "embedding #2", p.ProcessRequest(ctx, mk()), "global_tpm")
}
