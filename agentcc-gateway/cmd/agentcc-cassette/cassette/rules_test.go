package cassette

import (
	"reflect"
	"strings"
	"testing"
)

// TestScrubConfigAddsCredentialNames: a recording can name more credential
// parameters, member names and headers than the built-in rules know; their
// values go, and the cassette carries the parameter and member names, so
// replay blinds them too. A name with nothing to match is refused.
func TestScrubConfigAddsCredentialNames(t *testing.T) {
	param, member, header, echoed := fakeToken("vendorparam"), fakeToken("vendormember"), fakeToken("vendorheader"), fakeToken("echoed")
	c := newHTTPCassette(HTTPExchange{
		Request: HTTPRequest{Method: "POST", Path: "/v1/run", Query: "vendor_cred=" + param + "&page=2",
			Headers: hdr("Content-Type", "application/json", "X-Vendor-Ident", header),
			Body:    &Body{JSON: raw(`{"opaqueHandle":"` + member + `","query":"x"}`)}},
		Response: HTTPResponse{Status: 200, Headers: hdr("Content-Type", "application/json"),
			Body: &Body{JSON: raw(`{"run":{"opaque_handle":"` + echoed + `","next":"https://api.example.test/v1/run?vendor_cred=` + param + `&page=3"}}`)}},
	})
	cfg := ScrubConfig{SecretParams: []string{"vendor_cred"}, SecretKeys: []string{"opaqueHandle"}, CredentialHeaders: []string{"X-Vendor-Ident"}}
	text := saveOK(t, c, cfg)
	assertAbsent(t, text, param, member, header, echoed)
	assertPresent(t, text, "page=2", "page=3", `"query": "x"`)
	got := loadSaved(t, text)
	want := &Rules{SecretParams: []string{"vendor_cred"}, SecretKeys: []string{"opaqueHandle"}}
	if !reflect.DeepEqual(got.Rules, want) {
		t.Errorf("the cassette's rules = %+v, want %+v", got.Rules, want)
	}
	for name, bad := range map[string]ScrubConfig{
		"an empty parameter name":      {SecretParams: []string{""}},
		"a member name of punctuation": {SecretKeys: []string{"--"}},
	} {
		t.Run(name, func(t *testing.T) {
			err := Save(t.TempDir()+"/c.json", newHTTPCassette(), bad)
			if err == nil || !strings.Contains(err.Error(), "secret") {
				t.Errorf("Save = %v, want a refusal of the name", err)
			}
		})
	}
}

// TestDescribeHidesDataShapedNames: a miss report names a request's
// argument and parameter names, never its values; a name that is itself
// data (an email, a uuid, a credential shape) is hashed like a value.
func TestDescribeHidesDataShapedNames(t *testing.T) {
	k := NewKeyer()
	pat := fakeShape("github-pat")
	mcp := k.DescribeMCP("tools/call", raw(`{"name":"freebusy","arguments":{"`+aliceEmail+`":{"busy":[]},"`+pat+`":1,"calendar":"primary"}}`))
	http := k.DescribeHTTP("GET", "/v1/items", "q=x&"+strings.Replace(bobEmail, "@", "%40", 1)+"=1&"+workspaceID+"=2", "", nil)
	for _, d := range []string{mcp, http} {
		for _, v := range []string{"alice", "bob.smith", "corp-test", workspaceID, pat[:12]} {
			if strings.Contains(d, v) {
				t.Errorf("the description %q names a data-shaped name", d)
			}
		}
	}
	if !strings.Contains(mcp, "calendar") || !strings.Contains(http, "q") {
		t.Errorf("ordinary names are gone: %q, %q", mcp, http)
	}
}

// TestValidateLiteralInSecretName: a literal spelled in a credential name
// the cassette keeps is refused where the scrubber would rewrite that name
// in the recording (then the rule could match nothing, and the literal
// would reach the file through the rules): anywhere in a parameter name
// (query and form text is scrubbed as text), and standing alone in a JSON
// member name (where a literal inside a word is another word, kept).
func TestValidateLiteralInSecretName(t *testing.T) {
	for name, tc := range map[string]struct {
		cfg     ScrubConfig
		refused bool
	}{
		"a standalone literal in a member name": {ScrubConfig{Literals: []string{"acmecorp"}, SecretKeys: []string{"acmecorp_handle"}}, true},
		"a literal inside a member name's word": {ScrubConfig{Literals: []string{"test"}, SecretKeys: []string{"contest_handle"}}, false},
		"a literal in a parameter name":         {ScrubConfig{Literals: []string{"token"}, SecretParams: []string{"private_token"}}, true},
		"a literal inside a parameter's word":   {ScrubConfig{Literals: []string{"test"}, SecretParams: []string{"contest_cred"}}, true},
		"no literal in either":                  {ScrubConfig{Literals: []string{"acmecorp"}, SecretKeys: []string{"opaqueHandle"}, SecretParams: []string{"vendor_cred"}}, false},
	} {
		t.Run(name, func(t *testing.T) {
			err := tc.cfg.Validate()
			if refused := err != nil; refused != tc.refused {
				t.Errorf("Validate = %v, want refused %v", err, tc.refused)
			}
			if err != nil && !strings.Contains(err.Error(), "spells a literal to scrub") {
				t.Errorf("Validate = %v, want it to name the clash", err)
			}
		})
	}
	t.Run("a member name kept as the recording has it", func(t *testing.T) {
		member := fakeToken("contestmember")
		c := newHTTPCassette(HTTPExchange{Request: HTTPRequest{Method: "GET", Path: "/v1/me"},
			Response: HTTPResponse{Status: 200, Headers: hdr("Content-Type", "application/json"),
				Body: &Body{JSON: raw(`{"contest_handle":"` + member + `"}`)}}})
		c.TestAccount = "ci-account"
		text := saveOK(t, c, ScrubConfig{Literals: []string{"test"}, SecretKeys: []string{"contest_handle"}})
		assertAbsent(t, text, member)
		assertPresent(t, text, `"contest_handle": "scrubbed-token-1"`)
	})
}
