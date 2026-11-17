package mcp

import (
	"context"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/contracts/schematest"
	"github.com/futureagi/agentcc-gateway/internal/mcp/vserver"
)

func TestW5ApprovalDenialContracts(t *testing.T) {
	now := time.Now()
	m, org := w5Manager(t, func() time.Time { return now })
	check := func(d *Denial) {
		t.Helper()
		if d == nil {
			t.Fatal("expected denial")
		}
		if errs := schematest.ValidateDef(t, "mcp-denial-v1.schema.json", "Error", d.Object()); len(errs) > 0 {
			t.Fatalf("%s violates denial contract: %v", d.Code, errs)
		}
	}
	cc := w5Call(org)
	d := m.Check(cc)
	check(d)
	id := d.Metadata.(map[string]any)["approval_id"].(string)
	w5Retry(cc, id)
	check(m.Check(cc))
	cc.Args = []byte(`{"v":2}`)
	check(m.Check(cc))
	cc.Args = []byte(`{"v":1}`)
	w5Approve(t, m, org, id)
	if d := m.Check(cc); d != nil {
		t.Fatal(d)
	}
	check(m.Check(cc)) // used must not pretend there is a new pending request
	now = now.Add(31 * time.Second)
	check(m.Check(cc))
	cc = w5Call(org)
	id = w5Pending(t, m, cc)
	if _, err := m.Decide(context.Background(), id, ApprovalDecision{OrgID: org, Decision: "deny", Actor: "admin"}); err != nil {
		t.Fatal(err)
	}
	w5Retry(cc, id)
	check(m.Check(cc))
	cc.ctx = context.WithValue(cc.Context(), approvalRetryKey{}, approvalRetry{Invalid: true})
	check(m.Check(cc))
	check(approvalUnavailable())
}

func TestW5ApprovalListedAccountAliases(t *testing.T) {
	cc := w5Call("org")
	cc.OrgConfig.MCP.VirtualServers["review"].Members = []string{"mock@work"}
	cc.OrgConfig.MCP.VirtualServers["review"].Approval.Tools = []string{"mock@work/*"}
	vs, _ := cc.OrgConfig.MCPVirtualServer("vs-review")
	member := vserver.ParseMember("mock@work")
	tools, _ := vserver.Compose(vserver.SpecOf(vs), []vserver.Tool{{Member: member, Name: "echo"}}, nil)
	cc.Tool.ExposedName = vserver.EncodePrefixed(member.Key(), "echo")
	cc.ctx = context.WithValue(cc.Context(), callViewKey{}, &callView{vs: vs, tools: tools, sep: "_"})
	if _, needed, err := approvalPolicy(cc); err != nil || !needed {
		t.Fatalf("account-qualified listed tool was not held: %v %v", needed, err)
	}
}

func TestW5ApprovalResolvedTrust(t *testing.T) {
	cc := w5Call("org")
	cc.OrgConfig.MCP.VirtualServers["review"].Approval.Require = "destructive"
	no := false
	cc.Tool.Annotations = &ToolAnnotations{DestructiveHint: &no}
	// The resolved pool, including static servers, is the source of trust.
	pool := &w3bPool{servers: map[string]PoolServer{"mock": {ID: "mock", TrustedAnnotations: true}}}
	cc.ctx = context.WithValue(cc.Context(), tenantPoolKey{}, pool)
	if _, needed, err := approvalPolicy(cc); err != nil || needed {
		t.Fatalf("resolved trusted annotation not honored: %v %v", needed, err)
	}
	pool.servers["mock"] = PoolServer{ID: "mock", TrustedAnnotations: false}
	if _, needed, err := approvalPolicy(cc); err != nil || !needed {
		t.Fatalf("untrusted false annotation accepted: %v %v", needed, err)
	}
}
