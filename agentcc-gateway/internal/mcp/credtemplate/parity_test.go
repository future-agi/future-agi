package credtemplate

import (
	"encoding/json"
	"os"
	"regexp"
	"slices"
	"strings"
	"testing"
)

const (
	manifestSchema = "../../../catalog/connectors/schema/connector-manifest.v1.json"
	adminSchema    = "../../../../api_contracts/gateway/agentcc-admin.schema.json"
	// Django's one reserved-header list, which its vault and its push read
	// (the CW1 integration moved it out of mcp_credentials.py).
	djangoVault  = "../../../../futureagi/agentcc/services/mcp_credential_placement.py"
	probeSource  = "../probe.go"
	manifestDefs = "../../connectorcatalog/manifest/defs.go"
)

func readJSON(t *testing.T, path string) map[string]any {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var v map[string]any
	if err := json.Unmarshal(raw, &v); err != nil {
		t.Fatalf("%s: %v", path, err)
	}
	return v
}

// at walks a decoded JSON document by keys.
func at(t *testing.T, v any, keys ...string) any {
	t.Helper()
	for _, k := range keys {
		m, ok := v.(map[string]any)
		if !ok {
			t.Fatalf("no %q under %v", k, keys)
		}
		v = m[k]
	}
	return v
}

// sameLanguage checks that mine accepts exactly what theirs accepts, with
// theirs bounded to maxLen characters when maxLen > 0, over every single
// byte, the boundary lengths and the extra samples.
func sameLanguage(t *testing.T, what string, mine *regexp.Regexp, theirs string, maxLen int, samples ...string) {
	t.Helper()
	re := regexp.MustCompile(theirs)
	accept := func(s string) bool { return re.MatchString(s) && (maxLen <= 0 || len(s) <= maxLen) }
	inputs := slices.Clone(samples)
	for b := 0; b < 256; b++ {
		inputs = append(inputs, string([]byte{byte(b)}), "a"+string([]byte{byte(b)}))
	}
	for _, n := range []int{1, 31, 32, 33, 63, 64, 65, 128} {
		inputs = append(inputs, strings.Repeat("a", n))
	}
	for _, s := range inputs {
		if mine.MatchString(s) != accept(s) {
			t.Errorf("%s: %q is accepted by the gateway: %t, by the contract: %t", what, s, mine.MatchString(s), accept(s))
		}
	}
}

// TestPatternsMatchTheContracts: the names a part may have, the prefix and
// catalog_ref are exactly the contracts' (MCPCredentialSecret and its parts,
// connector-manifest/v1's headerName, queryName, varName and
// credentialPrefix, MCPServerOrgConfig.catalog_ref), and the part limits
// match: 1 + 7 pushed parts, 8 declared.
func TestPatternsMatchTheContracts(t *testing.T) {
	man := readJSON(t, manifestSchema)
	defs := at(t, man, "$defs").(map[string]any)
	pattern := func(def string) string { return at(t, defs, def, "pattern").(string) }
	sameLanguage(t, "varName", varNamePattern, pattern("varName"), 0, "_a", "a_1", "1a")
	sameLanguage(t, "queryName", queryNamePattern, pattern("queryName"), 0, "a.b~c-d_e")
	sameLanguage(t, "headerName", headerNamePattern, pattern("headerName"), 0, "X-Api-Key", "a b")
	sameLanguage(t, "credentialPrefix", prefixPattern, pattern("credentialPrefix"), 0, "Bearer ", "Bearer  ", "Token", " x")
	if got := at(t, defs, "credentialIn", "enum"); !equalStrings(got, []string{"header", "query", "url_variable"}) {
		t.Errorf("connector-manifest/v1 credentialIn is %v", got)
	}

	admin := readJSON(t, adminSchema)
	adefs := at(t, admin, "$defs").(map[string]any)
	for _, def := range []string{"MCPCredentialSecret", "MCPCredentialSecretPart"} {
		name := at(t, adefs, def, "properties", "name").(map[string]any)
		sameLanguage(t, def+".name", headerNamePattern, name["pattern"].(string), int(name["maxLength"].(float64)))
		if got := at(t, adefs, def, "properties", "in", "enum"); !equalStrings(got, []string{"header", "query", "url_variable"}) {
			t.Errorf("%s.in is %v", def, got)
		}
	}
	if got := int(at(t, adefs, "MCPCredentialSecret", "properties", "parts", "maxItems").(float64)); got != MaxParts-1 {
		t.Errorf("MCPCredentialSecret.parts holds at most %d, the gateway %d", got, MaxParts-1)
	}
	ref := at(t, adefs, "MCPServerOrgConfig", "properties", "catalog_ref", "pattern").(string)
	sameLanguage(t, "catalog_ref", refPattern, ref, 0, "close/close-mcp@1.0.0", "close/close-mcp@01.0.0",
		"a/close-mcp@1.0.0", "close/close-mcp@1.0", strings.Repeat("a", 41)+"/p@1.0.0", "ab/"+strings.Repeat("p", 25)+"@1.0.0")
	if got := int(at(t, defs, "auth", "properties", "credentialParts", "maxItems").(float64)); got != MaxParts {
		t.Errorf("connector-manifest/v1 allows %d credential parts, the gateway %d", got, MaxParts)
	}
}

func equalStrings(v any, want []string) bool {
	list, ok := v.([]any)
	if !ok || len(list) != len(want) {
		return false
	}
	for i, x := range list {
		if x != want[i] {
			return false
		}
	}
	return true
}

// quoted are the double-quoted strings of src between start and the first
// end after it.
func quoted(t *testing.T, path, start, end string) []string {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	src := string(raw)
	i := strings.Index(src, start)
	if i < 0 {
		t.Fatalf("%s: %q not found", path, start)
	}
	j := strings.Index(src[i:], end)
	if j < 0 {
		t.Fatalf("%s: no %q after %q", path, end, start)
	}
	var out []string
	for _, m := range regexp.MustCompile(`"([^"]+)"`).FindAllStringSubmatch(src[i:i+j], -1) {
		out = append(out, m[1])
	}
	if len(out) == 0 {
		t.Fatalf("%s: no names between %q and %q", path, start, end)
	}
	return out
}

// TestReservedHeadersCoverEveryList: every header Django's vault, the
// gateway's probe and connector-manifest/v1 refuse for a credential is
// refused here too, so nothing they refuse is placed by a template.
func TestReservedHeadersCoverEveryList(t *testing.T) {
	lists := map[string][]string{
		"Django _RESERVED_HEADERS":   quoted(t, djangoVault, "_RESERVED_HEADERS = frozenset(", ")"),
		"probe probeReservedHeaders": quoted(t, probeSource, "var probeReservedHeaders", "}"),
		"manifest transportHeaders":  quoted(t, manifestDefs, "var transportHeaders", "}"),
	}
	for list, names := range lists {
		for _, n := range names {
			if !ReservedHeader(n) || !ReservedHeader(strings.ToUpper(n)) {
				t.Errorf("%s refuses %s, and the template would place a credential in it", list, n)
			}
		}
	}
	for _, ok := range []string{"Authorization", "X-Api-Key", "Close-API-Key", "DD-API-KEY", "Cookie", "Api-Key"} {
		if ReservedHeader(ok) {
			t.Errorf("%s is a credential header, refused", ok)
		}
	}
}
