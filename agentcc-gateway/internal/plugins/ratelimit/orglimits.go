package ratelimit

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"slices"
	"strconv"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tokenizer"
)

// The org's rate_limiting section reaches this plugin as metadata the handler
// stamps from the org config (applyOrgRateLimitOverrides). Each
// value is a decimal limit, except per_model_rpm: a JSON object of model ->
// requests per minute holding the request's model.
const (
	metaOrgRPM      = "org_rate_limit_rpm"
	metaOrgTPM      = "org_rate_limit_tpm"
	metaPerKeyRPM   = "org_rate_limit_per_key_rpm"
	metaPerKeyTPM   = "org_rate_limit_per_key_tpm"
	metaPerUserRPM  = "org_rate_limit_per_user_rpm"
	metaPerModelRPM = "org_rate_limit_per_model_rpm"

	// metaTPMHold carries a token reservation from ProcessRequest to
	// ProcessResponse. The ratelimit_ prefix is reserved: a caller's metadata
	// cannot set it.
	metaTPMHold = "ratelimit_tpm_hold"
)

// limitName is a limit as a refusal names it: the field that set it and what
// it counts.
type limitName struct{ field, what string }

var (
	limitKeyOwn    = limitName{"rate_limit_rpm", "this API key's own requests per minute"}
	limitPerKeyRPM = limitName{"per_key_rpm", "requests per minute for each API key of this organization"}
	limitNodeRPM   = limitName{"rate_limiting.global_rpm", "requests per minute for each API key on this gateway"}
	limitOrgRPM    = limitName{"global_rpm", "this organization's requests per minute, all keys together"}
	limitOrgTPM    = limitName{"global_tpm", "this organization's tokens per minute, all keys together"}
	limitPerKeyTPM = limitName{"per_key_tpm", "tokens per minute for each API key of this organization"}
	limitPerModel  = limitName{"per_model_rpm", "requests per minute for each model in this organization"}
	limitPerUser   = limitName{"per_user_rpm", "requests per minute for each user in this organization"}
)

// hasOrgLimits reports whether the handler stamped any of the org's own
// limits (per_key_rpm is the per-key limit's, read in ProcessRequest). It
// only reads the map, so an org without limits pays no allocation.
func hasOrgLimits(rc *models.RequestContext) bool {
	return rc.Metadata[metaOrgRPM] != "" || rc.Metadata[metaOrgTPM] != "" ||
		rc.Metadata[metaPerKeyTPM] != "" || rc.Metadata[metaPerUserRPM] != "" ||
		rc.Metadata[metaPerModelRPM] != ""
}

// stampedLimit is the positive limit under key, or 0. A value the caller sent
// in its own metadata (CustomMetadataKeys) is never a limit: the reserved
// org_ prefix keeps it out, and this check keeps it out as well.
func stampedLimit(rc *models.RequestContext, key string) int64 {
	v := rc.Metadata[key]
	if v == "" || callerSupplied(rc, key) {
		return 0
	}
	n, err := strconv.ParseInt(v, 10, 64)
	if err != nil || n <= 0 {
		return 0
	}
	return n
}

func callerSupplied(rc *models.RequestContext, key string) bool {
	return slices.Contains(rc.CustomMetadataKeys, key)
}

// requestOrgID is the org the handler resolved the request to.
func requestOrgID(rc *models.RequestContext) string {
	if org := rc.Metadata["org_id"]; org != "" {
		return org
	}
	return rc.Metadata["key_org_id"]
}

// requestUserID is the user a per-user limit counts: the agent context's user id
// (x-agentcc-user-id or metadata.agent.user_id), else the body's user
// (rc.UserID), else none. When the request names no user the auth plugin
// fills rc.UserID with the key's owner, the user budgets fall back to; that
// is not a user this limit counts, unless the body itself names it.
func requestUserID(rc *models.RequestContext) string {
	if rc.Agent.UserID != "" {
		return rc.Agent.UserID
	}
	u := rc.UserID
	if u != "" && u == rc.Metadata["auth_key_owner"] && !bodyNamesUser(rc, u) {
		return ""
	}
	return u
}

// bodyNamesUser reports whether the request body's own user field is u.
func bodyNamesUser(rc *models.RequestContext, u string) bool {
	switch {
	case rc.Request != nil:
		return rc.Request.User == u
	case rc.EmbeddingRequest != nil:
		return rc.EmbeddingRequest.User == u
	}
	return false
}

// stampedModelLimit is the per-model limit for this request: the stamped
// object's entry for the request's model, or its only entry. The handler
// stamps the model before routing may rename it (a model_map alias), so the
// stamped name is the one counted.
func stampedModelLimit(rc *models.RequestContext) (string, int64) {
	v := rc.Metadata[metaPerModelRPM]
	if v == "" || callerSupplied(rc, metaPerModelRPM) {
		return "", 0
	}
	var m map[string]int64
	if json.Unmarshal([]byte(v), &m) != nil {
		return "", 0
	}
	if n := m[rc.Model]; n > 0 {
		return rc.Model, n
	}
	if len(m) == 1 {
		for model, n := range m {
			if n > 0 {
				return model, n
			}
		}
	}
	return "", 0
}

// keyPart is a value as a part of a counter key: as it is when it is at most
// 64 bytes and holds none of ':', '{', '}', else "#" and its SHA-256 (128
// bits, hex). A raw part never starts with '#', so the two never meet, and no
// part holds ':', so a key names one (org, kind, value) only.
func keyPart(s string) string {
	if s != "" && len(s) <= 64 && !strings.ContainsAny(s, ":{}") && s[0] != '#' {
		return s
	}
	sum := sha256.Sum256([]byte(s))
	return "#" + hex.EncodeToString(sum[:16])
}

func orgCounter(org, kind string) string {
	return "org:" + keyPart(org) + ":" + kind
}

func orgCounterFor(org, kind, value, unit string) string {
	return "org:" + keyPart(org) + ":" + kind + ":" + keyPart(value) + ":" + unit
}

// tokenScope is which token counters a reservation was added to.
const (
	scopeOrgTPM = 1 << iota
	scopeKeyTPM
)

// processOrg checks the request against the key's RPM (keyRPM, from source)
// and every org limit the handler stamped, together: the request is counted
// against all of them or, when one refuses, against none.
func (p *Plugin) processOrg(rc *models.RequestContext, orgID, keyID, realKeyID string, keyRPM int64, source limitName) pipeline.PluginResult {
	var checkBuf [6]redisstate.LimitCheck
	var nameBuf [6]limitName
	var tokensBuf [6]bool
	checks, names, tokens := checkBuf[:0], nameBuf[:0], tokensBuf[:0]
	add := func(key string, limit, cost int64, name limitName, isTokens bool) {
		checks = append(checks, redisstate.LimitCheck{Key: key, Limit: limit, Cost: cost})
		names = append(names, name)
		tokens = append(tokens, isTokens)
	}

	if keyRPM > 0 {
		add(keyID, keyRPM, 1, source, false) // the key's counter, as Allow keeps it
	}
	if n := stampedLimit(rc, metaOrgRPM); n > 0 {
		add(orgCounter(orgID, "rpm"), n, 1, limitOrgRPM, false)
	}
	if model, n := stampedModelLimit(rc); n > 0 {
		add(orgCounterFor(orgID, "model", model, "rpm"), n, 1, limitPerModel, false)
	}
	if user := requestUserID(rc); user != "" {
		if n := stampedLimit(rc, metaPerUserRPM); n > 0 {
			add(orgCounterFor(orgID, "user", user, "rpm"), n, 1, limitPerUser, false)
		}
	}
	orgTPM := stampedLimit(rc, metaOrgTPM)
	var keyTPM int64
	if realKeyID != "" {
		keyTPM = stampedLimit(rc, metaPerKeyTPM)
	}
	var estimate int64
	scope := 0
	if orgTPM > 0 || keyTPM > 0 {
		estimate = estimateTokens(rc)
	}
	if orgTPM > 0 {
		add(orgCounter(orgID, "tpm"), orgTPM, estimate, limitOrgTPM, true)
		scope |= scopeOrgTPM
	}
	if keyTPM > 0 {
		add(orgCounterFor(orgID, "key", realKeyID, "tpm"), keyTPM, estimate, limitPerKeyTPM, true)
		scope |= scopeKeyTPM
	}
	if len(checks) == 0 {
		return pipeline.ResultContinue() // only per_user_rpm, and no user id
	}

	d := p.multi.AllowN(checks)
	if !d.Allowed {
		c := checks[d.Refused]
		return pipeline.ResultError(rateLimitExceeded(names[d.Refused], c.Limit, c.ResetSeconds))
	}

	// x-ratelimit-*-requests and -tokens report the tightest counter of each.
	req, tok := -1, -1
	for i := range checks {
		switch {
		case tokens[i] && (tok < 0 || checks[i].Remaining < checks[tok].Remaining):
			tok = i
		case !tokens[i] && (req < 0 || checks[i].Remaining < checks[req].Remaining):
			req = i
		}
	}
	if req >= 0 {
		rc.Metadata["ratelimit_limit"] = strconv.FormatInt(checks[req].Limit, 10)
		rc.Metadata["ratelimit_remaining"] = strconv.FormatInt(checks[req].Remaining, 10)
		rc.Metadata["ratelimit_reset"] = strconv.Itoa(checks[req].ResetSeconds)
	}
	if tok >= 0 {
		rc.SetResponseHeader("x-ratelimit-limit-tokens", strconv.FormatInt(checks[tok].Limit, 10))
		rc.SetResponseHeader("x-ratelimit-remaining-tokens", strconv.FormatInt(checks[tok].Remaining, 10))
		rc.SetResponseHeader("x-ratelimit-reset-tokens", strconv.Itoa(checks[tok].ResetSeconds))
		rc.Metadata[metaTPMHold] = tokenHold{
			estimate:  estimate,
			window:    d.Window,
			local:     d.Local,
			scope:     scope,
			remaining: checks[tok].Remaining,
		}.encode()
	}
	return pipeline.ResultContinue()
}

// estimateTokens is what a request is expected to use, held against its
// token limits while it runs: its messages at ~4 characters a token plus its
// max_completion_tokens or max_tokens, or its embedding input's length / 4.
// A request of another kind holds nothing and counts its usage when done.
func estimateTokens(rc *models.RequestContext) int64 {
	switch {
	case rc.Request != nil:
		n := int64(tokenizer.EstimateTokens(rc.Request.Messages))
		if m := rc.Request.MaxCompletionTokens; m != nil && *m > 0 {
			n += int64(*m)
		} else if m := rc.Request.MaxTokens; m != nil && *m > 0 {
			n += int64(*m)
		}
		return n
	case rc.EmbeddingRequest != nil:
		return int64(len(rc.EmbeddingRequest.Input)/4 + 1)
	}
	return 0
}

// tokenHold is a token reservation: what was held, where, and the tightest
// token counter's remaining tokens after it.
type tokenHold struct {
	estimate  int64
	window    int64
	local     bool
	scope     int
	remaining int64
}

func (h tokenHold) encode() string {
	local := "0"
	if h.local {
		local = "1"
	}
	return strconv.FormatInt(h.estimate, 10) + "|" + strconv.FormatInt(h.window, 10) + "|" + local + "|" +
		strconv.Itoa(h.scope) + "|" + strconv.FormatInt(h.remaining, 10)
}

func decodeTokenHold(s string) (tokenHold, bool) {
	f := strings.Split(s, "|")
	if len(f) != 5 {
		return tokenHold{}, false
	}
	est, err1 := strconv.ParseInt(f[0], 10, 64)
	win, err2 := strconv.ParseInt(f[1], 10, 64)
	scope, err3 := strconv.Atoi(f[3])
	rem, err4 := strconv.ParseInt(f[4], 10, 64)
	if err1 != nil || err2 != nil || err3 != nil || err4 != nil {
		return tokenHold{}, false
	}
	return tokenHold{estimate: est, window: win, local: f[2] == "1", scope: scope, remaining: rem}, true
}

// settleTokens replaces a request's token reservation with the tokens it
// used: the response's usage when it has one; nothing when the request failed
// with no answer at all (an upstream error, or a later plugin refused it);
// the estimate when it was answered without usage (a stream that ended
// before the upstream reported it). The hold is removed, so a request is
// settled once, before the parallel post-plugins read the metadata.
func (p *Plugin) settleTokens(rc *models.RequestContext) {
	raw := rc.Metadata[metaTPMHold]
	delete(rc.Metadata, metaTPMHold)
	h, ok := decodeTokenHold(raw)
	if !ok {
		return
	}
	delta := int64(0)
	if used, known := usedTokens(rc); known {
		delta = used - h.estimate
	} else if rc.Response == nil && rc.EmbeddingResponse == nil && len(rc.Errors) > 0 {
		delta = -h.estimate
	}
	if delta != 0 {
		orgID := requestOrgID(rc)
		var keys [2]string
		n := 0
		if h.scope&scopeOrgTPM != 0 {
			keys[n] = orgCounter(orgID, "tpm")
			n++
		}
		if h.scope&scopeKeyTPM != 0 {
			keys[n] = orgCounterFor(orgID, "key", rc.Metadata["auth_key_id"], "tpm")
			n++
		}
		p.multi.Settle(keys[:n], h.window, delta, h.local)
	}
	remaining := h.remaining - delta
	if remaining < 0 {
		remaining = 0
	}
	rc.SetResponseHeader("x-ratelimit-remaining-tokens", strconv.FormatInt(remaining, 10))
}

// usedTokens is the request's token usage, when its response reported one.
func usedTokens(rc *models.RequestContext) (int64, bool) {
	if rc.Response != nil && rc.Response.Usage != nil {
		u := rc.Response.Usage
		if u.TotalTokens > 0 {
			return int64(u.TotalTokens), true
		}
		return int64(u.PromptTokens + u.CompletionTokens), true
	}
	if rc.EmbeddingResponse != nil && rc.EmbeddingResponse.Usage != nil {
		u := rc.EmbeddingResponse.Usage
		if u.TotalTokens > 0 {
			return int64(u.TotalTokens), true
		}
		return int64(u.PromptTokens), true
	}
	return 0, false
}
