package mcp

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// This regression deliberately uses only the baseline CallContext/Deps seam.
// On W4 the installed approval dependency is ignored and the mock executes.
type w5ListedApprovals struct{ checked int }

func (a *w5ListedApprovals) Check(cc *CallContext) *Denial {
	a.checked++
	vs, ok := cc.OrgConfig.MCPVirtualServer(cc.VirtualServer)
	if !ok || vs.Approval.Require != "listed" {
		return nil
	}
	return &Denial{Type: "permission_error", Code: "approval_required", Message: "Approval required", Metadata: map[string]any{"approval_id": "synthetic", "status": "pending", "approve_url": "/approvals/synthetic", "poll_url": "/mcp/approvals/synthetic", "expires_at": "2099-01-01T00:00:00Z"}}
}
func TestW5ApprovalRequiredNotExecuted(t *testing.T) {
	c := newChainServer(t)
	var org tenant.OrgConfig
	if err := json.Unmarshal([]byte(`{"mcp":{"virtual_servers":{"review":{"id":"vs-review","members":["mock"],"approval":{"require":"listed","tools":["mock/echo"]}}}}}`), &org); err != nil {
		t.Fatal(err)
	}
	a := &w5ListedApprovals{}
	deps := *c.Deps()
	deps.Approvals = a
	c.SetDeps(deps)
	dispatched := false
	c.hookObserver = func(h string, cc *CallContext) {
		if h == hookCheckApproval {
			cc.OrgConfig = &org
			cc.VirtualServer = "vs-review"
		}
		if h == hookCallUpstream {
			dispatched = true
		}
	}
	w := c.callTool(t)
	if w.Code != http.StatusOK || !strings.Contains(w.Body.String(), `"approval_required"`) || !strings.Contains(w.Body.String(), `"isError":true`) || dispatched || a.checked != 1 {
		t.Fatalf("listed tool was not held: status=%d checked=%d dispatch=%v body=%s", w.Code, a.checked, dispatched, w.Body.String())
	}
}
