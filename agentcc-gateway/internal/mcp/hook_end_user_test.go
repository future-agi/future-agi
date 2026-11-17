package mcp

import "testing"

func TestVendorEndUserRefusedWhileVendorCredentialsDefault(t *testing.T) {
	s := NewServer(ServerConfig{})
	cc := &CallContext{OrgConfig: orgConfig(t, `{"mcp":{"servers":{"partner":{"credential":{"mode":"vendor_end_user","vendor":"pipedream"}}}}}`), Upstream: UpstreamRef{ServerID: "partner"}, Principal: Principal{Kind: PrincipalOAuth, OrgID: "org-a", UserID: "member-a"}}
	if d := resolveEndUser(s, cc); d == nil {
		t.Fatal("vendor server must not fall through to its pushed shared credential")
	}
}

func TestNonVendorServerUnaffectedByStep12a(t *testing.T) {
	if d := resolveEndUser(nil, &CallContext{}); d != nil {
		t.Fatal("non vendor call changed")
	}
}
