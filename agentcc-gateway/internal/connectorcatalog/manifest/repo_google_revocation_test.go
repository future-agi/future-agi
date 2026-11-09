package manifest

import (
	"os"
	"slices"
	"testing"
)

// googleRevocationURL is Google's OAuth 2.0 token revocation endpoint
// (developers.google.com/identity/protocols/oauth2/web-server, "Revoking a
// token", and accounts.google.com's openid-configuration revocation_endpoint).
const googleRevocationURL = "https://oauth2.googleapis.com/revoke"

// TestRepoGroupGoogleDeclaresGooglesRevocation holds IR-CN14c-3 (AM-C1 item
// 4): every provider in client group google is one OAuth client of one Cloud
// project, so each declares Google's revocation endpoint, or a disconnect
// would leave the user's grant live at Google. L0's one-client rule already
// refuses a group whose members disagree; this test refuses the group that
// agrees on "none", which L0 accepts.
func TestRepoGroupGoogleDeclaresGooglesRevocation(t *testing.T) {
	cat, err := validateRepoCatalog(os.DirFS(repoCatalogRoot))
	if err != nil {
		t.Fatalf("catalog/connectors is not valid:\n%v", err)
	}
	var members []string
	for _, a := range cat.Apps {
		for _, p := range a.Providers {
			o := p.Auth.OAuth2
			if o == nil || o.ClientGroup != "google" {
				continue
			}
			ref := a.ID + "/" + p.ID
			members = append(members, ref)
			r := o.Revocation
			if r == nil {
				t.Errorf("%s is in client group google but declares no revocation (want %s)", ref, googleRevocationURL)
				continue
			}
			if r.URL != googleRevocationURL || r.Method != "POST" || r.TokenBody != "form" || r.TokenField != "token" {
				t.Errorf("%s revocation = %s %s (%s, field %s), want POST %s (form, field token)",
					ref, r.Method, r.URL, r.TokenBody, r.TokenField, googleRevocationURL)
			}
		}
	}
	slices.Sort(members)
	for _, want := range []string{"gmail/futureagi-rest", "google-bigquery/mcp", "google-calendar/futureagi-rest"} {
		if !slices.Contains(members, want) {
			t.Errorf("client group google has no %s (members %v)", want, members)
		}
	}
}
