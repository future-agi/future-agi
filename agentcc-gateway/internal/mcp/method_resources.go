package mcp

import (
	"encoding/json"
	"net/http"
	"sort"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/mcp/vserver"
)

// handleResourcesList serves resources/list through its chain (chain.go).
func (s *Server) handleResourcesList(w http.ResponseWriter, r *http.Request, msg *Message) {
	s.runListOrRead(w, r, msg, resourcesListChain, nil, s.writeResourcesList)
}

// handleResourcesRead serves resources/read through its chain (chain.go). A
// result handle is answered by Deps.ResultShaper in authorizeResource, so its
// chain has no upstream read.
func (s *Server) handleResourcesRead(w http.ResponseWriter, r *http.Request, msg *Message) {
	steps := resourcesReadChain
	var params ReadResourceParams
	if json.Unmarshal(msg.Params, &params) == nil && isResultHandle(params.URI) {
		steps = resultHandleReadChain
	}
	s.runListOrRead(w, r, msg, steps, prepareResourceRead, s.writeReadResource)
}

// resultHandleReadChain reads a result handle: the head of every chain, the
// access step that asks the shaper, and the result scan.
var resultHandleReadChain = withHead(
	chainStep{name: hookAuthorizeResource, run: authorizeResource},
	chainStep{name: hookScanResource, run: scanResource},
)

// prepareResourceRead reads the resources/read params into the call.
func prepareResourceRead(w http.ResponseWriter, cc *CallContext) bool {
	var params ReadResourceParams
	if err := json.Unmarshal(cc.msg.Params, &params); err != nil {
		writeJSONRPCError(w, cc.msg.ID, ErrCodeInvalidParams, "invalid resources/read params")
		return false
	}
	cc.legacy.resourceURI = params.URI
	return true
}

// handleResourceTemplatesList serves resources/templates/list: not yet
// supported, so the unknown-method answer, as before.
func (s *Server) handleResourceTemplatesList(w http.ResponseWriter, r *http.Request, msg *Message) {
	writeMethodNotFound(w, msg)
}

// handleResourcesSubscribe serves resources/subscribe and
// resources/unsubscribe: not yet supported.
func (s *Server) handleResourcesSubscribe(w http.ResponseWriter, r *http.Request, msg *Message) {
	writeMethodNotFound(w, msg)
}

// --- Resource names (M10) ---

const (
	// resourceScheme prefixes a virtual server's resource names:
	// mcp://<member>/<original uri>.
	resourceScheme = "mcp://"
	// resultHandlePrefix names a result spilled by Deps.ResultShaper (P11).
	resultHandlePrefix = "mcp://fagi/results/"
	// era2026 is the protocol revision whose resource-not-found code is
	// -32602; earlier ones use -32002 (cc.Era, set by M11).
	era2026                       = "2026-07-28"
	errCodeResourceNotFoundLegacy = -32002
)

func isResultHandle(uri string) bool { return strings.HasPrefix(uri, resultHandlePrefix) }

// resourceName is a member resource's name in a virtual server.
func resourceName(memberKey, uri string) string { return resourceScheme + memberKey + "/" + uri }

// resourceList is the view's resources the caller may read, each renamed,
// sorted by name.
func (v *callView) resourceList(cc *CallContext) []Resource {
	list := []Resource{}
	seen := map[string]bool{}
	for _, m := range v.members {
		for _, res := range v.source.MemberResources(m.Server) {
			name := resourceName(m.Key(), res.URI)
			if seen[name] || v.itemRefusal(cc, m, res.URI, name) != "" {
				continue
			}
			seen[name] = true
			res.URI, res.Raw = name, editRaw(res.Raw, map[string]string{"uri": name})
			list = append(list, res)
		}
	}
	sort.Slice(list, func(i, j int) bool { return list[i].URI < list[j].URI })
	return list
}

// resource resolves mcp://<member>/<uri> to its member and upstream resource;
// whether the caller may read it is authorizeResource's call (itemRefusal).
func (v *callView) resource(name string) (vserver.Member, Resource, bool) {
	rest, ok := strings.CutPrefix(name, resourceScheme)
	if !ok {
		return vserver.Member{}, Resource{}, false
	}
	key, uri, ok := strings.Cut(rest, "/")
	if !ok {
		return vserver.Member{}, Resource{}, false
	}
	m, ok := v.member(key)
	if !ok {
		return vserver.Member{}, Resource{}, false
	}
	for _, r := range v.source.MemberResources(m.Server) {
		if r.URI == uri {
			return m, r, true
		}
	}
	return vserver.Member{}, Resource{}, false
}
