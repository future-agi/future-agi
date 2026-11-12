package cassette

import (
	"encoding/json"
	"regexp"
	"strings"
	"testing"
)

// TestScrubKeepsToolSchemas: a tools/list result's input and output schemas
// are structure: a property named like a credential keeps its type, limits
// (as numbers), description and required list, so the replayed tools/list
// matches the vendor's. The values a schema can carry under such a
// property (default, examples, enum, and everything inside a credential-
// named object property) are still scrubbed. Outside tools/list schemas a
// "properties" map is data (a CRM record's values) and a credential in it
// goes, as does one in a malformed schema.
func TestScrubKeepsToolSchemas(t *testing.T) {
	def, example, enum, region := fakeToken("schemadefault"), fakeToken("schemaexample"), fakeToken("schemaenum"), fakeToken("schemaregion")
	crm, malformed := fakeToken("crmtoken"), fakeToken("malformed")
	list := `{"tools":[{"name":"post_message","description":"Post a message","inputSchema":{"type":"object","properties":{` +
		`"bot_token":{"type":"string","minLength":8,"description":"Workspace bot token","default":"` + def + `","examples":["` + example + `"]},` +
		`"webhook_secret":{"type":"string","maxLength":64},` +
		`"credentials":{"type":"object","properties":{"client_secret":{"type":"string","enum":["` + enum + `"]},"region":{"type":"string","default":"` + region + `"}},"required":["client_secret"]},` +
		`"channel":{"type":"string","default":"general"}},"required":["channel","bot_token"]},` +
		`"outputSchema":{"type":"object","properties":{"access_key":{"type":"string"},"ts":{"type":"string"}}}},` +
		`{"name":"broken","inputSchema":{"type":"object","properties":{"token":"` + malformed + `"}}}]}`
	c := newMCPCassette(
		MCPExchange{Method: "tools/list", Params: raw(`{}`), Result: raw(list)},
		MCPExchange{Method: "tools/call", Params: raw(`{"name":"get_contact","arguments":{"id":"x"}}`),
			Result: raw(`{"structuredContent":{"properties":{"api_token":"` + crm + `","firstname":"Ada"}},` +
				`"content":[{"type":"text","text":"Workspace bot token rotated"}]}`)},
	)
	text := saveOK(t, c, ScrubConfig{})
	assertAbsent(t, text, def, example, enum, region, crm, malformed)
	xs := loadSaved(t, text).MCP.Exchanges
	var got struct {
		Tools []struct {
			InputSchema  json.RawMessage `json:"inputSchema"`
			OutputSchema json.RawMessage `json:"outputSchema"`
		} `json:"tools"`
	}
	if err := json.Unmarshal(xs[0].Result, &got); err != nil || len(got.Tools) != 2 {
		t.Fatalf("tools/list = %s (%v)", xs[0].Result, err)
	}
	in := compactJSON(t, got.Tools[0].InputSchema)
	for _, want := range []string{
		`"bot_token":{"type":"string","minLength":8,"description":"Workspace bot token","default":"scrubbed-token-`,
		`"webhook_secret":{"type":"string","maxLength":64}`,
		`"credentials":{"type":"object","properties":{"client_secret":{"type":"string","enum":["scrubbed-token-`,
		`"region":{"type":"string","default":"scrubbed-token-`, `"required":["client_secret"]`,
		`"channel":{"type":"string","default":"general"}`, `"required":["channel","bot_token"]`,
	} {
		if !strings.Contains(in, want) {
			t.Errorf("inputSchema lacks %s:\n%s", want, in)
		}
	}
	if !regexp.MustCompile(`"examples":\["scrubbed-token-[0-9]+"\]`).MatchString(in) {
		t.Errorf("inputSchema examples = %s", in)
	}
	if out := compactJSON(t, got.Tools[0].OutputSchema); out != `{"type":"object","properties":{"access_key":{"type":"string"},"ts":{"type":"string"}}}` {
		t.Errorf("outputSchema = %s", out)
	}
	if r := compactJSON(t, xs[1].Result); !strings.Contains(r, `"content":[{"type":"text","text":"Workspace bot token rotated"}]`) {
		t.Errorf("a schema description was collected and replaced elsewhere: %s", r)
	}
}
