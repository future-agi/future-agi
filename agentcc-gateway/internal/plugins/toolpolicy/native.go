package toolpolicy

import (
	"bytes"
	"context"
	"encoding/json"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"github.com/futureagi/agentcc-gateway/internal/toolview"
	"sort"
	"strconv"
)

type effectivePolicy struct {
	enabled                              bool
	orgEnabled                           bool
	orgAllow, orgDeny, keyAllow, keyDeny policyNames
	orgAction                            string
	orgMax                               int
	action                               string
	max                                  int
}

// effective is shared with canonical chat: in particular key lists alone do
// not enable a disabled global/org policy, and disabled global max still caps
// an enabled org policy exactly as it did before native support.
func (p *Plugin) effective(rc *models.RequestContext) effectivePolicy {
	e := effectivePolicy{action: p.defaultAction, max: p.maxToolsPerRequest}
	if p.tenantStore != nil && rc.Metadata[tenant.MetadataKeyOrgID] != "" {
		if cfg := p.tenantStore.Get(rc.Metadata[tenant.MetadataKeyOrgID]); cfg != nil && cfg.ToolPolicy != nil && cfg.ToolPolicy.Enabled {
			o := cfg.ToolPolicy
			e.orgEnabled = true
			e.orgAction = o.DefaultAction
			e.orgMax = o.MaxToolsPerRequest
			e.orgAllow = newPolicyNames(o.Allow)
			e.orgDeny = newPolicyNames(o.Deny)
		}
	}
	e.enabled = p.enabled || e.orgEnabled
	if e.orgAction != "" {
		e.action = e.orgAction
	}
	if e.orgMax > 0 && (e.max == 0 || e.orgMax < e.max) {
		e.max = e.orgMax
	}
	e.keyAllow = parseCSV(rc.Metadata["auth_allowed_tools"])
	e.keyDeny = parseCSV(rc.Metadata["auth_denied_tools"])
	return e
}
func (p *Plugin) denied(e effectivePolicy, ref toolRef) bool {
	return (p.enabled && p.deny.has(ref)) || e.orgDeny.has(ref) || e.keyDeny.has(ref) || (p.enabled && !p.allow.empty() && !p.allow.has(ref)) || (!e.orgAllow.empty() && !e.orgAllow.has(ref)) || (!e.keyAllow.empty() && !e.keyAllow.has(ref))
}
func policyList(p policyNames) []string {
	out := make([]string, 0, len(p.exact))
	for k := range p.exact {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
func (p *Plugin) policyBytes(e effectivePolicy) []byte {
	// Private copied values, never digests or metadata. Encoding cannot fail for
	// this closed scalar/list shape and sorts all originally unordered sets.
	out, _ := json.Marshal(struct {
		Global, Enabled bool
		Action          string
		Max             int
		Lists           [][]string
	}{p.enabled, e.enabled, e.action, e.max, [][]string{policyList(p.allow), policyList(p.deny), policyList(e.orgAllow), policyList(e.orgDeny), policyList(e.keyAllow), policyList(e.keyDeny)}})
	return out
}

type nativePolicyState struct {
	body        []byte
	policy      []byte
	snapshot    toolview.Snapshot
	keep        []toolview.ToolID
	checked     bool
	requirement models.ComputerUseRequirement
	owner       *toolview.NativeRequest
	generation  uint64
}

func nativePolicyError() *models.APIError {
	return models.ErrBadRequest("native_tool_policy_unavailable", "native tool policy cannot be enforced")
}

// PrepareNative runs before a native quote at 40 and verifies the same prepared
// body and policy at 60. No mutation or re-quote is allowed on the second call.
func (p *Plugin) PrepareNative(ctx context.Context, rc *models.RequestContext) *models.APIError {
	if rc == nil || ctx.Err() != nil {
		return nativePolicyError()
	}
	previous, prepared := p.native.Load(rc)
	if rc.Request != nil {
		if prepared {
			return nativePolicyError()
		}
		return nil
	}
	view, ok := rc.PassThrough.(toolview.NativeToolRequest)
	if !ok {
		if prepared || rc.Routing.ComputerUse.Required {
			return nativePolicyError()
		}
		return nil
	}
	e := p.effective(rc)
	policy := p.policyBytes(e)
	if prepared {
		state := previous.(*nativePolicyState)
		if state.owner != nil {
			current, ok := view.(*toolview.NativeRequest)
			if !ok || current != state.owner || current.Generation() != state.generation {
				return nativePolicyError()
			}
		}
		if !bytes.Equal(state.body, view.Body()) || !bytes.Equal(state.policy, policy) || state.requirement != rc.Routing.ComputerUse {
			return nativePolicyError()
		}
		if state.checked && view.ApplyToolFilter(ctx, state.snapshot, state.keep) != nil {
			return nativePolicyError()
		}
		return nil
	}
	required := rc.Routing.ComputerUse
	if required.Required {
		validator, ok := view.(interface {
			ValidateComputerUse(context.Context, models.ComputerUseRequirement) error
		})
		if !ok || validator.ValidateComputerUse(ctx, required) != nil {
			return nativePolicyError()
		}
	}
	state := &nativePolicyState{policy: policy, requirement: required}
	if e.enabled || required.Required {
		snapshot, err := view.ToolSnapshot(ctx)
		if err != nil {
			return nativePolicyError()
		}
		if e.enabled && e.max > 0 && snapshot.Len() > e.max {
			return models.ErrBadRequest("too_many_tools", "native tool count exceeds policy")
		}
		keep := make([]toolview.ToolID, 0, snapshot.Len())
		for i := 0; i < snapshot.Len(); i++ {
			d, _ := snapshot.Tool(i)
			if d.Kind() == toolview.Unknown {
				return nativePolicyError()
			}
			ref := toolRef{name: d.PolicyName(), builtin: d.Kind() != toolview.Function}
			if e.enabled && p.denied(e, ref) {
				if e.action == "reject" || required.Required {
					return models.ErrForbidden("native tool is not allowed by policy")
				}
				continue
			}
			keep = append(keep, d.ID())
		}
		if view.ApplyToolFilter(ctx, snapshot, keep) != nil {
			return nativePolicyError()
		}
		prepared, err := view.ToolSnapshot(ctx)
		if err != nil {
			return nativePolicyError()
		}
		state.snapshot = prepared
		state.checked = true
		for i := 0; i < prepared.Len(); i++ {
			d, _ := prepared.Tool(i)
			state.keep = append(state.keep, d.ID())
		}
		rc.Metadata["tools_requested"] = strconv.Itoa(snapshot.Len())
		if removed := snapshot.Len() - prepared.Len(); removed > 0 {
			rc.Metadata["tools_filtered"] = strconv.Itoa(removed)
		}
	}
	if current, ok := view.(*toolview.NativeRequest); ok {
		state.owner = current
		state.generation = current.Generation()
	}
	state.body = bytes.Clone(view.Body())
	p.native.Store(rc, state)
	rc.AddFinalizer(func() { p.native.Delete(rc) })
	return nil
}
