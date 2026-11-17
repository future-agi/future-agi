package mcp

import (
	"strings"
	"testing"
)

type fixtureVendorCredentials struct{ asserted bool }

func (f fixtureVendorCredentials) AllowAsserted(*CallContext) bool { return f.asserted }
func (fixtureVendorCredentials) Resolve(*CallContext, string) (*AuthOverride, *Denial) {
	panic("stage 1 must not dispatch a vendor call")
}

func TestEndUserBoundWithoutRawEvidenceOrLegacyFallback(t *testing.T) {
	s := NewServer(ServerConfig{})
	s.SetDeps(Deps{VendorCredentials: fixtureVendorCredentials{asserted: true}})
	for _, tc := range []struct {
		name                      string
		principal                 Principal
		asserted, source, subject string
		refused                   bool
	}{
		{"oauth", Principal{Kind: PrincipalOAuth, OrgID: "org-a", UserID: "member-a"}, "", "member", "member-a", false},
		{"org asserted", Principal{Kind: PrincipalKey, OrgID: "org-a"}, "customer-a", "asserted", "customer-a", false},
		{"personal conflict", Principal{Kind: PrincipalKey, OrgID: "org-a", UserID: "member-a"}, "customer-a", "", "", true},
		{"never key creator", Principal{Kind: PrincipalKey, OrgID: "org-a", KeyID: "created-by-member-a"}, "", "", "", true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			cc := &CallContext{OrgConfig: orgConfig(t, `{"mcp":{"servers":{"partner":{"credential":{"mode":"vendor_end_user","vendor":"pipedream"}}}}}`), Upstream: UpstreamRef{ServerID: "partner"}, Principal: tc.principal}
			cc.Agent.EndUser = tc.asserted
			denial := resolveEndUser(s, cc)
			identity, ok := EndUserOf(cc)
			if denial == nil {
				t.Fatal("legacy credential path became executable")
			}
			if tc.refused {
				if ok {
					t.Fatal("refused identity stored")
				}
				return
			}
			if !ok || identity.Source != tc.source || identity.Subject != tc.subject {
				t.Fatal("wrong bound identity")
			}
			raw := string(cc.Evidence["io.futureagi/end_user"])
			if !strings.Contains(raw, identity.Hash) || strings.Contains(raw, tc.subject) {
				t.Fatal("raw identity in evidence or missing hash")
			}
		})
	}
}
