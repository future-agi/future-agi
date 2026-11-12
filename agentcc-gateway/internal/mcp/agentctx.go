package mcp

import (
	"encoding/json"
	"net/http"
	"strconv"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Agent context on MCP requests.
//
// The section 6.2 header table applies to MCP HTTP requests, and
// params._meta["io.futureagi/agent"] carries the same fields per request in
// snake_case. Precedence, highest first: runtime-token claims, x-agentcc-*,
// x-omega-* (the same field with a different value in both is 400
// agent_header_conflict), framework alias headers, then _meta. Headers always
// win over _meta. The generation id comes from x-agentcc-generation-id, else
// _meta["io.futureagi/generation_id"]; the traceparent from its header, else
// _meta.traceparent.
//
// The agent-context parse step on the model path (internal/server/hook_agent_parse.go,
// agent_aliases.go) is unexported in a package that imports this one, so this
// mirrors its precedence on top of the same exported models helpers
// (AgentFieldBySuffix, AgentFieldByKey, SetAgentField, ApplyRuntimeClaims,
// ValidAgentID): a value is accepted or refused alike on both paths. Two
// differences: the framework aliases are always on (the org's
// routing.agents.framework_aliases switch is in the org config, which hook 3
// resolves after this step), and there is one _meta object, not
// metadata.agent and metadata.omega.

// Inbound names the agent context is read from.
const (
	agentHeaderPrefix = "x-agentcc-"
	omegaHeaderPrefix = "x-omega-"
	// HeaderGenerationID names the model call that emitted a tool call.
	HeaderGenerationID   = "x-agentcc-generation-id"
	headerSessionGeneric = "x-session-id"
	headerTraceparent    = "traceparent"

	// MetaKeyAgent and MetaKeyGenerationID are the request _meta keys of the
	// agent context and the generation id.
	MetaKeyAgent        = "io.futureagi/agent"
	MetaKeyGenerationID = "io.futureagi/generation_id"
	metaKeyTraceparent  = "traceparent"
	// metaKeyGatewayPrefix is the gateway's _meta namespace: no inbound key in
	// it reaches an upstream.
	metaKeyGatewayPrefix = "io.futureagi/"

	errAgentHeaderInvalid  = "agent_header_invalid"
	errAgentHeaderConflict = "agent_header_conflict"
	stepSourceHeader       = "header"
	stepSourceBody         = "body"
	maxAgentSessionLen     = 255
)

// Slots each namespace has beyond the agent fields.
const (
	agentSlotLedgerID     = models.AgentFieldCount + iota // ledger-id, budget-scope's alias
	agentSlotSession                                      // session-id
	agentSlotOrgID                                        // org-id: checked against a runtime token only
	agentSlotDeploymentID                                 // deployment-id: likewise
	agentSlotCount
)

// The two header namespaces. _meta has one, kept in the first.
const (
	agentNSAgentcc = iota
	agentNSOmega
)

func agentSlotSuffix(slot int) string {
	switch slot {
	case agentSlotLedgerID:
		return "ledger-id"
	case agentSlotSession:
		return "session-id"
	case agentSlotOrgID:
		return "org-id"
	case agentSlotDeploymentID:
		return "deployment-id"
	}
	return models.AgentFieldSuffixes[slot]
}

// agentValues is what one channel sent for each slot, per namespace.
type agentValues [2][agentSlotCount]string

// agentHeaderNames and agentMetaNames name each slot, for error params.
var agentHeaderNames, agentMetaNames = func() (hdr, meta [2][agentSlotCount]string) {
	for slot := 0; slot < agentSlotCount; slot++ {
		s := agentSlotSuffix(slot)
		hdr[agentNSAgentcc][slot], hdr[agentNSOmega][slot] = agentHeaderPrefix+s, omegaHeaderPrefix+s
		meta[agentNSAgentcc][slot] = "_meta." + MetaKeyAgent + "." + strings.ReplaceAll(s, "-", "_")
	}
	return hdr, meta
}()

// agentSlotRef is one slot of one namespace.
type agentSlotRef struct{ ns, slot int }

// agentHeaderIndex maps every lower-case agent header to its slot.
var agentHeaderIndex = func() map[string]agentSlotRef {
	m := make(map[string]agentSlotRef, 2*agentSlotCount)
	for ns := agentNSAgentcc; ns <= agentNSOmega; ns++ {
		for slot := 0; slot < agentSlotCount; slot++ {
			m[agentHeaderNames[ns][slot]] = agentSlotRef{ns, slot}
		}
	}
	return m
}()

// agentAlias is another framework's own header for an agent field (plan
// section 6.2, framework aliases). An alias ranks below every canonical
// header and never refuses: an invalid value is ignored.
type agentAlias struct {
	header    string
	field     int    // the agent field, or agentAliasSession
	framework string // agent_framework when no canonical framework; "" for traceparent
}

const agentAliasSession = -1

// agentAliases in the agent-context precedence order: the session aliases first, so the
// first valid one is the session.
var agentAliases = [...]agentAlias{
	{"x-claude-code-session-id", agentAliasSession, "claude-code"},
	{"x-thread-id", agentAliasSession, "mastra"},
	{"x-a2a-context-id", agentAliasSession, "a2a"},
	{"x-resource-id", models.AgentFieldUserID, "mastra"},
	{"x-a2a-task-id", models.AgentFieldTaskID, "a2a"},
	{headerTraceparent, models.AgentFieldTraceID, ""},
}

var agentAliasIndex = func() map[string]int {
	m := make(map[string]int, len(agentAliases))
	for i, al := range agentAliases {
		m[al.header] = i
	}
	return m
}()

// agentInput is everything hook 2 reads from the request.
type agentInput struct {
	hdr         agentValues
	meta        agentValues // _meta["io.futureagi/agent"], in the first namespace
	alias       [len(agentAliases)]string
	omegaHeader bool // a non-empty x-omega-* header was sent

	sessionGeneric   string
	generationHeader string
	generationMeta   string
	generationMetaOK bool // _meta's generation id was sent as a string
	generationMetaIn bool // _meta held a generation id at all
	traceparentMeta  string
}

// readAgentInput reads the agent headers of r and the _meta of msg's params.
// A malformed io.futureagi/* _meta value is 400 agent_header_invalid.
func readAgentInput(r *http.Request, msg *Message) (*agentInput, *Denial) {
	in := &agentInput{}
	if r != nil {
		in.scanHeaders(r.Header)
	}
	if msg != nil {
		if d := in.readMeta(msg.Params); d != nil {
			return in, d
		}
	}
	return in, nil
}

// scanHeaders reads every agent, alias and generation header. A header sent
// twice counts with its first value, as Header.Get reads it.
func (in *agentInput) scanHeaders(h http.Header) {
	for k, vs := range h {
		if len(vs) == 0 {
			continue
		}
		v := strings.TrimSpace(vs[0])
		if v == "" {
			continue
		}
		name := strings.ToLower(k)
		if strings.HasPrefix(name, omegaHeaderPrefix) {
			in.omegaHeader = true
		}
		if ref, ok := agentHeaderIndex[name]; ok {
			in.hdr[ref.ns][ref.slot] = v
			continue
		}
		if i, ok := agentAliasIndex[name]; ok {
			in.alias[i] = v
			continue
		}
		switch name {
		case HeaderGenerationID:
			in.generationHeader = v
		case headerSessionGeneric:
			in.sessionGeneric = v
		}
	}
}

// readMeta reads params._meta: the io.futureagi/agent object (snake_case
// agent fields; a known field must be a string or a number, require also an
// array of strings), the generation id and the traceparent. A _meta that is
// absent or not an object carries no agent context.
func (in *agentInput) readMeta(params json.RawMessage) *Denial {
	meta := paramsMeta(params)
	if meta == nil {
		return nil
	}
	if raw, ok := meta[MetaKeyAgent]; ok && !isAgentJSONNull(raw) {
		var fields map[string]json.RawMessage
		if json.Unmarshal(raw, &fields) != nil || fields == nil {
			return agentInvalid("_meta."+MetaKeyAgent, "must be an object of agent fields")
		}
		for k, v := range fields {
			slot, ok := metaAgentSlot(k)
			if !ok {
				continue // an unknown key is the caller's own, and harmless
			}
			s, ok := agentJSONScalar(v)
			if !ok && slot == models.AgentFieldRequire {
				s, ok = agentJSONStringList(v)
			}
			if !ok {
				return agentInvalid(agentMetaNames[agentNSAgentcc][slot], "must be a string or a number")
			}
			in.meta[agentNSAgentcc][slot] = s
		}
	}
	if raw, ok := meta[MetaKeyGenerationID]; ok && !isAgentJSONNull(raw) {
		in.generationMetaIn = true
		in.generationMeta, in.generationMetaOK = agentJSONString(raw)
	}
	if raw, ok := meta[metaKeyTraceparent]; ok {
		in.traceparentMeta, _ = agentJSONString(raw)
	}
	return nil
}

// metaAgentSlot is the slot a _meta agent key names; ledger_id is kept apart
// from budget_scope so the two can be checked against each other.
func metaAgentSlot(key string) (int, bool) {
	if key == "ledger_id" {
		return agentSlotLedgerID, true
	}
	return models.AgentFieldByKey(key)
}

// agentResult is what hook 2 resolved.
type agentResult struct {
	agent        models.AgentContext
	generationID string
	traceParent  string
}

// Where a field's value came from. agentOriginGatewayTrace is
// x-agentcc-trace-id, which callers sent as the gateway's own trace id before
// agent context existed: it ranks as a canonical header, but an invalid value
// is ignored and on its own it identifies no agent (the agent-context rule).
const (
	agentOriginNone = iota
	agentOriginHeader
	agentOriginGatewayTrace
	agentOriginAlias
	agentOriginMeta
)

// agentOrigin records, per agent field, which channel set it and the header
// or key it came under.
type agentOrigin struct {
	kind [models.AgentFieldCount]uint8
	name [models.AgentFieldCount]string
}

func (o *agentOrigin) any(kind uint8) bool {
	for _, k := range o.kind {
		if k == kind {
			return true
		}
	}
	return false
}

// resolve applies the sources in precedence order and returns the agent
// context, or the 400 that refuses the request.
func (in *agentInput) resolve(rt *models.RuntimeIdentity) (agentResult, *Denial) {
	var a models.AgentContext
	var origin agentOrigin
	if v := in.hdr[agentNSAgentcc][models.AgentFieldTraceID]; v != "" && !models.ValidAgentID(v) {
		in.hdr[agentNSAgentcc][models.AgentFieldTraceID] = ""
	}
	if d := applyAgentValues(&a, &origin, &in.hdr, &agentHeaderNames, agentOriginHeader); d != nil {
		return agentResult{}, d
	}
	session, d := in.headerSession(rt)
	if d != nil {
		return agentResult{}, d
	}
	aliasSession, aliasFramework := in.applyAliases(&a, &origin, session != "")
	if d := applyAgentValues(&a, &origin, &in.meta, &agentMetaNames, agentOriginMeta); d != nil {
		return agentResult{}, d
	}
	traceParent := in.traceParent()
	if a.TraceID == "" && traceParent != "" {
		a.TraceID, _ = traceIDFromTraceparent(traceParent)
	}
	if rt != nil {
		if d := in.applyRuntimeClaims(&a, &origin, rt); d != nil {
			return agentResult{}, d
		}
	}
	if session == "" {
		session = firstSession(aliasSession, in.sessionGeneric)
	}
	a.Framework = in.framework(&a, &origin, rt, aliasFramework)
	a.IdentitySource = in.identitySource(&origin, rt, aliasFramework)
	a.SessionID = session
	gen, d := in.generationID()
	if d != nil {
		return agentResult{}, d
	}
	return agentResult{agent: a, generationID: gen, traceParent: traceParent}, nil
}

// pickValue returns slot's value in values, looking first in the x-agentcc
// namespace, then in x-omega, and for budget-scope also under its ledger-id
// alias. Two different values make a conflict, reported as the one found
// second.
func pickValue(values *agentValues, slot int) (value string, from, conflict agentSlotRef, found, conflicted bool) {
	refs := [4]agentSlotRef{{agentNSAgentcc, slot}, {agentNSOmega, slot}}
	n := 2
	if slot == models.AgentFieldBudgetScope {
		refs = [4]agentSlotRef{{agentNSAgentcc, slot}, {agentNSAgentcc, agentSlotLedgerID}, {agentNSOmega, slot}, {agentNSOmega, agentSlotLedgerID}}
		n = 4
	}
	for _, ref := range refs[:n] {
		v := values[ref.ns][ref.slot]
		switch {
		case v == "":
		case !found:
			value, from, found = v, ref, true
		case v != value:
			return value, from, ref, true, true
		}
	}
	return value, from, agentSlotRef{}, found, false
}

// applyAgentValues sets each agent field one channel sent and no
// higher-ranked channel set, after checking its namespaces agree and the
// value is valid.
func applyAgentValues(a *models.AgentContext, origin *agentOrigin, values *agentValues, names *[2][agentSlotCount]string, kind uint8) *Denial {
	for field := 0; field < models.AgentFieldCount; field++ {
		v, from, conflict, found, conflicted := pickValue(values, field)
		if !found {
			continue
		}
		if conflicted {
			name := names[conflict.ns][conflict.slot]
			return agentConflict(name, name+" disagrees with "+names[from.ns][from.slot], nil)
		}
		if origin.kind[field] != agentOriginNone {
			continue
		}
		name := names[from.ns][from.slot]
		if err := a.SetAgentField(field, v); err != nil {
			return agentInvalid(name, err.Reason)
		}
		fieldKind := kind
		if kind == agentOriginHeader && field == models.AgentFieldTraceID && from.ns == agentNSAgentcc {
			fieldKind = agentOriginGatewayTrace
		}
		origin.kind[field], origin.name[field] = fieldKind, name
		if field == models.AgentFieldStep {
			a.StepSource = stepSourceHeader
			if kind == agentOriginMeta {
				a.StepSource = stepSourceBody
			}
		}
	}
	return nil
}

// headerSession is the session from the runtime claim or a canonical session
// header, the sources above the aliases.
func (in *agentInput) headerSession(rt *models.RuntimeIdentity) (string, *Denial) {
	v, from, conflict, found, conflicted := pickValue(&in.hdr, agentSlotSession)
	if conflicted {
		name := agentHeaderNames[conflict.ns][conflict.slot]
		return "", agentConflict(name, name+" disagrees with "+agentHeaderNames[from.ns][from.slot], nil)
	}
	if found && len(v) > maxAgentSessionLen {
		return "", agentInvalid(agentHeaderNames[from.ns][from.slot], "must be at most 255 bytes")
	}
	if rt != nil && rt.SessionID != "" {
		return rt.SessionID, nil
	}
	return v, nil
}

// applyAliases maps the alias headers onto the fields no canonical header set
// and returns the session and framework they supplied. framework is that of
// the first valid alias naming one, whether or not a canonical header
// overrode its value; traceparent names none.
func (in *agentInput) applyAliases(a *models.AgentContext, origin *agentOrigin, sessionTaken bool) (session, framework string) {
	for i, al := range agentAliases {
		v := in.alias[i]
		if v == "" {
			continue
		}
		mapped, ok := v, models.ValidAgentID(v)
		if al.field == models.AgentFieldTraceID {
			mapped, ok = traceIDFromTraceparent(v)
		}
		if !ok {
			continue
		}
		if framework == "" {
			framework = al.framework
		}
		switch {
		case al.field == agentAliasSession && (sessionTaken || session != ""):
			if sessionTaken {
				a.AliasOverridden = append(a.AliasOverridden, al.header)
			}
		case al.field == agentAliasSession:
			session = mapped
		case origin.kind[al.field] != agentOriginNone:
			a.AliasOverridden = append(a.AliasOverridden, al.header)
		default:
			_ = a.SetAgentField(al.field, mapped) // already validated
			origin.kind[al.field], origin.name[al.field] = agentOriginAlias, al.header
		}
	}
	return session, framework
}

// applyRuntimeClaims merges a runtime token's claims into a
// (models.AgentContext.ApplyRuntimeClaims), naming the header or key that
// disagreed with a claim.
func (in *agentInput) applyRuntimeClaims(a *models.AgentContext, origin *agentOrigin, rt *models.RuntimeIdentity) *Denial {
	var sent, names [2]string // org-id, deployment-id
	for i, slot := range [2]int{agentSlotOrgID, agentSlotDeploymentID} {
		v, from, conflict, _, conflicted := pickValue(&in.hdr, slot)
		if conflicted {
			name := agentHeaderNames[conflict.ns][conflict.slot]
			return agentConflict(name, name+" disagrees with "+agentHeaderNames[from.ns][from.slot], nil)
		}
		sent[i], names[i] = v, agentHeaderNames[from.ns][from.slot]
	}
	ce := a.ApplyRuntimeClaims(rt, sent[0], sent[1])
	if ce == nil {
		return nil
	}
	param := origin.name[models.AgentFieldAgentID]
	switch ce.Field {
	case "org_id":
		param = names[0]
	case "deployment_id":
		param = names[1]
	case "run_id":
		param = origin.name[models.AgentFieldRunID]
	}
	meta := map[string]string{"source": models.IdentitySourceRuntimeToken, "field": ce.Field}
	msg := param + " disagrees with the runtime token's " + ce.Field
	if ce.Reason != "" {
		meta["reason"] = ce.Reason
		msg = param + " names a run outside the runtime token's root run " + rt.RootRunID
	}
	return agentConflict(param, msg, meta)
}

// framework is agent_framework: omega when any x-omega-* header was sent;
// otherwise x-agentcc-framework, the runtime claim's framework, the first
// valid alias's framework, and last the _meta framework.
func (in *agentInput) framework(a *models.AgentContext, origin *agentOrigin, rt *models.RuntimeIdentity, aliasFramework string) string {
	switch {
	case in.omegaHeader:
		return "omega"
	case origin.kind[models.AgentFieldFramework] == agentOriginHeader:
		return a.Framework
	case rt != nil && rt.Framework != "":
		return rt.Framework
	case aliasFramework != "":
		return aliasFramework
	}
	return a.Framework
}

// identitySource is agent.identity_source: where the agent's identity came
// from, or "" when the request carried none. _meta is the request body's.
func (in *agentInput) identitySource(origin *agentOrigin, rt *models.RuntimeIdentity, aliasFramework string) string {
	switch {
	case rt != nil:
		return models.IdentitySourceRuntimeToken
	case in.omegaHeader || origin.any(agentOriginHeader):
		return models.IdentitySourceHeader
	case aliasFramework != "":
		return models.IdentitySourceAlias(aliasFramework)
	case origin.any(agentOriginMeta):
		return models.IdentitySourceBody
	}
	return ""
}

// generationID is the model call that emitted the tool call: the header, else
// the _meta key. It is checked as an id and never trusted for money.
func (in *agentInput) generationID() (string, *Denial) {
	if v := in.generationHeader; v != "" {
		if !models.ValidAgentID(v) {
			return "", agentInvalid(HeaderGenerationID, "must be 1 to 128 characters of A-Z a-z 0-9 . _ : @ / -")
		}
		return v, nil
	}
	if !in.generationMetaIn {
		return "", nil
	}
	if !in.generationMetaOK || !models.ValidAgentID(in.generationMeta) {
		return "", agentInvalid("_meta."+MetaKeyGenerationID, "must be a string of 1 to 128 characters of A-Z a-z 0-9 . _ : @ / -")
	}
	return in.generationMeta, nil
}

// traceParent is a valid W3C traceparent from the header, else from _meta;
// an invalid one is ignored, as an alias is.
func (in *agentInput) traceParent() string {
	for _, v := range [2]string{in.alias[agentAliasIndex[headerTraceparent]], strings.TrimSpace(in.traceparentMeta)} {
		if _, ok := traceIDFromTraceparent(v); ok {
			return v
		}
	}
	return ""
}

// firstSession is the session from the sources below the canonical headers:
// an alias, then x-session-id. A generic source that is too long is not a
// session.
func firstSession(alias, generic string) string {
	for _, s := range [2]string{alias, generic} {
		if s != "" && len(s) <= maxAgentSessionLen {
			return s
		}
	}
	return ""
}

// traceIDFromTraceparent returns the trace id of a W3C traceparent,
// version-traceid-parentid-flags in lower-case hex with neither id all zeros
// (version 00 is exactly 55 characters; a later version may append fields).
func traceIDFromTraceparent(v string) (string, bool) {
	const n = 55 // 2 + 1 + 32 + 1 + 16 + 1 + 2
	if len(v) < n || v[2] != '-' || v[35] != '-' || v[52] != '-' {
		return "", false
	}
	version, traceID, spanID, flags := v[0:2], v[3:35], v[36:52], v[53:55]
	if !lowerHex(version) || version == "ff" || !lowerHex(traceID) || !lowerHex(spanID) || !lowerHex(flags) {
		return "", false
	}
	if (version == "00" && len(v) != n) || (len(v) > n && v[n] != '-') {
		return "", false
	}
	if strings.Trim(traceID, "0") == "" || strings.Trim(spanID, "0") == "" {
		return "", false
	}
	return traceID, true
}

func lowerHex(s string) bool {
	for i := 0; i < len(s); i++ {
		if c := s[i]; !('0' <= c && c <= '9' || 'a' <= c && c <= 'f') {
			return false
		}
	}
	return true
}

// --- JSON values ---

// paramsMeta is params' _meta object, or nil when params or _meta is not an
// object.
func paramsMeta(params json.RawMessage) map[string]json.RawMessage {
	var p map[string]json.RawMessage
	if len(params) == 0 || json.Unmarshal(params, &p) != nil {
		return nil
	}
	var meta map[string]json.RawMessage
	if raw, ok := p["_meta"]; !ok || json.Unmarshal(raw, &meta) != nil {
		return nil
	}
	return meta
}

func isAgentJSONNull(raw json.RawMessage) bool {
	return strings.TrimSpace(string(raw)) == "null"
}

// agentJSONString is a JSON string's trimmed value; false for anything else.
func agentJSONString(raw json.RawMessage) (string, bool) {
	var s string
	if json.Unmarshal(raw, &s) != nil {
		return "", false
	}
	return strings.TrimSpace(s), true
}

// agentJSONScalar is a JSON string's trimmed value, or a JSON number written as a
// plain decimal (an integer exactly as sent, anything else without an
// exponent), so it is checked by the same rules as a header (the agent-context rule).
func agentJSONScalar(raw json.RawMessage) (string, bool) {
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

// agentJSONStringList is a JSON array of strings as the comma list a header
// carries, so a list field reads the same from either channel.
func agentJSONStringList(raw json.RawMessage) (string, bool) {
	var list []string
	if json.Unmarshal(raw, &list) != nil {
		return "", false
	}
	return strings.Join(list, ","), true
}

// --- Refusals ---

// agentInvalid is 400 agent_header_invalid for one header or _meta key.
func agentInvalid(param, reason string) *Denial {
	return agentDenial(errAgentHeaderInvalid, param, param+" "+reason, nil)
}

// agentConflict is 400 agent_header_conflict: one field sent twice with
// different values, or against a runtime-token claim (meta).
func agentConflict(param, message string, meta map[string]string) *Denial {
	return agentDenial(errAgentHeaderConflict, param, message, meta)
}

// agentDenial is a protocol-level 400: JSON-RPC -32602 whose data carries the
// code, the header or key, and any metadata.
func agentDenial(code, param, message string, meta map[string]string) *Denial {
	data := map[string]any{"code": code, "param": param}
	d := &Denial{Type: models.ErrTypeInvalidRequest, Code: code, Message: message,
		RPCCode: ErrCodeInvalidParams, HTTPStatus: http.StatusBadRequest}
	if meta != nil {
		data["metadata"] = meta
		d.Metadata = meta
	}
	d.Data, _ = json.Marshal(data)
	return d
}
