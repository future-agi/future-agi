package cassette

import (
	"errors"
	"regexp"
	"strings"
	"testing"
)

// TestVerifyLeavesCassetteValuesAlone: a literal spelled inside one of the
// cassette's own values (its format, its notice, an MCP method, the names
// in its rules) never refuses the save: the scrubber does not write those,
// so they hold no recorded value. Recorded text with the literal is still
// scrubbed.
func TestVerifyLeavesCassetteValuesAlone(t *testing.T) {
	c := newMCPCassette(MCPExchange{Method: "tools/call", Params: raw(`{"name":"search","arguments":{"q":"x"}}`),
		Result: raw(`{"content":[{"type":"text","text":"vendor cassette with tools"}]}`)})
	c.TestAccount = "ci-account"
	text := saveOK(t, c, ScrubConfig{Literals: []string{"vendor", "cassette", "tools"}})
	if r := compactJSON(t, loadSaved(t, text).MCP.Exchanges[0].Result); !regexp.MustCompile(
		`"text":"scrubbed-literal-[0-9]+ scrubbed-literal-[0-9]+ with scrubbed-literal-[0-9]+"`).MatchString(r) {
		t.Errorf("recorded text with the literals = %s", r)
	}
	if got := loadSaved(t, text); got.MCP.Exchanges[0].Method != "tools/call" || got.Notice != Notice || got.Format != Format {
		t.Errorf("the cassette's own values changed: %q %q %q", got.MCP.Exchanges[0].Method, got.Notice, got.Format)
	}
}

// TestRefusalPathShowsCassetteFieldNames: a refusal names the place by the
// cassette's own field names even when a literal spells one of them (the
// test account label holding the literal is refused at $.test_account).
func TestRefusalPathShowsCassetteFieldNames(t *testing.T) {
	c := newMCPCassette(MCPExchange{Method: "tools/call", Params: raw(`{"name":"build","arguments":{}}`), Result: raw(`{"ok":true}`)})
	refused := saveRefused(t, c, ScrubConfig{Literals: []string{"test"}}) // the label is ci-test-account
	if msg := refused.Error(); !strings.Contains(msg, "scrub-incomplete:literal at $.test_account") || strings.Contains(msg, "<member") {
		t.Errorf("refusal %q: want the place named $.test_account", msg)
	}
}

// TestScrubLiteralInMemberNameStandsAlone: in a recorded member name, a
// literal is replaced only where it stands alone (no letter or digit
// touching it), as ids are, so a vendor field that merely contains it
// (latest, contest) keeps its name; in a value it is replaced anywhere.
// verify agrees: a name that holds the literal inside a word is no survivor.
func TestScrubLiteralInMemberNameStandsAlone(t *testing.T) {
	c := newMCPCassette(MCPExchange{Method: "tools/call", Params: raw(`{"name":"build","arguments":{"contest_name":"spring"}}`),
		Result: raw(`{"structuredContent":{"latest":1,"test_mode":true,"contest":"x","test":"y","note":"latest build"}}`)})
	c.TestAccount = "ci-account"
	text := saveOK(t, c, ScrubConfig{Literals: []string{"test"}})
	x := loadSaved(t, text).MCP.Exchanges[0]
	want := `{"structuredContent":{"latest":1,"scrubbed-literal-1_mode":true,"contest":"x","scrubbed-literal-1":"y","note":"lascrubbed-literal-1 build"}}`
	if r := compactJSON(t, x.Result); r != want {
		t.Errorf("result = %s\nwant      %s", r, want)
	}
	if p := compactJSON(t, x.Params); p != `{"name":"build","arguments":{"contest_name":"spring"}}` {
		t.Errorf("params = %s, want the argument name kept", p)
	}
	t.Run("verify refuses a standalone literal left in a name", func(t *testing.T) {
		ph := newPlaceholders()
		ph.add(classLiteral, "test")
		ph.assign()
		for name, refused := range map[string]bool{"latest": false, "contest_id": false, "test_mode": true, "test": true} {
			c := newMCPCassette(MCPExchange{Method: "tools/call", Params: raw(`{"name":"build","arguments":{}}`),
				Result: raw(`{"structuredContent":{"` + name + `":1}}`)})
			c.TestAccount, c.Format, c.Notice = "ci-account", Format, Notice
			data, err := marshal(c)
			if err != nil {
				t.Fatal(err)
			}
			var r *RefusedError
			if got := errors.As(verify(data, ph, nil), &r); got != refused {
				t.Errorf("member %q: refused %v, want %v", name, got, refused)
			}
		}
	})
}
