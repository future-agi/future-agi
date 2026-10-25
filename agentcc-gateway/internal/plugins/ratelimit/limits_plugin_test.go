package ratelimit

// The org's rate_limiting section, read from the metadata keys the handler
// stamps. These tests set every key directly.

import (
	"context"
	"encoding/json"
	"net/http"
	"strconv"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

// orgLimitRC is a chat request of org (empty: none) from key keyID for model.
func orgLimitRC(org, keyID, model string) *models.RequestContext {
	rc := &models.RequestContext{Metadata: map[string]string{}}
	if org != "" {
		rc.Metadata["org_id"] = org
		rc.Metadata["key_org_id"] = org
	}
	if keyID != "" {
		rc.Metadata["auth_key_id"] = keyID
	}
	rc.Model = model
	rc.Request = &models.ChatCompletionRequest{
		Model:    model,
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}},
	}
	return rc
}

func newLimitsPlugin() *Plugin {
	p := New(config.RateLimitConfig{Enabled: true}, nil, NewRateLimiter())
	return p
}

func wantAllowed(t *testing.T, what string, res pipeline.PluginResult) {
	t.Helper()
	if res.Error != nil || res.Action != pipeline.Continue {
		msg := ""
		if res.Error != nil {
			msg = res.Error.Message
		}
		t.Fatalf("%s: refused (%s), want allowed", what, msg)
	}
}

// wantRefused checks a 429 that names the org config field that refused it.
func wantRefused(t *testing.T, what string, res pipeline.PluginResult, field string) {
	t.Helper()
	if res.Error == nil {
		t.Fatalf("%s: allowed, want 429 naming %s", what, field)
	}
	e := res.Error
	if e.Status != http.StatusTooManyRequests || e.Code != "rate_limit_exceeded" || e.Type != "rate_limit_error" {
		t.Fatalf("%s: got %d %s/%s, want 429 rate_limit_error/rate_limit_exceeded", what, e.Status, e.Type, e.Code)
	}
	if e.Param == nil || *e.Param != field {
		got := "<nil>"
		if e.Param != nil {
			got = *e.Param
		}
		t.Errorf("%s: param = %s, want %s", what, got, field)
	}
	if !strings.Contains(e.Message, field) {
		t.Errorf("%s: message %q does not name %s", what, e.Message, field)
	}
	if e.RetryAfterSec < 1 || e.RetryAfterSec > 60 {
		t.Errorf("%s: Retry-After = %d, want 1..60", what, e.RetryAfterSec)
	}
}

func usage(total int) *models.ChatCompletionResponse {
	return &models.ChatCompletionResponse{Usage: &models.Usage{PromptTokens: total / 2, CompletionTokens: total - total/2, TotalTokens: total}}
}

// global_rpm is one counter for the whole org: every key of the org counts
// against it, and another org's quota is its own.
func TestOrgWideRPMSharedAcrossKeys(t *testing.T) {
	p := newLimitsPlugin()
	ctx := context.Background()
	mk := func(org, key string) *models.RequestContext {
		rc := orgLimitRC(org, key, "m-chat")
		rc.Metadata["org_rate_limit_rpm"] = "2"
		return rc
	}
	wantAllowed(t, "org-a key1 #1", p.ProcessRequest(ctx, mk("org-a", "k1")))
	wantAllowed(t, "org-a key2 #1", p.ProcessRequest(ctx, mk("org-a", "k2")))
	wantRefused(t, "org-a key3 #1 (third request of the org)", p.ProcessRequest(ctx, mk("org-a", "k3")), "global_rpm")
	wantRefused(t, "org-a key1 #2", p.ProcessRequest(ctx, mk("org-a", "k1")), "global_rpm")
	// Another org's quota is its own.
	wantAllowed(t, "org-b key9 #1", p.ProcessRequest(ctx, mk("org-b", "k9")))
	wantAllowed(t, "org-b key9 #2", p.ProcessRequest(ctx, mk("org-b", "k9")))
}

// A request refused by one limit counts against none: the org-wide counter
// is not spent by a request the per-user limit refused.
func TestOrgWideRPMRefusedRequestConsumesNothing(t *testing.T) {
	p := newLimitsPlugin()
	ctx := context.Background()
	mk := func(user string) *models.RequestContext {
		rc := orgLimitRC("org-a", "k1", "m-chat")
		rc.Metadata["org_rate_limit_rpm"] = "2"
		rc.Metadata["org_rate_limit_per_user_rpm"] = "1"
		rc.UserID = user
		return rc
	}
	wantAllowed(t, "u1 #1", p.ProcessRequest(ctx, mk("u1")))
	wantRefused(t, "u1 #2", p.ProcessRequest(ctx, mk("u1")), "per_user_rpm")
	wantAllowed(t, "u2 #1 (the org has used 1 of 2)", p.ProcessRequest(ctx, mk("u2")))
	wantRefused(t, "u3 #1 (the org has used 2 of 2)", p.ProcessRequest(ctx, mk("u3")), "global_rpm")
}

// The org-wide request headers report the org's counter.
func TestOrgWideRPMHeaders(t *testing.T) {
	p := newLimitsPlugin()
	rc := orgLimitRC("org-a", "k1", "m-chat")
	rc.Metadata["org_rate_limit_rpm"] = "5"
	wantAllowed(t, "first", p.ProcessRequest(context.Background(), rc))
	if rc.Metadata["ratelimit_limit"] != "5" || rc.Metadata["ratelimit_remaining"] != "4" {
		t.Errorf("request headers: limit=%q remaining=%q, want 5 and 4", rc.Metadata["ratelimit_limit"], rc.Metadata["ratelimit_remaining"])
	}
	if r, err := strconv.Atoi(rc.Metadata["ratelimit_reset"]); err != nil || r < 1 || r > 60 {
		t.Errorf("ratelimit_reset = %q, want 1..60", rc.Metadata["ratelimit_reset"])
	}
}

// global_tpm: admitted while the org's tokens this minute are under the
// limit, the call's real usage counts once it finishes, and the next call
// from any key of the org is refused.
func TestOrgTPMSettlesOnUsage(t *testing.T) {
	p := newLimitsPlugin()
	ctx := context.Background()
	mk := func(org, key string) *models.RequestContext {
		rc := orgLimitRC(org, key, "m-chat")
		rc.Metadata["org_rate_limit_tpm"] = "1000"
		return rc
	}
	rc1 := mk("org-a", "k1")
	wantAllowed(t, "first call", p.ProcessRequest(ctx, rc1))
	rc1.Response = usage(2000)
	p.ProcessResponse(ctx, rc1)
	wantRefused(t, "second call, another key", p.ProcessRequest(ctx, mk("org-a", "k2")), "global_tpm")
	wantAllowed(t, "another org", p.ProcessRequest(ctx, mk("org-b", "k9")))
}

// The estimate is held while a call runs, so concurrent calls see it; a call
// that failed before any answer gives it back.
func TestOrgTPMReservesThenRefundsAFailedCall(t *testing.T) {
	p := newLimitsPlugin()
	ctx := context.Background()
	mk := func() *models.RequestContext {
		rc := orgLimitRC("org-a", "k1", "m-chat")
		rc.Metadata["org_rate_limit_tpm"] = "100"
		max := 500
		rc.Request.MaxTokens = &max
		return rc
	}
	rc1 := mk()
	wantAllowed(t, "first call (nothing counted yet)", p.ProcessRequest(ctx, rc1))
	wantRefused(t, "concurrent call while the first holds its estimate", p.ProcessRequest(ctx, mk()), "global_tpm")
	rc1.AddError(&models.APIError{Status: 502, Code: "upstream_error", Message: "boom"})
	p.ProcessResponse(ctx, rc1)
	wantAllowed(t, "after the failed call gave its estimate back", p.ProcessRequest(ctx, mk()))
}

// A stream that ended without usage (a BYOK stream the client left) keeps
// its estimate rather than counting nothing.
func TestOrgTPMStreamWithoutUsageKeepsEstimate(t *testing.T) {
	p := newLimitsPlugin()
	ctx := context.Background()
	mk := func() *models.RequestContext {
		rc := orgLimitRC("org-a", "k1", "m-chat")
		rc.Metadata["org_rate_limit_tpm"] = "100"
		rc.IsStream = true
		max := 500
		rc.Request.MaxTokens = &max
		return rc
	}
	rc1 := mk()
	wantAllowed(t, "stream", p.ProcessRequest(ctx, rc1))
	rc1.Response = &models.ChatCompletionResponse{} // finalizeStream: no usage
	p.ProcessResponse(ctx, rc1)
	wantRefused(t, "next call", p.ProcessRequest(ctx, mk()), "global_tpm")
}

// The token headers: the limit, what is left after the estimate, and after
// the call settles on its usage.
func TestOrgTPMHeaders(t *testing.T) {
	p := newLimitsPlugin()
	ctx := context.Background()
	rc := orgLimitRC("org-a", "k1", "m-chat")
	rc.Metadata["org_rate_limit_tpm"] = "10000"
	wantAllowed(t, "call", p.ProcessRequest(ctx, rc))
	h := map[string]string{}
	for _, kv := range rc.ResponseHeaders() {
		h[strings.ToLower(kv.Name)] = kv.Value
	}
	if h["x-ratelimit-limit-tokens"] != "10000" {
		t.Fatalf("x-ratelimit-limit-tokens = %q, want 10000 (headers %v)", h["x-ratelimit-limit-tokens"], h)
	}
	before, err := strconv.Atoi(h["x-ratelimit-remaining-tokens"])
	if err != nil || before >= 10000 || before <= 0 {
		t.Errorf("x-ratelimit-remaining-tokens = %q, want the limit less the estimate", h["x-ratelimit-remaining-tokens"])
	}
	if r, err := strconv.Atoi(h["x-ratelimit-reset-tokens"]); err != nil || r < 1 || r > 60 {
		t.Errorf("x-ratelimit-reset-tokens = %q, want 1..60", h["x-ratelimit-reset-tokens"])
	}
	rc.Response = usage(4000)
	p.ProcessResponse(ctx, rc)
	for _, kv := range rc.ResponseHeaders() {
		if strings.EqualFold(kv.Name, "x-ratelimit-remaining-tokens") && kv.Value != "6000" {
			t.Errorf("after 4000 tokens: x-ratelimit-remaining-tokens = %q, want 6000", kv.Value)
		}
	}
}

// per_key_tpm: one key's tokens never limit another key of the org.
func TestPerKeyTPM(t *testing.T) {
	p := newLimitsPlugin()
	ctx := context.Background()
	mk := func(key string) *models.RequestContext {
		rc := orgLimitRC("org-a", key, "m-chat")
		rc.Metadata["org_rate_limit_per_key_tpm"] = "1000"
		return rc
	}
	rc1 := mk("k1")
	wantAllowed(t, "k1 #1", p.ProcessRequest(ctx, rc1))
	rc1.Response = usage(2000)
	p.ProcessResponse(ctx, rc1)
	wantRefused(t, "k1 #2", p.ProcessRequest(ctx, mk("k1")), "per_key_tpm")
	wantAllowed(t, "k2 #1", p.ProcessRequest(ctx, mk("k2")))
}

// per_model_rpm: each model, across every key of the org. The handler stamps
// {"<the request's model>": rpm} before routing can rename the model, so the
// counter is the stamped model's.
func TestPerModelRPM(t *testing.T) {
	p := newLimitsPlugin()
	ctx := context.Background()
	mk := func(key, model, stamped string) *models.RequestContext {
		rc := orgLimitRC("org-a", key, model)
		rc.Metadata["org_rate_limit_per_model_rpm"] = `{"` + stamped + `":1}`
		return rc
	}
	wantAllowed(t, "m-chat via k1", p.ProcessRequest(ctx, mk("k1", "m-chat", "m-chat")))
	wantRefused(t, "m-chat via k2", p.ProcessRequest(ctx, mk("k2", "m-chat", "m-chat")), "per_model_rpm")
	wantAllowed(t, "m-other via k2", p.ProcessRequest(ctx, mk("k2", "m-other", "m-other")))
	// An alias the router renamed after the stamp still counts as the alias.
	wantAllowed(t, "alias-x #1", p.ProcessRequest(ctx, mk("k1", "routed-model", "alias-x")))
	wantRefused(t, "alias-x #2", p.ProcessRequest(ctx, mk("k2", "routed-model", "alias-x")), "per_model_rpm")
	// Another org's model counter is its own.
	rc := orgLimitRC("org-b", "k9", "m-chat")
	rc.Metadata["org_rate_limit_per_model_rpm"] = `{"m-chat":1}`
	wantAllowed(t, "org-b m-chat", p.ProcessRequest(ctx, rc))
}

// per_user_rpm keys on the request's user id: the agent context's user id,
// else the body's user; a request with neither is not limited by it.
func TestPerUserRPM(t *testing.T) {
	p := newLimitsPlugin()
	ctx := context.Background()
	mk := func(key, agentUser, bodyUser string) *models.RequestContext {
		rc := orgLimitRC("org-a", key, "m-chat")
		rc.Metadata["org_rate_limit_per_user_rpm"] = "1"
		rc.Agent.UserID = agentUser
		rc.UserID = bodyUser
		return rc
	}
	wantAllowed(t, "u1 (header) via k1", p.ProcessRequest(ctx, mk("k1", "u1", "")))
	wantRefused(t, "u1 (body user) via k2", p.ProcessRequest(ctx, mk("k2", "", "u1")), "per_user_rpm")
	wantAllowed(t, "u2", p.ProcessRequest(ctx, mk("k1", "u2", "")))
	// The agent context's user id wins over the body's user.
	wantAllowed(t, "agent u3 with body u1", p.ProcessRequest(ctx, mk("k1", "u3", "u1")))
	for i := 0; i < 3; i++ {
		wantAllowed(t, "no user id #"+strconv.Itoa(i+1), p.ProcessRequest(ctx, mk("k1", "", "")))
	}
}

// A user id over 64 bytes is hashed into the counter key: two long ids that
// share their first 64 bytes still get counters of their own.
func TestPerUserRPMLongUserIDs(t *testing.T) {
	p := newLimitsPlugin()
	ctx := context.Background()
	long := strings.Repeat("u", 300)
	mk := func(user string) *models.RequestContext {
		rc := orgLimitRC("org-a", "k1", "m-chat")
		rc.Metadata["org_rate_limit_per_user_rpm"] = "1"
		rc.UserID = user
		return rc
	}
	wantAllowed(t, "long-a #1", p.ProcessRequest(ctx, mk(long+"a")))
	wantRefused(t, "long-a #2", p.ProcessRequest(ctx, mk(long+"a")), "per_user_rpm")
	wantAllowed(t, "long-b #1", p.ProcessRequest(ctx, mk(long+"b")))
}

// An org limit comes only from the handler's stamp: a value the caller put
// in x-agentcc-metadata (recorded in CustomMetadataKeys) is never applied.
func TestOrgLimitNotCallerSettable(t *testing.T) {
	p := newLimitsPlugin()
	ctx := context.Background()
	for _, key := range []string{
		"org_rate_limit_rpm", "org_rate_limit_tpm", "org_rate_limit_per_key_rpm",
		"org_rate_limit_per_key_tpm", "org_rate_limit_per_user_rpm",
	} {
		for i := 0; i < 3; i++ {
			rc := orgLimitRC("org-c", "k-"+key, "m-chat")
			rc.UserID = "u1"
			rc.Metadata[key] = "1"
			rc.CustomMetadataKeys = append(rc.CustomMetadataKeys, key)
			if i == 0 {
				rc.Response = usage(5000)
			}
			wantAllowed(t, key+" set by the caller #"+strconv.Itoa(i+1), p.ProcessRequest(ctx, rc))
			p.ProcessResponse(ctx, rc)
		}
	}
	for i := 0; i < 3; i++ {
		rc := orgLimitRC("org-c", "k-model", "m-chat")
		rc.Metadata["org_rate_limit_per_model_rpm"] = `{"m-chat":1}`
		rc.CustomMetadataKeys = []string{"org_rate_limit_per_model_rpm"}
		wantAllowed(t, "per_model_rpm set by the caller #"+strconv.Itoa(i+1), p.ProcessRequest(ctx, rc))
	}
}

// The node switch turns every limit off (rate_limiting.enabled false).
func TestOrgWideRPMOffWithTheNodeSwitch(t *testing.T) {
	p := New(config.RateLimitConfig{Enabled: false}, nil, NewRateLimiter())
	for i := 0; i < 3; i++ {
		rc := orgLimitRC("org-a", "k1", "m-chat")
		rc.Metadata["org_rate_limit_rpm"] = "1"
		wantAllowed(t, "node switch off #"+strconv.Itoa(i+1), p.ProcessRequest(context.Background(), rc))
	}
}
