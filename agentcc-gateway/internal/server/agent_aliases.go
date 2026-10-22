package server

import (
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// agentAlias is another framework's own header for an agent field (a
// framework alias). An alias ranks below every canonical
// source, never returns 400 (an invalid value is ignored and named in
// metadata.agent_alias_ignored), and is on unless the org sets
// routing.agents.framework_aliases=false.
type agentAlias struct {
	header    string // lower case, as recorded in alias_overridden
	field     int    // the agent field it maps to, or aliasSession
	framework string // agent_framework when no canonical framework; "" for traceparent
}

// aliasSession is the session, which is not an AgentContext field index.
const aliasSession = -1

// agentAliases in precedence order: the session aliases first, in the order
// of the session precedence, so the first valid one is the session.
var agentAliases = [...]agentAlias{
	{"x-claude-code-session-id", aliasSession, "claude-code"},
	{"x-thread-id", aliasSession, "mastra"},
	{"x-a2a-context-id", aliasSession, "a2a"},
	{"x-resource-id", models.AgentFieldUserID, "mastra"},
	{"x-a2a-task-id", models.AgentFieldTaskID, "a2a"},
	{"traceparent", models.AgentFieldTraceID, ""},
}

const agentAliasCount = len(agentAliases)

// metaAliasIgnored names, comma-separated, the alias headers whose value was
// not a valid id.
const metaAliasIgnored = "agent_alias_ignored"

// aliasResult is what the alias headers contributed. framework is that of
// the first valid alias naming one, whether or not a canonical header
// overrode its value: it says which framework sent the call, and makes the
// identity source alias:<framework> when nothing ranked higher identified
// the agent. traceparent names no framework, so on its own it identifies no
// agent: an OpenTelemetry-instrumented client sends it on every call.
type aliasResult struct {
	session   string // the session an alias supplied
	framework string
}

// aliasesEnabled reports whether the org lets framework aliases count. It
// reads the org's base config: the session is the staged-rollout bucketing
// key (effectiveOrgConfig, step 4) and the aliases supply it, so the toggle
// cannot come from the variant the session picks.
func (h *Handlers) aliasesEnabled(rc *models.RequestContext) bool {
	_, orgCfg := h.resolveOrgConfig(rc)
	return orgCfg.AgentsConfig().FrameworkAliases
}

// applyAgentAliases maps the alias header values (indexed like agentAliases)
// onto a, for each field no canonical header set (origin), and records the
// fields it set there. sessionTaken reports whether a higher-ranked source
// (a runtime claim or a canonical session header) supplied the session.
func applyAgentAliases(rc *models.RequestContext, a *models.AgentContext, values *[agentAliasCount]string, origin *fieldOrigin, sessionTaken bool) aliasResult {
	var res aliasResult
	var ignored []string
	for i, al := range agentAliases {
		v := values[i]
		if v == "" {
			continue
		}
		mapped, ok := v, models.ValidAgentID(v)
		if al.field == models.AgentFieldTraceID {
			mapped, ok = traceIDFromTraceparent(v)
		}
		if !ok {
			ignored = append(ignored, al.header)
			continue
		}
		if res.framework == "" {
			res.framework = al.framework
		}
		switch {
		case al.field == aliasSession && (sessionTaken || res.session != ""):
			if sessionTaken {
				a.AliasOverridden = append(a.AliasOverridden, al.header)
			}
		case al.field == aliasSession:
			res.session = mapped
		case origin.kind[al.field] != originNone:
			a.AliasOverridden = append(a.AliasOverridden, al.header)
		default:
			_ = a.SetAgentField(al.field, mapped) // already validated
			origin.set(al.field, originAlias, al.header)
		}
	}
	if len(ignored) > 0 {
		rc.Metadata[metaAliasIgnored] = strings.Join(ignored, ",")
	}
	return res
}

// traceIDFromTraceparent returns the trace id of a W3C traceparent,
// version-traceid-parentid-flags in lower-case hex with neither id all zeros
// (version 00 is exactly 55 characters; a later version may append fields).
// The parent span id is the gateway span's parent, which telemetry
// reads from the header itself.
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
