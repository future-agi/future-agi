package conformance

import (
	"encoding/json"
	"fmt"
	"slices"
)

// Keys are the gateway key ids a case's virtual servers admit: the posture
// key calls through the posture virtual server, the audit key lists through
// the audit one.
type Keys struct {
	Posture string `json:"posture"`
	Audit   string `json:"audit"`
}

// OrgConfigs is the org config the harness pushes for each org of the plan,
// keyed by org. An http case's org holds its server as add-from-catalog
// would push it (catalog_ref, url_variables, era and the credential's
// push-only secret) and its two virtual servers; the static org holds the
// virtual servers of every stdio case, whose servers are operator YAML.
// mocks maps a case's server id to its mock's base URL.
func OrgConfigs(plan *Plan, mocks map[string]string, keys map[string]Keys) (map[string]json.RawMessage, error) {
	type orgMCP struct {
		Enabled        bool           `json:"enabled"`
		Access         map[string]any `json:"access"`
		Servers        map[string]any `json:"servers,omitempty"`
		VirtualServers map[string]any `json:"virtual_servers"`
	}
	orgs := map[string]*orgMCP{}
	for _, cs := range plan.Cases {
		if cs.Skip != "" {
			continue
		}
		k, ok := keys[cs.ID]
		if !ok || k.Posture == "" || k.Audit == "" {
			return nil, fmt.Errorf("case %s: no posture and audit key ids", cs.ID)
		}
		o := orgs[cs.Org]
		if o == nil {
			o = &orgMCP{Enabled: true, Access: map[string]any{"default": "deny"}, VirtualServers: map[string]any{}}
			orgs[cs.Org] = o
		}
		if cs.Transport != "stdio" {
			base, ok := mocks[cs.ServerID]
			if !ok {
				return nil, fmt.Errorf("case %s: no mock named %s is running", cs.ID, cs.ServerID)
			}
			if o.Servers == nil {
				o.Servers = map[string]any{}
			}
			o.Servers[cs.ServerID] = cs.serverConfig(base)
		}
		o.VirtualServers[cs.Posture.Slug] = cs.postureServer(k.Posture)
		o.VirtualServers[cs.Audit.Slug] = map[string]any{
			"id": cs.Audit.ID, "members": []string{cs.ServerID},
			"access": map[string]any{"keys": []string{k.Audit}},
		}
	}
	out := make(map[string]json.RawMessage, len(orgs))
	for org, o := range orgs {
		raw, err := json.Marshal(map[string]any{"mcp": o})
		if err != nil {
			return nil, err
		}
		out[org] = raw
	}
	return out, nil
}

// serverConfig is the case's server as add-from-catalog would push it.
func (cs *Case) serverConfig(mockBase string) map[string]any {
	s := map[string]any{"url": mockBase + cs.Rest, "transport": cs.Transport, "catalog_ref": cs.Ref}
	if cs.PinEra != "" {
		s["era"] = cs.PinEra
	}
	if len(cs.URLVariables) > 0 {
		s["url_variables"] = cs.URLVariables
	}
	if cs.Credential.Secret != nil {
		s["credential"] = map[string]any{"mode": "shared", "secret": cs.Credential.Secret}
	}
	return s
}

// postureServer is the virtual server add-from-catalog would push for the
// case, with the access rule its postures translate to: the default
// posture auto lets every tool through but those the manifest holds at ask
// or deny; a default of ask or deny admits only the tools held at auto, or
// none at all. ask is held as deny until approvals exist (row posture.ask). A rule's
// empty allow list narrows nothing, so "none at all" is a deny of every tool.
func (cs *Case) postureServer(key string) map[string]any {
	var allow, deny []string
	for _, t := range cs.Tools {
		if t.Denied {
			deny = append(deny, cs.ServerID+"/"+t.Name)
		} else {
			allow = append(allow, cs.ServerID+"/"+t.Name)
		}
	}
	rule := map[string]any{"match": map[string]any{"key_ids": []string{key}}}
	if gatewayRefuses(cs.DefaultPosture) {
		if len(allow) > 0 {
			rule["allow"] = allow
		} else {
			deny = []string{cs.ServerID + "/*"}
		}
	}
	if len(deny) > 0 {
		slices.Sort(deny)
		rule["deny"] = deny
	}
	access := map[string]any{"keys": []string{key}}
	if len(rule) > 1 {
		access["rules"] = []any{rule}
	}
	vs := map[string]any{"id": cs.Posture.ID, "members": []string{cs.ServerID}, "access": access}
	if cs.EnvProbe != "" {
		vs["tools"] = map[string]any{"exclude": []string{cs.ServerID + "/" + envProbeTool}}
	}
	return vs
}
