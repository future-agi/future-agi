package mcpmock

import (
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// TestParseToolsShapesAndRefusals: a tool list is an array or {"tools": [...]};
// price_usd and result are kept off the listed definition, a missing
// inputSchema gets the empty object schema, and a bad list is refused.
func TestParseToolsShapesAndRefusals(t *testing.T) {
	for _, src := range []string{
		`[{"name":"a","price_usd":0.5,"result":{"content":[]}}]`,
		`{"tools":[{"name":"a","price_usd":0.5,"result":{"content":[]}}]}`,
		// a captured tools/list result: its other members are ignored
		`{"tools":[{"name":"a","price_usd":0.5,"result":{"content":[]}}],"nextCursor":"c2","resultType":"complete","ttlMs":60000,"cacheScope":"session"}`,
	} {
		tools, err := ParseTools([]byte(src))
		if err != nil || len(tools) != 1 {
			t.Fatalf("ParseTools(%s) = %v, %v", src, tools, err)
		}
		def := string(tools[0].Def)
		if tools[0].PriceUSD != 0.5 || len(tools[0].Result) == 0 || strings.Contains(def, "price_usd") ||
			strings.Contains(def, "result") || !strings.Contains(def, `"inputSchema":{"type":"object"}`) {
			t.Fatalf("tool = %+v, def %s", tools[0], def)
		}
	}
	for name, src := range map[string]string{
		"not json":       `nope`,
		"no name":        `[{"description":"x"}]`,
		"empty name":     `[{"name":""}]`,
		"duplicate":      `[{"name":"a"},{"name":"a"}]`,
		"negative price": `[{"name":"a","price_usd":-1}]`,
		"price string":   `[{"name":"a","price_usd":"1"}]`,
		"wrong wrapper":  `{"tool":[{"name":"a"}]}`,
		"no wrapper":     `{}`,
		"tools not list": `{"tools":"a"}`,
	} {
		if _, err := ParseTools([]byte(src)); err == nil {
			t.Fatalf("%s: ParseTools(%s) accepted", name, src)
		}
	}
}

// TestLoadFixturesMissingFilesAreEmpty: a directory without some files loads
// empty lists for them; a broken file is an error naming it.
func TestLoadFixturesMissingFilesAreEmpty(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "tools.json"), []byte(`[{"name":"only"}]`), 0o600); err != nil {
		t.Fatal(err)
	}
	fix, err := LoadFixtures(dir)
	if err != nil || len(fix.Tools) != 1 || len(fix.Resources) != 0 || len(fix.Prompts) != 0 {
		t.Fatalf("LoadFixtures = %+v, %v", fix, err)
	}
	if err := os.WriteFile(filepath.Join(dir, "prompts.json"), []byte(`[{"title":"no name"}]`), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadFixtures(dir); err == nil || !strings.Contains(err.Error(), "prompts.json") {
		t.Fatalf("a broken prompts.json = %v", err)
	}
	if fix, err := LoadFixtures(""); err != nil || len(fix.Tools) != 0 {
		t.Fatalf("no fixtures dir = %+v, %v", fix, err)
	}
}

// TestControlRefusesBadInput: an unknown behaviour field, a negative count, a
// bad tool list and a bad last are refused with 400, and leave the armed
// behaviour as it was.
func TestControlRefusesBadInput(t *testing.T) {
	m, srv := testMock(t, "dual", "none")
	postControl(t, srv.URL, "/__control/behavior", `{"page_size":2}`)
	for path, body := range map[string]string{
		"/__control/behavior": `{"pagesize":2}`,
		"/__control/tools":    `[{"name":"a"},{"name":"a"}]`,
	} {
		resp, err := http.Post(srv.URL+path, "application/json", strings.NewReader(body))
		if err != nil {
			t.Fatal(err)
		}
		resp.Body.Close()
		if resp.StatusCode != http.StatusBadRequest {
			t.Fatalf("POST %s %s = %d, want 400", path, body, resp.StatusCode)
		}
	}
	resp, err := http.Post(srv.URL+"/__control/behavior", "application/json", strings.NewReader(`{"fail_next":-1}`))
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest || m.State().Behavior().PageSize != 2 || len(m.State().Tools()) != 3 {
		t.Fatalf("a refused control call changed the mock: %d %+v", resp.StatusCode, m.State().Behavior())
	}
	if resp, err = http.Get(srv.URL + "/__received?last=-1"); err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("last=-1 = %d, want 400", resp.StatusCode)
	}
	if resp, err = http.Post(srv.URL+"/__control/oauth/token", "application/json", strings.NewReader(`{}`)); err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("minting a token outside OAuth mode = %d, want 404", resp.StatusCode)
	}
}
