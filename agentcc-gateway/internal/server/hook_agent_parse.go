package server

import (
	"net/http"
	"net/textproto"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// parseAgentContext is call-order step 3: it reads the agent headers, the
// body's metadata.agent or metadata.omega, the body's session_id and
// rc.Runtime's claims into rc.Agent, and validates them.
// It never changes the body and needs no effective org config, so
// effectiveOrgConfig can bucket on the session and run id. Keep observeHook
// as its first call.
//
// Precedence, highest first: runtime-token claims, x-agentcc-*, x-omega-*
// (the same field with a different value in both is 400
// agent_header_conflict), framework alias headers, then the body.
// applyAgentContext (step 7) does the org-dependent rest.
func (h *Handlers) parseAgentContext(rc *models.RequestContext, r *http.Request, req *models.ChatCompletionRequest) *models.APIError {
	h.observeHook(hookParseAgentContext, rc)
	var in agentInput
	in.scanHeaders(r.Header)
	in.dropInvalidGatewayTrace()
	if err := in.readBody(req); err != nil {
		return err
	}
	var a models.AgentContext
	var origin fieldOrigin
	if err := applyAgentValues(&a, &origin, &in.hdr, &agentHeaderNames, originHeader); err != nil {
		return err
	}
	session, err := in.headerSession(rc.Runtime)
	if err != nil {
		return err
	}
	var aliases aliasResult
	if in.anyAlias && h.aliasesEnabled(rc) {
		aliases = applyAgentAliases(rc, &a, &in.alias, &origin, session != "")
	}
	if err := applyAgentValues(&a, &origin, &in.body, &agentBodyNames, originBody); err != nil {
		return err
	}
	if rt := rc.Runtime; rt != nil {
		if err := applyRuntimeClaims(&a, &origin, rt, &in); err != nil {
			return err
		}
	}
	if session == "" {
		session = in.lowerSession(aliases.session)
	}
	a.Framework = in.framework(&a, &origin, rc.Runtime, aliases.framework)
	a.IdentitySource = in.identitySource(&origin, rc.Runtime, aliases.framework, a.Framework)
	if err := in.attribution(&a); err != nil {
		return err
	}
	a.SessionID = session
	rc.Agent = a
	if session != "" {
		rc.SessionID = session
	}
	return nil
}

// Slots each header namespace (and each body object) has beyond the agent
// fields.
const (
	slotLedgerID     = models.AgentFieldCount + iota // ledger-id, budget-scope's alias
	slotSession                                      // session-id
	slotOrgID                                        // org-id: checked against a runtime token only
	slotDeploymentID                                 // deployment-id: likewise
	nsSlots
)

// The two namespaces: x-agentcc-* and metadata.agent, x-omega-* and
// metadata.omega.
const (
	nsAgentcc = iota
	nsOmega
)

// Headers outside the two namespaces.
const (
	otherUser      = iota // x-agentcc-user: overrides the body user, logged as end_user
	otherTags             // x-agentcc-tags
	otherSessionID        // x-session-id, a generic session source
	otherReferer          // HTTP-Referer
	otherTitle            // X-Title
	otherORTitle          // X-OpenRouter-Title
	otherCount
)

var otherHeaderNames = [otherCount]string{"x-agentcc-user", "x-agentcc-tags", "x-session-id", "http-referer", "x-title", "x-openrouter-title"}

// Attribution limits, in characters (log columns end_user, app_referer and
// app_title).
const (
	maxEndUserLen  = 256
	maxRefererLen  = 256
	maxAppTitleLen = 128
)

func slotSuffix(slot int) string {
	switch slot {
	case slotLedgerID:
		return "ledger-id"
	case slotSession:
		return "session-id"
	case slotOrgID:
		return "org-id"
	case slotDeploymentID:
		return "deployment-id"
	}
	return models.AgentFieldSuffixes[slot]
}

// agentValues is what one channel sent for each slot, in its two namespaces.
type agentValues [2][nsSlots]string

// agentHeaderNames and agentBodyNames name each slot, for error params and
// for recording where a field came from.
var agentHeaderNames, agentBodyNames = func() (hdr, body [2][nsSlots]string) {
	for slot := 0; slot < nsSlots; slot++ {
		s := slotSuffix(slot)
		hdr[nsAgentcc][slot], hdr[nsOmega][slot] = "x-agentcc-"+s, "x-omega-"+s
		key := strings.ReplaceAll(s, "-", "_")
		body[nsAgentcc][slot], body[nsOmega][slot] = "metadata.agent."+key, "metadata.omega."+key
	}
	return hdr, body
}()

// headerSlot is where a header's value is kept.
type headerSlot struct {
	kind uint8 // headerNS, headerOther or headerAlias
	ns   uint8
	slot uint8
}

const (
	headerNS = iota
	headerOther
	headerAlias
)

// agentHeaderIndex maps the canonical form of every agent header the
// gateway reads to its slot, so the request's headers are read in one pass
// with no allocation.
var agentHeaderIndex = func() map[string]headerSlot {
	m := make(map[string]headerSlot, 2*nsSlots+otherCount+agentAliasCount)
	for ns := nsAgentcc; ns <= nsOmega; ns++ {
		for slot := 0; slot < nsSlots; slot++ {
			m[textproto.CanonicalMIMEHeaderKey(agentHeaderNames[ns][slot])] = headerSlot{headerNS, uint8(ns), uint8(slot)}
		}
	}
	for i, name := range otherHeaderNames {
		m[textproto.CanonicalMIMEHeaderKey(name)] = headerSlot{kind: headerOther, slot: uint8(i)}
	}
	for i, al := range agentAliases {
		m[textproto.CanonicalMIMEHeaderKey(al.header)] = headerSlot{kind: headerAlias, slot: uint8(i)}
	}
	return m
}()

// agentInput is everything the parse step read from the request.
type agentInput struct {
	hdr, body   agentValues
	other       [otherCount]string
	alias       [agentAliasCount]string
	anyAlias    bool
	omegaHeader bool    // an x-omega-* header was sent
	bodyNS      [2]bool // metadata.agent, metadata.omega was sent
	bodySession string  // the body's session_id
	metaSession string  // the body's metadata.session_id
}

// scanHeaders reads every agent, alias and attribution header. A header sent
// twice counts with its first value, as Header.Get reads it.
func (in *agentInput) scanHeaders(h http.Header) {
	for k, vs := range h {
		if len(vs) == 0 {
			continue
		}
		slot, ok := agentHeaderIndex[k]
		if !ok {
			if ck := textproto.CanonicalMIMEHeaderKey(k); ck != k {
				k = ck
				slot, ok = agentHeaderIndex[k]
			}
		}
		v := strings.TrimSpace(vs[0])
		if v == "" {
			continue
		}
		if !ok {
			if len(k) > len("X-Omega-") && strings.EqualFold(k[:len("X-Omega-")], "X-Omega-") {
				in.omegaHeader = true
			}
			continue
		}
		switch slot.kind {
		case headerNS:
			in.hdr[slot.ns][slot.slot] = v
			in.omegaHeader = in.omegaHeader || slot.ns == nsOmega
		case headerOther:
			in.other[slot.slot] = v
		case headerAlias:
			in.alias[slot.slot] = v
			in.anyAlias = true
		}
	}
}

// dropInvalidGatewayTrace forgets an x-agentcc-trace-id that is not a valid
// id before any precedence is worked out, so it can neither win nor conflict
// with x-omega-trace-id (see originGatewayTrace).
func (in *agentInput) dropInvalidGatewayTrace() {
	if v := in.hdr[nsAgentcc][models.AgentFieldTraceID]; v != "" && !models.ValidAgentID(v) {
		in.hdr[nsAgentcc][models.AgentFieldTraceID] = ""
	}
}

// Where a field's value came from. originGatewayTrace is x-agentcc-trace-id,
// which a caller may send as the gateway's own trace id
// (middleware.RequestID accepts any value): it ranks as a canonical header,
// but like an alias an invalid value is ignored, and on its own it
// identifies no agent.
const (
	originNone = iota
	originHeader
	originGatewayTrace
	originAlias
	originBody
)

// fieldOrigin records, per agent field, which channel set it and under what
// header or body key.
type fieldOrigin struct {
	kind [models.AgentFieldCount]uint8
	name [models.AgentFieldCount]string
}

func (o *fieldOrigin) set(field int, kind uint8, name string) {
	o.kind[field], o.name[field] = kind, name
}

func (o *fieldOrigin) any(kind uint8) bool {
	for _, k := range o.kind {
		if k == kind {
			return true
		}
	}
	return false
}

// slotRef is one slot of one namespace.
type slotRef struct{ ns, slot int }

// pickValue returns slot's value in values, looking first in the x-agentcc
// (metadata.agent) namespace, then in x-omega (metadata.omega), and for
// budget-scope also under its ledger-id alias. Two different values make a
// conflict, reported as the one found second.
func pickValue(values *agentValues, slot int) (value string, from, conflict slotRef, found, conflicted bool) {
	refs := [4]slotRef{{nsAgentcc, slot}, {nsOmega, slot}}
	n := 2
	if slot == models.AgentFieldBudgetScope {
		refs = [4]slotRef{{nsAgentcc, slot}, {nsAgentcc, slotLedgerID}, {nsOmega, slot}, {nsOmega, slotLedgerID}}
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
	return value, from, slotRef{}, found, false
}

// applyAgentValues sets each agent field one channel sent and no
// higher-ranked channel set, after checking its two namespaces agree and the
// value is valid.
func applyAgentValues(a *models.AgentContext, origin *fieldOrigin, values *agentValues, names *[2][nsSlots]string, kind uint8) *models.APIError {
	for field := 0; field < models.AgentFieldCount; field++ {
		v, from, conflict, found, conflicted := pickValue(values, field)
		if !found {
			continue
		}
		if conflicted {
			name := names[conflict.ns][conflict.slot]
			return agentHeaderConflict(name, name+" disagrees with "+names[from.ns][from.slot], nil)
		}
		if origin.kind[field] != originNone {
			continue
		}
		name := names[from.ns][from.slot]
		fieldKind := kind
		if kind == originHeader && field == models.AgentFieldTraceID && from.ns == nsAgentcc {
			fieldKind = originGatewayTrace // valid: dropInvalidGatewayTrace ran
		}
		if err := a.SetAgentField(field, v); err != nil {
			return agentHeaderInvalid(name, err.Reason)
		}
		origin.set(field, fieldKind, name)
		if field == models.AgentFieldStep {
			a.StepSource = stepSourceHeader
			if kind == originBody {
				a.StepSource = stepSourceBody
			}
		}
	}
	return nil
}

// framework is agent_framework: omega when any x-omega-* header was sent;
// otherwise x-agentcc-framework, the runtime claim's framework, the first
// valid alias's framework, omega for a metadata.omega body, and last
// metadata.agent.framework.
func (in *agentInput) framework(a *models.AgentContext, origin *fieldOrigin, rt *models.RuntimeIdentity, aliasFramework string) string {
	switch {
	case in.omegaHeader:
		return "omega"
	case origin.kind[models.AgentFieldFramework] == originHeader:
		return a.Framework
	case rt != nil && rt.Framework != "":
		return rt.Framework
	case aliasFramework != "":
		return aliasFramework
	case in.bodyNS[nsOmega]:
		return "omega"
	}
	return a.Framework
}

// identitySource is agent.identity_source: where the agent's identity came
// from, or "" when the request carried none (it is then not agent-tagged).
func (in *agentInput) identitySource(origin *fieldOrigin, rt *models.RuntimeIdentity, aliasFramework, framework string) string {
	switch {
	case rt != nil:
		return models.IdentitySourceRuntimeToken
	case in.omegaHeader || origin.any(originHeader):
		return models.IdentitySourceHeader
	case aliasFramework != "":
		return models.IdentitySourceAlias(aliasFramework)
	case origin.any(originBody) || (in.bodyNS[nsOmega] && framework == "omega"):
		return models.IdentitySourceBody
	}
	return ""
}

// attribution reads x-agentcc-user, x-agentcc-tags, HTTP-Referer and X-Title
// (or X-OpenRouter-Title). The referer and title are cut to their column
// widths; a longer user id is refused, since cutting it changes who it names.
func (in *agentInput) attribution(a *models.AgentContext) *models.APIError {
	if v := in.other[otherUser]; v != "" {
		if models.RuneCount(v) > maxEndUserLen {
			return agentHeaderInvalid(otherHeaderNames[otherUser], "must be at most 256 characters")
		}
		a.EndUser = v
	}
	if v := in.other[otherTags]; v != "" {
		tags, err := models.ParseAgentTags(v)
		if err != nil {
			return invalidTags(otherHeaderNames[otherTags], err.Error())
		}
		a.Tags = tags
	}
	a.AppReferer = models.TruncateRunes(in.other[otherReferer], maxRefererLen)
	title := in.other[otherTitle]
	if title == "" {
		title = in.other[otherORTitle]
	}
	a.AppTitle = models.TruncateRunes(title, maxAppTitleLen)
	return nil
}

// applyRuntimeClaims merges rc.Runtime's claims into a
// (models.AgentContext.ApplyRuntimeClaims), naming the header or body key
// that disagreed with a claim.
func applyRuntimeClaims(a *models.AgentContext, origin *fieldOrigin, rt *models.RuntimeIdentity, in *agentInput) *models.APIError {
	var sent [2]string // org-id, deployment-id
	var names [2]string
	for i, slot := range [2]int{slotOrgID, slotDeploymentID} {
		v, from, conflict, _, conflicted := pickValue(&in.hdr, slot)
		if conflicted {
			name := agentHeaderNames[conflict.ns][conflict.slot]
			return agentHeaderConflict(name, name+" disagrees with "+agentHeaderNames[from.ns][from.slot], nil)
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
	return agentHeaderConflict(param, msg, meta)
}
