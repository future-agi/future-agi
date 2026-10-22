package server

import (
	"encoding/json"
	"math"
	"net/http"
	"slices"
	"strconv"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/capability"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// applyCallerMetadata records the caller's own dimensions on the request
// context, from both channels that carry them: the x-agentcc-metadata header
// and the OpenAI-spec `metadata` body field. Either one is the gateway's
// equivalent of calling span.SetAttribute() in a natively instrumented service.
//
// The header wins on conflict. It is set by the calling infrastructure, and
// infrastructure tagging should not be overridable by the payload it forwards.
//
// body is the request body's own metadata object, or nil for endpoints whose
// body has no such field.
func applyCallerMetadata(rc *models.RequestContext, r *http.Request, body json.RawMessage) {
	if len(body) > 0 {
		mergeCallerMetadata(rc, body)
	}
	if meta := r.Header.Get("x-agentcc-metadata"); meta != "" {
		mergeCallerMetadata(rc, json.RawMessage(meta))
	}
}

// parseMetadataHeader records the dimensions carried by the x-agentcc-metadata
// header alone, for endpoints that read the header before their body exists.
func parseMetadataHeader(meta string, rc *models.RequestContext) {
	mergeCallerMetadata(rc, json.RawMessage(meta))
}

// applyCallerExtras snapshots the request body's unknown top-level fields onto
// the request context, so telemetry can export what the caller actually sent.
//
// These are not exotic: ChatCompletionRequest.UnmarshalJSON matches against a
// fixed list of ~23 field names, so every OpenAI parameter added since —
// reasoning_effort, parallel_tool_calls, store, prediction — arrives here too,
// alongside whatever an SDK's extra_body carried.
//
// Taken at parse time on purpose. The translation layer writes its own state
// into the same Extra map on the canonical request, and reading it later would
// export gateway internals as if the caller had sent them. A snapshot taken
// before any of that runs excludes them by provenance, with no list of our own
// key names to keep correct.
//
// Scalars only. A nested object or array cannot be exported as a queryable
// attribute anyway, and objects are where credentials live when someone passes
// service-account JSON through extra_body.
func applyCallerExtras(rc *models.RequestContext, extra map[string]json.RawMessage) {
	for k, raw := range extra {
		// metadata is the curated dimension channel and has already been read.
		if k == "metadata" {
			continue
		}
		recordCallerExtra(rc, k, raw)
	}
}

// applyCallerExtrasFromBody is applyCallerExtras for dialects the gateway does
// not unmarshal into a canonical struct — the Anthropic and Gemini endpoints.
//
// It reads the raw body instead of a parsed request because both of those
// handlers have a native pass-through path that forwards the caller's bytes
// untouched; there is no struct to hang unknown fields off, and adding one
// would still miss the pass-through. `known` is the dialect's own field set.
func applyCallerExtrasFromBody(rc *models.RequestContext, body []byte, known map[string]struct{}) {
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(body, &raw); err != nil {
		return
	}
	for k, v := range raw {
		if _, isSpec := known[k]; isSpec {
			continue
		}
		recordCallerExtra(rc, k, v)
	}
}

// recordCallerExtra vets one caller-supplied body field and stores it.
func recordCallerExtra(rc *models.RequestContext, key string, raw json.RawMessage) {
	if isCredentialShapedKey(key) {
		rc.CallerExtrasDropped++
		return
	}
	v, ok := decodeScalar(raw)
	if !ok {
		rc.CallerExtrasDropped++
		return
	}
	if rc.CallerExtras == nil {
		rc.CallerExtras = make(map[string]any, 8)
	}
	rc.CallerExtras[key] = v
}

// decodeScalar returns a JSON string, number or bool as its Go value. Anything
// else — object, array, null — is not a scalar and is reported as such.
//
// Numbers keep their type rather than becoming strings: an integer exported as
// an int lands in the platform's numeric attribute column, where a range filter
// works. That is worth more than matching how metadata stringifies everything.
func decodeScalar(raw json.RawMessage) (any, bool) {
	var v any
	if err := json.Unmarshal(raw, &v); err != nil {
		return nil, false
	}
	switch t := v.(type) {
	case string, bool:
		return t, true
	case float64:
		// json.Unmarshal makes every number a float64. Integral values go back
		// to int64 so they render as 3 rather than 3.0 in a trace UI.
		if t == math.Trunc(t) && math.Abs(t) < 1<<53 {
			return int64(t), true
		}
		return t, true
	default:
		return nil, false
	}
}

// isCredentialShapedKey is the last line of defence over scalar-only filtering
// and value redaction, not the only one. Over-blocking here costs an absent
// attribute; under-blocking costs a leaked secret, so the match is deliberately
// broad.
func isCredentialShapedKey(key string) bool {
	lower := strings.ToLower(key)
	for _, needle := range []string{
		"secret", "token", "credential", "password", "passwd",
		"api_key", "apikey", "private_key", "bearer", "signature",
		// Not a bare "auth": that also swallows author and authority, which
		// are ordinary body fields. Separator-less forms like authtoken are
		// caught by "token" above.
		"authorization", "auth_", "auth-",
	} {
		if strings.Contains(lower, needle) {
			return true
		}
	}
	return false
}

// mergeCallerMetadata merges one JSON object of caller dimensions into the
// request context. Security-sensitive keys are blocked to prevent client-side
// injection, and accepted keys are recorded on the context so telemetry can
// tell them apart from the metadata plugins write themselves.
//
// Values are decoded leniently: a JSON string is unquoted, anything else keeps
// its JSON text, so one numeric value never costs the caller its other
// dimensions.
func mergeCallerMetadata(rc *models.RequestContext, raw json.RawMessage) {
	var m map[string]json.RawMessage
	if err := json.Unmarshal(raw, &m); err != nil {
		return
	}
	for k, rv := range m {
		if isBlockedMetadataKey(k) || isAgentContextObject(k, rv) {
			continue
		}
		var v string
		if err := json.Unmarshal(rv, &v); err != nil {
			v = string(rv)
		}
		// Dedup against the accepted list, not against rc.Metadata: that map
		// also holds internal keys the plugins wrote, and a caller key that
		// collides with one still has to be recorded as caller-supplied.
		if !slices.Contains(rc.CustomMetadataKeys, k) {
			rc.CustomMetadataKeys = append(rc.CustomMetadataKeys, k)
		}
		rc.Metadata[k] = v
	}
}

// isBlockedMetadataKey reports whether a caller's x-agentcc-metadata header
// or body metadata may not set key, in any letter case. A caller's value for
// such a key is dropped, never refused: the request still runs.
//
// Request metadata is one map shared by the caller's dimensions and the
// gateway's own billing, cost, telemetry, routing and identity keys. Every
// name the gateway itself writes into or reads out of it is the gateway's
// (reservedMetadataPrefixes, reservedMetadataKeys), unless
// callerSettableMetadataKeys allows it, and every other name is the
// caller's. TestEveryGatewayMetadataKeyIsReservedOrCallerSettable holds that
// complete against the gateway's source: a new gateway key that is neither
// reserved nor allowed fails it.
func isBlockedMetadataKey(key string) bool {
	lower := strings.ToLower(key)
	if _, ok := callerSettableMetadataKeys[lower]; ok {
		return false
	}
	for _, prefix := range reservedMetadataPrefixes {
		if strings.HasPrefix(lower, prefix) {
			return true
		}
	}
	_, reserved := reservedMetadataKeys[lower]
	return reserved
}

// reservedMetadataPrefixes are the families the gateway owns whole.
//
// The agent prefixes (agent_, omega_, run_, step, ledger_, budget_) keep a
// caller from spoofing the agent dimensions, which come only from the typed
// agent context (parseAgentContext).
//
// "rbac_" is the RBAC plugin's: only the gateway sets rbac_team and rbac_role.
//
// "route_reason." is the routing evidence the router records
// (routing.RouteReason*).
var reservedMetadataPrefixes = []string{
	"auth_", "key_", "org_", "budget_", "ratelimit_",
	"cache_", "guardrail_", "credit_", "credits_", "rbac_",
	"auto_", "managed_",
	"agent_", "omega_", "run_", "step", "ledger_",
	"route_reason.",
}

// reservedMetadataKeys are the gateway's names outside those families, in
// lower case.
//
// Exact names, not wider prefixes: the plugins own cost and cost_source,
// while a prefix wide enough to cover them would also swallow cost_center and
// cost_code, ordinary business dimensions; the same holds for model_,
// response_, routing_ and the rest.
var reservedMetadataKeys = setOf(
	// Identity, credentials and policy.
	"authorization", "client_ip", "access_groups", "access_group_matched",
	"x-guardrail-policy", // the X-Guardrail-Policy header's value; the header is its only channel
	"answer_unavailable", // why no output check can read the answer
	"request_unavailable",
	"a2a_task_id", "a2a_context_id",
	"timeout_ms",

	// Cost and billing. audio_seconds and input_characters are the units the
	// cost plugin prices a transcription, translation or speech request on;
	// images_generated and quality are what the platform prices an image
	// entry on when the gateway reported no cost; model_alias selects the
	// operator's alias cost factor.
	"cost", "cost_source", "cost_base", "cost_markup_percent",
	"billing_mode", "usage_estimated",
	"billing_alert", // an unpriced managed hold
	"audio_seconds", "input_characters", "images_generated", "quality", "model_alias",
	models.OCRPagesMetadataKey,          // the OCR page count the cost plugin prices
	models.RerankSearchUnitsMetadataKey, // the upstream's billed rerank units
	models.CountedTokensMetadataKey,     // a token count's count, never the request's usage
	usageUnreadableMetadataKey,          // why a request was priced on an estimate
	"input_tokens", "output_tokens", "prompt_tokens", "completion_tokens",
	"response_content_type", "response_size_bytes", // a speech answer's evidence
	models.ResponseStatusMetadataKey, // the status a passed-through answer was logged with

	// Routing evidence; several come back as x-agentcc-* response headers.
	metaModelAliasRequested,                        // the gateway's record of an omega alias (applyAgentContext)
	metaRoutingOptions, metaUpstreamProviderObject, // gateway-written routing evidence
	metaSourceChoice, // the auto pool's managed source choice
	"routing_strategy", "routing_strategy_applied", "routing_rule",
	"provider_locked", "provider_lock_target", "race_winner",
	"complexity_tier", "complexity_score", "complexity_source", "complexity_provider",
	"complexity_override", "complexity_original_model",
	"fallback_model", "fallback_provider", "failover_attempts", "failover_original_provider",
	"original_model", "original_provider", "model_map_matched", "model_map_provider",
	"model_deprecated", "model_max_output_tokens", "input_token_warning",

	// Telemetry.
	"outcome_class", // the gateway's outcome class
	"endpoint_type", "endpoint_format", "translation_drops",
	"privacy_mode", "privacy_redacted", // the request's redaction mode comes from the org and the gateway, never the caller
	"tools_requested", "tools_filtered",
	"assistants_operation", "vector_stores_operation",
	"edge_cache", "edge_cache_control", "edge_cache_etag", "edge_cache_vary",
	capability.MetaNoPerfDisclosure,
)

// callerSettableMetadataKeys are the names the gateway also writes or reads
// that stay the caller's, each with why that is safe. None of them may fall
// in a reserved family (TestCallerSettableMetadataKeysAreOutsideReservedFamilies).
var callerSettableMetadataKeys = map[string]string{
	// Caller requests the gateway reads by design; no plugin writes them.
	"provider_lock":     "the x-agentcc-provider-lock header's metadata form, checked against provider_lock's allow and deny lists like the header",
	"if_none_match":     "the edge cache's conditional request: a 304 only for the caller's own matching ETag",
	"edge_cache_opt_in": "the caller opting its own response into the edge cache, which cache.edge.require_opt_in asks for",
	// Ordinary caller dimensions the gateway writes only on one path, after
	// intake and unconditionally there, so its own value always wins.
	"customer_id":  "written by the auth plugin (priority 20, after intake) from the license claims under license auth",
	"license_id":   "written by the auth plugin (priority 20, after intake) from the license claims under license auth",
	"instance_id":  "written by the auth plugin (priority 20, after intake) from the license claims under license auth",
	"thread_id":    "written by the Assistants handlers from the URL after setupAssistantsRC",
	"message_id":   "written by the Assistants handlers from the URL after setupAssistantsRC",
	"assistant_id": "written by the Assistants handlers from the URL after setupAssistantsRC",
	// The speech relay's session evidence: written only by the relay
	// (handlers_speech.go), which reads no caller metadata, so on a relayed
	// session no caller value is there; elsewhere they are the caller's own.
	"voice":        "written only by the speech relay, which takes no caller metadata, when the session names a voice",
	"close_reason": "written only by the speech relay, which takes no caller metadata, when the session ends",
}

// setOf is a lookup set of keys, lower-cased.
func setOf(keys ...string) map[string]struct{} {
	s := make(map[string]struct{}, len(keys))
	for _, k := range keys {
		s[strings.ToLower(k)] = struct{}{}
	}
	return s
}

// isAgentContextObject reports whether a caller metadata entry is the body's
// agent-context channel: an object under metadata.agent or metadata.omega,
// which parseAgentContext types into rc.Agent, so it is never also kept as a
// caller dimension. Any other value under those keys ("agent":"support-bot")
// is the caller's own metadata.
func isAgentContextObject(key string, raw json.RawMessage) bool {
	return (key == bodyAgentKey || key == bodyOmegaKey) && isJSONObject(raw)
}

// isJSONObject reports whether raw, a value from a parsed JSON document, is
// an object. null is not: it unmarshals into a nil map without an error.
func isJSONObject(raw json.RawMessage) bool {
	for _, c := range raw {
		switch c {
		case ' ', '\t', '\n', '\r':
			continue
		}
		return c == '{'
	}
	return false
}

// extractBodySessionID extracts session_id from the request body's "metadata"
// field (which lands in Extra for chat completion requests).
func extractBodySessionID(extra map[string]json.RawMessage) string {
	if extra == nil {
		return ""
	}
	raw, ok := extra["metadata"]
	if !ok {
		return ""
	}
	var meta map[string]interface{}
	if err := json.Unmarshal(raw, &meta); err != nil {
		return ""
	}
	if sid, ok := meta["session_id"].(string); ok && sid != "" {
		return sid
	}
	return ""
}

// The body's agent-context objects: metadata.agent, and Omega's
// metadata.omega, with snake_case keys.
const (
	bodyAgentKey = "agent"
	bodyOmegaKey = "omega"
)

// readBody reads the agent fields and session sources of the body: its
// session_id, and from its metadata object the session_id and the agent
// objects. It changes nothing (applyRoutingOptions strips session_id later, step 6;
// applyAgentContext removes the agent objects, step 7). Only an object under
// metadata.agent or metadata.omega is agent context: any other value there
// is the caller's own metadata and stays as it was sent. In an agent object,
// a known field that is not a string or a number (or, for require, an array
// of strings) is 400 agent_header_invalid.
func (in *agentInput) readBody(req *models.ChatCompletionRequest) *models.APIError {
	if req == nil || len(req.Extra) == 0 {
		return nil
	}
	if raw, ok := req.Extra["session_id"]; ok {
		in.bodySession = jsonString(raw)
	}
	raw, ok := req.Extra["metadata"]
	if !ok {
		return nil
	}
	var meta map[string]json.RawMessage
	if json.Unmarshal(raw, &meta) != nil {
		return nil // not an object: it carries no agent context
	}
	if v, ok := meta["session_id"]; ok {
		in.metaSession = jsonString(v)
	}
	for ns, key := range [2]string{bodyAgentKey, bodyOmegaKey} {
		obj, ok := meta[key]
		if !ok || !isJSONObject(obj) {
			continue // absent, or the caller's own value: no agent context
		}
		var fields map[string]json.RawMessage
		if json.Unmarshal(obj, &fields) != nil {
			continue
		}
		in.bodyNS[ns] = true
		for k, v := range fields {
			slot, ok := bodyAgentSlot(k)
			if !ok {
				continue // an unknown key is the caller's own, and harmless
			}
			s, ok := jsonScalar(v)
			if !ok && slot == models.AgentFieldRequire {
				if s, ok = jsonStringList(v); !ok {
					return agentHeaderInvalid(agentBodyNames[ns][slot], "must be a comma-separated string or an array of strings")
				}
			}
			if !ok {
				return agentHeaderInvalid(agentBodyNames[ns][slot], "must be a string or a number")
			}
			in.body[ns][slot] = s
		}
	}
	return nil
}

// bodyAgentSlot is the slot a body agent key names; ledger_id is kept apart
// from budget_scope so the two can be checked against each other.
func bodyAgentSlot(key string) (int, bool) {
	if key == "ledger_id" {
		return slotLedgerID, true
	}
	return models.AgentFieldByKey(key)
}

// jsonString is a JSON string's trimmed value, or "" for anything else.
func jsonString(raw json.RawMessage) string {
	var s string
	if json.Unmarshal(raw, &s) != nil {
		return ""
	}
	return strings.TrimSpace(s)
}

// jsonScalar is a JSON string's trimmed value, or a JSON number written as a
// plain decimal (an integer exactly as sent, anything else without an
// exponent, since JavaScript writes 1e-7), so it is checked by the same
// rules as a header.
func jsonScalar(raw json.RawMessage) (string, bool) {
	var v any
	if json.Unmarshal(raw, &v) != nil {
		return "", false
	}
	switch t := v.(type) {
	case string:
		return strings.TrimSpace(t), true
	case float64:
		if lit := strings.TrimSpace(string(raw)); !strings.ContainsAny(lit, ".eE") {
			return lit, true
		}
		return strconv.FormatFloat(t, 'f', -1, 64), true
	}
	return "", false
}

// jsonStringList is a JSON array of strings as the comma list a header
// carries, so a list field reads the same from either channel.
func jsonStringList(raw json.RawMessage) (string, bool) {
	var list []string
	if json.Unmarshal(raw, &list) != nil {
		return "", false
	}
	return strings.Join(list, ","), true
}

// headerSession is the session from the runtime claim or a canonical session
// header, the sources above the aliases.
func (in *agentInput) headerSession(rt *models.RuntimeIdentity) (string, *models.APIError) {
	v, from, conflict, found, conflicted := pickValue(&in.hdr, slotSession)
	if conflicted {
		name := agentHeaderNames[conflict.ns][conflict.slot]
		return "", agentHeaderConflict(name, name+" disagrees with "+agentHeaderNames[from.ns][from.slot], nil)
	}
	if found && len(v) > maxSessionIDLen {
		return "", agentHeaderInvalid(agentHeaderNames[from.ns][from.slot], "must be at most 255 bytes")
	}
	if rt != nil && rt.SessionID != "" {
		return rt.SessionID, nil
	}
	return v, nil
}

// lowerSession is the session from the sources below the canonical headers:
// an alias, then x-session-id, the body's session_id and metadata.session_id.
// A generic source that is too long is not a session.
func (in *agentInput) lowerSession(alias string) string {
	for _, s := range [...]string{alias, in.other[otherSessionID], in.bodySession, in.metaSession} {
		if s != "" && len(s) <= maxSessionIDLen {
			return s
		}
	}
	return ""
}

// stripAgentMetadata removes the body's agent objects from its metadata, so
// they never reach an upstream; the caller's other metadata stays, including
// a non-object value under agent or omega. A metadata object left empty is
// removed.
func stripAgentMetadata(req *models.ChatCompletionRequest) {
	raw, ok := req.Extra["metadata"]
	if !ok {
		return
	}
	var meta map[string]json.RawMessage
	if json.Unmarshal(raw, &meta) != nil {
		return
	}
	stripped := false
	for _, key := range [2]string{bodyAgentKey, bodyOmegaKey} {
		if isJSONObject(meta[key]) {
			delete(meta, key)
			stripped = true
		}
	}
	if !stripped {
		return
	}
	if len(meta) == 0 {
		delete(req.Extra, "metadata")
		return
	}
	if b, err := json.Marshal(meta); err == nil {
		req.Extra["metadata"] = b
	}
}
