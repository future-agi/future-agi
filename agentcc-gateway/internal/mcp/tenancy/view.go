package tenancy

import (
	"sort"
	"sync/atomic"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// staticOrgView is what principals of mcp.static.org_id are served: their
// org's own pool plus the operator's static YAML pool. An org server shadows
// a YAML server of the same id. The org pool is set when the view is made
// (and on a reload that names another org), so it is never nil.
type staticOrgView struct {
	orgPool atomic.Pointer[Pool]
	static  *Pool
	cache   listCache
}

func (v *staticOrgView) org() *Pool { return v.orgPool.Load() }

func (v *staticOrgView) OrgID() string { return v.org().OrgID() }

func (v *staticOrgView) Servers() []mcp.PoolServer {
	out := v.org().Servers()
	seen := make(map[string]bool, len(out))
	for _, s := range out {
		seen[s.ID] = true
	}
	for _, s := range v.static.Servers() {
		if !seen[s.ID] {
			out = append(out, s)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}

func (v *staticOrgView) Server(id string) (mcp.PoolServer, bool) {
	if s, ok := v.org().Server(id); ok {
		return s, true
	}
	return v.static.Server(id)
}

func (v *staticOrgView) Client(serverID, credKey string, auth mcp.AuthOverride) (mcp.UpstreamClient, error) {
	if _, ok := v.org().Server(serverID); ok {
		return v.org().Client(serverID, credKey, auth)
	}
	return v.static.Client(serverID, credKey, auth)
}

func (v *staticOrgView) CachedList(key mcp.ListCacheKey, build func() []mcp.Tool) []mcp.Tool {
	return v.cache.get([2]uint64{v.org().gen.Load(), v.static.gen.Load()}, key, build)
}
