package conformance

import (
	"fmt"
	"regexp"
	"slices"
	"strings"
	"testing"
	"testing/fstest"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

// rowIDs are a case's rows, a skipped one marked " (skip)".
func rowIDs(cs *Case) []string {
	var out []string
	for _, r := range cs.Rows {
		id := r.ID
		if r.Skip != "" {
			id += " (skip)"
		}
		out = append(out, id)
	}
	return out
}

func TestBuildPlansEveryProviderOfBothCatalogs(t *testing.T) {
	plan := mustPlan(t)
	var ids []string
	for _, cs := range plan.Cases {
		ids = append(ids, cs.ID)
	}
	want := []string{
		"cn1-example/close/close-mcp", "cn1-example/github/local", "cn1-example/github/remote",
		"cn1-example/gmail/futureagi-rest", "cn1-example/gmail/relay", "cn1-example/linear/linear-mcp",
		"cn1-example/slack/slack-mcp", "cn1-example/snowflake/cortex-mcp", "cn1-example/stripe/stripe-mcp",
		"l3-fixtures/acme/legacy", "l3-fixtures/acme/local", "l3-fixtures/acme/remote", "l3-fixtures/acme/search",
	}
	if !slices.Equal(ids, want) {
		t.Fatalf("cases %v\nwant  %v", ids, want)
	}
	if plan.Harness != HarnessVersion || plan.InboundEra != InboundEra || plan.StaticOrg != StaticOrg {
		t.Fatalf("plan header %+v", plan)
	}
}

func TestBuildRowsForARecordedProviderWithAHeaderKey(t *testing.T) {
	cs := caseByID(t, mustPlan(t), remoteCase)
	want := []string{
		"annotations.acme_remote__list_spaces", "annotations.acme_remote__purge_docs",
		"annotations.acme_remote__search_docs", "annotations.acme_remote__update_doc",
		"call.allowed.acme_remote__list_spaces", "call.allowed.acme_remote__search_docs",
		"call.denied.acme_remote__purge_docs", "call.denied.acme_remote__update_doc",
		"credential.header.X-Acme-Key", "credential.inbound_not_forwarded", "credential.never_logged",
		"list.posture_view", "list.through_virtual_server", "posture.ask (skip)", "rate_limits (skip)",
		"recording.matches_manifest", "records.one_per_call", "transport.era",
	}
	if got := rowIDs(cs); !slices.Equal(got, want) {
		t.Fatalf("rows %v\nwant %v", got, want)
	}
	// The recording serves list_spaces, which the manifest does not declare:
	// it takes the default posture (auto); ask and deny are refused.
	for name, denied := range map[string]bool{"list_spaces": false, "search_docs": false, "update_doc": true, "purge_docs": true} {
		if tl := toolByName(t, cs, name); tl.Denied != denied {
			t.Errorf("%s denied = %t, want %t (posture %s)", name, tl.Denied, denied, tl.Posture)
		}
	}
	if toolByName(t, cs, "list_spaces").Declared {
		t.Error("list_spaces is not declared by the manifest")
	}
	sd := toolByName(t, cs, "search_docs")
	if !sameJSON(sd.Arguments, []byte(`{"query":"roadmap"}`)) || len(sd.result) == 0 {
		t.Errorf("search_docs takes the recorded call's arguments and result: %s", sd.Arguments)
	}
	if cs.Era != "2025-06-18" || cs.PinEra != "" || cs.Path != "/aaa/mcp" || cs.URLVariables["workspace"] != "aaa" {
		t.Errorf("era %q pin %q path %q vars %v", cs.Era, cs.PinEra, cs.Path, cs.URLVariables)
	}
	sec := cs.Credential.Secret
	if sec == nil || sec.Type != "api_key" || sec.In != "header" || sec.Name != "X-Acme-Key" || sec.Header != "X-Acme-Key" {
		t.Fatalf("secret %+v", sec)
	}
	if !strings.HasPrefix(sec.Value, "cn8-l3-") || !slices.Contains(cs.Credential.Values, sec.Value) {
		t.Fatal("the secret is a test value the never-logged row looks for")
	}
	if len(cs.Drift) != 0 || cs.Recording == "" {
		t.Fatalf("recording %q drift %v", cs.Recording, cs.Drift)
	}
}

func TestBuildSkipsWhatThisBaseCannotPlace(t *testing.T) {
	plan := mustPlan(t)
	for _, tc := range []struct {
		caseID, row, reason string
	}{
		{"l3-fixtures/acme/search", "credential.query.sig", "keeps no query string to witness it"},
		{"l3-fixtures/acme/search", "credential.url_variable.tenantToken", "{tenantToken}"},
		{"l3-fixtures/acme/search", "credential.header.X-Acme-Client", "holds back the whole credential"},
		{"l3-fixtures/acme/search", "url.user.account", "per-user resolution"},
		{"l3-fixtures/acme/search", rowUserMode, "own gateway token"},
		{"l3-fixtures/acme/legacy", "credential.header.Authorization", `prefix "Token "`},
		{"l3-fixtures/acme/legacy", "transport.path", "/sse"},
		{"cn1-example/close/close-mcp", "credential.header.Close-Scope", "customer sets"},
		{"cn1-example/close/close-mcp", "credential.header.Close-API-Key", "holds back the whole credential"},
		{"cn1-example/linear/linear-mcp", "credential.oauth", "per-user connect flow"},
		{"cn1-example/linear/linear-mcp", rowPins, "tool pins"},
		{"cn1-example/linear/linear-mcp", rowRateLimits, "tool rate limits"},
		{"cn1-example/stripe/stripe-mcp", rowGeneric, "argument translator"},
		{"cn1-example/gmail/futureagi-rest", rowK2Runtime, "94_cn_rest_runtime.sh --catalog"},
		{"cn1-example/github/local", "credential.env_value", "never their values"},
		{"cn1-example/slack/slack-mcp", "tools.declared", "recording"},
		{"cn1-example/gmail/relay", rowPartner, "vendor credentials"},
	} {
		cs := caseByID(t, plan, tc.caseID)
		i := slices.IndexFunc(cs.Rows, func(r Row) bool { return r.ID == tc.row })
		if i < 0 || !strings.Contains(cs.Rows[i].Skip, tc.reason) {
			t.Errorf("%s: row %s should be skipped naming %q; rows %v", tc.caseID, tc.row, tc.reason, rowIDs(cs))
		}
	}
	if relay := caseByID(t, plan, "cn1-example/gmail/relay"); relay.Skip == "" || len(relay.Rows) != 1 {
		t.Errorf("a partner provider is skipped whole: %+v", relay)
	}
	// The gateway takes a catalog credential whole: a provider with a
	// part L3 cannot push or witness pushes none of its parts, and its mock
	// requires none.
	for _, id := range []string{"cn1-example/close/close-mcp", "l3-fixtures/acme/search"} {
		cs := caseByID(t, plan, id)
		if cs.Credential.Secret != nil || len(cs.Credential.Expect) != 0 || slices.Contains(cs.MockArgs, "--auth-header") {
			t.Errorf("%s: secret %+v expect %v mock args %v: want nothing pushed", id, cs.Credential.Secret, cs.Credential.Expect, cs.MockArgs)
		}
	}
	// Credential placement and the REST runtime are built: no reason may say
	// either still waits.
	for _, cs := range plan.Cases {
		for _, r := range cs.Rows {
			if strings.Contains(r.Skip, "refuses a placement until") || strings.Contains(r.Skip, "runtime is not built") {
				t.Errorf("%s: row %s gives a reason that is no longer true: %s", cs.ID, r.ID, r.Skip)
			}
		}
	}
}

// workItemRE matches an internal work-item code (a lane, plan amendment or
// review request id). A skip reason is printed in the report an operator
// reads, so it says in plain words what the row waits on and never cites one.
var workItemRE = regexp.MustCompile(`\b(M\d+x?|CN\d+[a-z]?|V\d+|AM-C\d+|R\d+|CW\d+[a-z]?|IR-\w+|[A-Z]+-PLAN)\b`)

// plainReason reports whether a skip reason is one an operator can read:
// a sentence of its own that cites no internal work item.
func plainReason(skip string) bool {
	return len(skip) >= 40 && !workItemRE.MatchString(skip)
}

func TestEverySkipGivesAPlainReason(t *testing.T) {
	for _, cs := range mustPlan(t).Cases {
		seen := map[string]bool{}
		for _, r := range cs.Rows {
			if seen[r.ID] {
				t.Errorf("%s: row %s twice", cs.ID, r.ID)
			}
			seen[r.ID] = true
			if r.Skip != "" && !plainReason(r.Skip) {
				t.Errorf("%s: row %s skips without a plain reason: %s", cs.ID, r.ID, r.Skip)
			}
		}
	}
}

func TestBuildPlansAStdioProviderAsAStaticServer(t *testing.T) {
	cs := caseByID(t, mustPlan(t), localCase)
	if cs.Transport != "stdio" || cs.Org != StaticOrg || cs.EnvProbe != "acme_local__env_names" {
		t.Fatalf("transport %q org %q probe %q", cs.Transport, cs.Org, cs.EnvProbe)
	}
	if v := cs.Credential.Env["ACME_TOKEN"]; !strings.HasPrefix(v, "cn8-l3-") || !slices.Contains(cs.Credential.Values, v) {
		t.Fatalf("ACME_TOKEN gets a test value the never-logged row looks for: %q", v)
	}
	if !slices.Contains(rowIDs(cs), prefixEnv+"ACME_TOKEN") || cs.Credential.Secret != nil {
		t.Fatalf("rows %v secret %+v", rowIDs(cs), cs.Credential.Secret)
	}
	if !slices.Equal(cs.MockArgs, []string{"--mode", "stdio", "--era", "dual"}) {
		t.Fatalf("mock args %v", cs.MockArgs)
	}
}

func TestBuildPinsTheSSEEraAndAStructuredResult(t *testing.T) {
	cs := caseByID(t, mustPlan(t), legacyCase)
	if cs.Transport != "sse" || cs.Era != "2024-11-05" || cs.PinEra != "2024-11-05" || cs.Rest != "/sse" {
		t.Fatalf("transport %q era %q pin %q rest %q", cs.Transport, cs.Era, cs.PinEra, cs.Rest)
	}
	if cs.Credential.Secret != nil || len(cs.Credential.Expect) != 0 {
		t.Fatal("a Token-prefixed key is not pushed")
	}
	if ds := toolByName(t, cs, "doc_stats"); !strings.Contains(string(ds.result), `"structuredContent":{"documents":0,"folders":0}`) {
		t.Fatalf("doc_stats result %s", ds.result)
	}
}

func TestNeverLoggedRunsOnlyWhereACredentialIsPlaced(t *testing.T) {
	plan := mustPlan(t)
	for id, want := range map[string]bool{remoteCase: true, localCase: true, linearCase: false, legacyCase: false} {
		if got := slices.Contains(rowIDs(caseByID(t, plan, id)), rowNotLogged); got != want {
			t.Errorf("%s: row %s present %t, want %t", id, rowNotLogged, got, want)
		}
	}
}

func TestPlaceCredentialNamesWhatNoLaneBuildsYet(t *testing.T) {
	for kind, row := range map[string]string{
		manifest.AuthBasic: "credential.basic", manifest.AuthServiceAccount: "credential.service_account",
		manifest.AuthMCPOAuth: "credential.oauth", manifest.AuthOAuth2: "credential.oauth",
	} {
		p := &manifest.Provider{Transport: manifest.Transport{Kind: "http"}, Auth: manifest.Auth{Kind: kind}}
		cred, rows := placeCredential("x/y@1.0.0", p)
		if cred.Secret != nil || len(rows) != 1 || rows[0].ID != row || !plainReason(rows[0].Skip) {
			t.Errorf("%s: secret %+v rows %+v", kind, cred.Secret, rows)
		}
		if rows[0].Skip == "" {
			t.Errorf("%s: row %s runs, but nothing places the credential", kind, row)
		}
	}
	p := &manifest.Provider{Transport: manifest.Transport{Kind: "http"}, Auth: manifest.Auth{Kind: manifest.AuthNone}}
	if cred, rows := placeCredential("x/y@1.0.0", p); cred.Secret != nil || len(rows) != 0 {
		t.Errorf("auth none places nothing: %+v %+v", cred, rows)
	}
	two := &manifest.Provider{Transport: manifest.Transport{Kind: "http"}, Auth: manifest.Auth{Kind: manifest.AuthAPIKey,
		CredentialParts: []manifest.CredentialPart{{In: "header", Name: "DD-API-KEY", Secret: true}, {In: "header", Name: "DD-APPLICATION-KEY", Secret: true}}}}
	// A second secret header part is pushed in the secret's parts,
	// and the mock witnesses it by sha256.
	cred, rows := placeCredential("datadog/mcp@1.0.0", two)
	if cred.Secret == nil || cred.Secret.Name != "DD-API-KEY" || len(cred.Secret.Parts) != 1 ||
		cred.Secret.Parts[0] != (SecretPart{In: "header", Name: "DD-APPLICATION-KEY", Value: cred.Secret.Parts[0].Value}) ||
		len(rows) != 2 || rows[0].Skip != "" || rows[1].Skip != "" {
		t.Fatalf("two secret headers: secret %+v rows %+v", cred.Secret, rows)
	}
	second := cred.Secret.Parts[0].Value
	if len(cred.Expect) != 2 || cred.Expect[1] != (Placement{Row: "credential.header.DD-APPLICATION-KEY", Header: "dd-application-key", SHA256: sha256Hex(second)}) ||
		!slices.Contains(cred.Values, second) || !hasFlag(cred.MockArgs, "--credential-header", "DD-APPLICATION-KEY") {
		t.Errorf("two secret headers: expect %+v values %d mock args %v", cred.Expect, len(cred.Values), cred.MockArgs)
	}
	if strings.Contains(fmt.Sprintf("%v", cred), cred.Secret.Value) || strings.Contains(fmt.Sprintf("%v", cred), second) {
		t.Error("a Credential prints its test value")
	}
}

// A constant part is never pushed; the gateway adds it from the
// catalog entry the catalog_ref names, and the mock witnesses
// it by sha256 of the catalog's value. A provider with a part L3 cannot push
// or witness pushes nothing, since the gateway refuses a catalog credential
// pushed in part.
func TestPlaceCredentialWhole(t *testing.T) {
	withConstant := &manifest.Provider{Transport: manifest.Transport{Kind: "http"}, Auth: manifest.Auth{Kind: manifest.AuthAPIKey,
		CredentialParts: []manifest.CredentialPart{{In: "header", Name: "X-Key", Secret: true}, {In: "header", Name: "X-Client", Value: "futureagi"}}}}
	cred, rows := placeCredential("acme/k@1.0.0", withConstant)
	if cred.Secret == nil || cred.Secret.Name != "X-Key" || len(cred.Secret.Parts) != 0 || len(rows) != 2 || rows[0].Skip != "" || rows[1].Skip != "" {
		t.Fatalf("a constant part: secret %+v rows %+v", cred.Secret, rows)
	}
	if len(cred.Expect) != 2 || cred.Expect[1] != (Placement{Row: "credential.header.X-Client", Header: "x-client", SHA256: sha256Hex("futureagi")}) ||
		slices.Contains(cred.Values, "futureagi") || !hasFlag(cred.MockArgs, "--credential-header", "X-Client") {
		t.Errorf("a constant part: expect %+v values %v mock args %v", cred.Expect, cred.Values, cred.MockArgs)
	}

	for name, parts := range map[string][]manifest.CredentialPart{
		"a query part":               {{In: "header", Name: "X-Key", Secret: true}, {In: "query", Name: "sig", Secret: true}},
		"a customer-set header":      {{In: "header", Name: "X-Key", Secret: true}, {In: "header", Name: "X-Scope"}},
		"an unsupported prefix":      {{In: "header", Name: "X-Key", Secret: true}, {In: "header", Name: "Authorization", Prefix: "Token ", Secret: true}},
		"a url variable first":       {{In: "url_variable", Name: "tenant", Secret: true}, {In: "header", Name: "X-Key", Secret: true}},
		"only a constant, no secret": {{In: "header", Name: "X-Client", Value: "futureagi"}},
	} {
		p := &manifest.Provider{Transport: manifest.Transport{Kind: "http"}, Auth: manifest.Auth{Kind: manifest.AuthAPIKey, CredentialParts: parts}}
		cred, rows := placeCredential("acme/k@1.0.0", p)
		if cred.Secret != nil || len(cred.Expect) != 0 || len(cred.Values) != 0 || len(cred.MockArgs) != 0 || len(rows) != len(parts) {
			t.Errorf("%s: secret %+v expect %v mock args %v rows %+v: want nothing pushed", name, cred.Secret, cred.Expect, cred.MockArgs, rows)
			continue
		}
		for _, r := range rows {
			if r.Skip == "" || !plainReason(r.Skip) {
				t.Errorf("%s: row %s runs or gives no plain reason: %q", name, r.ID, r.Skip)
			}
		}
	}
}

func hasFlag(args []string, flag, value string) bool {
	for i := 0; i+1 < len(args); i++ {
		if args[i] == flag && args[i+1] == value {
			return true
		}
	}
	return false
}

func TestBuildRefuses(t *testing.T) {
	invalid := fstest.MapFS{"apps/x/app.yaml": {Data: []byte("schema: nope\n")}}
	if _, err := Build([]Catalog{{Label: "bad", FS: invalid}}); err == nil || !strings.Contains(err.Error(), "catalog bad") {
		t.Fatalf("an invalid catalog: %v", err)
	}
	if _, err := Build([]Catalog{{Label: "empty", FS: fstest.MapFS{}}}); err == nil || !strings.Contains(err.Error(), "no provider") {
		t.Fatalf("an empty catalog: %v", err)
	}
}

func TestIDsAreStableAndValid(t *testing.T) {
	vsRE := regexp.MustCompile(`^vs_[0-9A-HJKMNP-TV-Z]{26}$`)
	if a, b := vsID("x#posture"), vsID("x#posture"); a != b || !vsRE.MatchString(a) || a == vsID("x#audit") {
		t.Fatalf("vsID %s %s", a, b)
	}
	ids := map[string]bool{}
	first := serverID("app-with-dashes", "prov", ids)
	second := serverID("app-with-dashes", "prov", ids)
	long := serverID(strings.Repeat("a", 40), "provider", ids)
	if first != "app_with_dashes_prov" || second != "app_with_dashes_prov_2" || len(long) != 40 {
		t.Fatalf("server ids %s %s %s", first, second, long)
	}
	idRE := regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,47}$`)
	for _, cs := range mustPlan(t).Cases {
		if !idRE.MatchString(cs.ServerID) {
			t.Errorf("%s: server id %q breaks the org config's id pattern", cs.ID, cs.ServerID)
		}
	}
}
