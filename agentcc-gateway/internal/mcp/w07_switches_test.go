package mcp

import (
	"context"
	"net/http/httptest"
	"testing"
)

func TestW07NamedSelectionsNeverFallBackWhenOff(t *testing.T) {
	for _, on := range []bool{false, true} {
		for _, selection := range []string{"explicit", "default", "key"} {
			s := NewServer(ServerConfig{VirtualServersEnabled: on})
			access := `"default_for_org":true`
			if selection == "key" {
				access = `"keys":["restricted"]`
			}
			if selection == "explicit" {
				access = `"default_for_org":false`
			}
			cfg := orgConfig(t, `{"mcp":{"enabled":true,"access":{"default":"allow"},"virtual_servers":{"named":{"id":"vs_named","members":[],"access":{`+access+`}}}}}`)
			r := httptest.NewRequest("POST", "/mcp", nil)
			if selection == "explicit" {
				r = WithVirtualServer(r, "named")
			}
			cc := s.newCallContext(r, &Message{Method: MethodToolsList}, nil)
			cc.Principal = Principal{Kind: PrincipalAdmin, OrgID: "org-a", KeyID: "restricted"}
			cc.OrgConfig = cfg
			denial := resolveVirtualServer(s, cc)
			if on && denial != nil {
				t.Fatalf("ON rejected %s", selection)
			}
			if !on && (denial == nil || denial.Code != "virtual_server_not_found" || callViewOf(cc) != nil) {
				t.Fatalf("OFF widened %s selection", selection)
			}
			cc.runFinalizers()
			s.Close()
		}
	}
}

func TestW07IsolationAndStaticCompatibilityRemainOn(t *testing.T) {
	s := NewServer(ServerConfig{})
	defer s.Close()
	s.Registry().SetStaticOrg("owner")
	for _, org := range []string{"owner", "other", ""} {
		cc := s.newCallContext(httptest.NewRequest("POST", "/mcp", nil), &Message{Method: MethodToolsList}, nil)
		cc.Principal = Principal{Kind: PrincipalKey, OrgID: org}
		if d := resolveVirtualServer(s, cc); d != nil {
			t.Fatal(d.Code)
		}
		view := callViewOf(cc)
		if org == "owner" && view != nil {
			t.Fatal("static owner lost legacy view")
		}
		if org != "owner" && (view == nil || view.denyAll == "") {
			t.Fatal("foreign/anonymous tenant gained static access")
		}
		cc.runFinalizers()
	}
}

func TestW07AdminNamedPreviewsOff(t *testing.T) {
	s := NewServer(ServerConfig{})
	defer s.Close()
	if _, err := s.AdminTools("org-a", "named"); err != ErrUnknownVirtualServer {
		t.Fatal(err)
	}
	if _, err := s.AdminTestTool(httptest.NewRequest("POST", "/mcp", nil), "org-a", "named", "static_read", nil); err != ErrUnknownVirtualServer {
		t.Fatal(err)
	}
	if _, err := s.AdminServedTools(context.Background(), "org-a", "named", ""); err != ErrUnknownVirtualServer {
		t.Fatal(err)
	}
}
