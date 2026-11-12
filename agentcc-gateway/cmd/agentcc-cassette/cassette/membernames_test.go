package cassette

import (
	"encoding/json"
	"errors"
	"regexp"
	"strings"
	"testing"
)

// TestScrubJSONMemberNames: an email, an id, a uuid, a literal or the
// recording's own credential used as a JSON member name is replaced like
// the same value anywhere else: with the one placeholder the value got,
// in HTTP bodies, MCP results and JSON inside text, keeping member order.
func TestScrubJSONMemberNames(t *testing.T) {
	t.Run("an email as a member name (Calendar freeBusy)", func(t *testing.T) {
		c := newHTTPCassette(HTTPExchange{
			Request: HTTPRequest{Method: "POST", Path: "/calendar/v3/freeBusy", Headers: hdr("Content-Type", "application/json"),
				Body: &Body{JSON: raw(`{"timeMin":"2026-10-19T00:00:00Z","items":[{"id":"` + aliceEmail + `"}]}`)}},
			Response: HTTPResponse{Status: 200, Headers: hdr("Content-Type", "application/json"),
				Body: &Body{JSON: raw(`{"kind":"calendar#freeBusy","calendars":{"` + aliceEmail + `":{"busy":[]},"` +
					bobEmail + `":{"errors":[{"reason":"notFound"}]}}}`)}},
		})
		text := saveOK(t, c, ScrubConfig{})
		assertAbsent(t, text, aliceEmail, bobEmail)
		got := loadSaved(t, text).HTTP.Exchanges[0]
		var req struct {
			Items []struct{ ID string } `json:"items"`
		}
		if err := json.Unmarshal(got.Request.Body.JSON, &req); err != nil || len(req.Items) != 1 {
			t.Fatalf("request body %s: %v", got.Request.Body.JSON, err)
		}
		resp := compactJSON(t, got.Response.Body.JSON)
		want := regexp.MustCompile(`^\{"kind":"calendar#freeBusy","calendars":\{"(user-[0-9]+@example\.com)":\{"busy":\[\]\},"user-[0-9]+@example\.com":\{"errors"`)
		m := want.FindStringSubmatch(resp)
		if m == nil {
			t.Fatalf("response = %s, want both member names scrubbed in their order", resp)
		}
		if m[1] != req.Items[0].ID {
			t.Errorf("the requested calendar is %q but its member name is %q: want one placeholder", req.Items[0].ID, m[1])
		}
	})
	t.Run("an id as a member name", func(t *testing.T) {
		const slackUser = "U0123ABCDEF"
		c := newMCPCassette(MCPExchange{Method: "tools/call", Params: raw(`{"name":"users_list","arguments":{}}`),
			Result: raw(`{"structuredContent":{"members":[{"id":"` + slackUser + `"}],"profiles":{"` + slackUser + `":{"title":"QA"}}}}`)})
		text := saveOK(t, c, ScrubConfig{})
		assertAbsent(t, text, slackUser)
		r := compactJSON(t, loadSaved(t, text).MCP.Exchanges[0].Result)
		if !regexp.MustCompile(`"members":\[\{"id":"(scrubbed-id-[0-9]+)"\}\],"profiles":\{"(scrubbed-id-[0-9]+)":\{"title":"QA"\}\}`).MatchString(r) {
			t.Fatalf("result = %s", r)
		}
		ids := regexp.MustCompile(`scrubbed-id-[0-9]+`).FindAllString(r, -1)
		if len(ids) != 2 || ids[0] != ids[1] {
			t.Errorf("the id and the member name got different placeholders: %v", ids)
		}
	})
	t.Run("the recording's credential, a uuid and a literal as member names", func(t *testing.T) {
		known := fakeToken("recording-credential")
		c := newMCPCassette(MCPExchange{Method: "tools/call", Params: raw(`{"name":"whoami","arguments":{}}`),
			Result: raw(`{"structuredContent":{"sessions":{"` + known + `":{"ok":true}},"workspaces":{"` + workspaceID +
				`":{"plan":"pro"}},"tenants":{"` + workspace + `":{"seats":3}}}}`)})
		text := saveOK(t, c, ScrubConfig{KnownSecrets: []string{known}, Literals: []string{workspace}})
		assertAbsent(t, text, known, workspaceID, workspace)
		r := compactJSON(t, loadSaved(t, text).MCP.Exchanges[0].Result)
		for _, want := range []string{`"sessions":{"scrubbed-token-1":{"ok":true}}`, `"workspaces":{"` + placeholderUUIDPrefix,
			`"tenants":{"scrubbed-literal-1":{"seats":3}}`} {
			if !strings.Contains(r, want) {
				t.Errorf("result %s lacks %s", r, want)
			}
		}
	})
	t.Run("an email as a member name in JSON inside text", func(t *testing.T) {
		inner, _ := json.Marshal(`{"` + bobEmail + `":{"busy":[]}}`)
		c := newMCPCassette(MCPExchange{Method: "tools/call", Params: raw(`{"name":"freebusy","arguments":{"calendars":["primary"]}}`),
			Result: raw(`{"content":[{"type":"text","text":` + string(inner) + `}]}`)})
		text := saveOK(t, c, ScrubConfig{})
		assertAbsent(t, text, bobEmail)
		var res struct {
			Content []struct{ Text string } `json:"content"`
		}
		_ = json.Unmarshal(loadSaved(t, text).MCP.Exchanges[0].Result, &res)
		if len(res.Content) != 1 || !regexp.MustCompile(`^\{"user-[0-9]+@example\.com":\{"busy":\[\]\}\}$`).MatchString(res.Content[0].Text) {
			t.Errorf("content = %+v", res.Content)
		}
	})
}

// TestCheckRefusesSensitiveMemberNames: check, run on a committed cassette
// edited by hand, refuses an email or a credential shape used as a member
// name, escaped or not, and its message names rule and place, never the
// name.
func TestCheckRefusesSensitiveMemberNames(t *testing.T) {
	c := newHTTPCassette(HTTPExchange{Request: HTTPRequest{Method: "GET", Path: "/calendar/v3/freeBusy"},
		Response: HTTPResponse{Status: 200, Headers: hdr("Content-Type", "application/json"),
			Body: &Body{JSON: raw(`{"calendars":{"primary":{"busy":[]}}}`)}}})
	good := saveOK(t, c, ScrubConfig{})
	pat := fakeShape("github-pat")
	for name, tc := range map[string]struct{ to, under, rule string }{
		"an email":                 {`"` + aliceEmail + `"`, "", "email"},
		"an escaped email":         {`"` + jsonEscapeFirst(aliceEmail, '@') + `"`, "", "email"},
		"an escaped github token":  {`"` + jsonEscapeFirst(pat, '_') + `"`, "", "github-pat"},
		"a credential shape as is": {`"` + pat + `"`, "", "github-pat"},
		"a finding under an email": {`"` + aliceEmail + `"`, `"note": "` + jsonEscapeFirst(pat, '_') + `"`, "github-pat"},
	} {
		t.Run(name, func(t *testing.T) {
			edited := strings.Replace(good, `"primary"`, tc.to, 1)
			if tc.under != "" {
				edited = strings.Replace(edited, `"busy": []`, tc.under, 1)
			}
			if edited == good || tc.under != "" && !strings.Contains(edited, tc.under) {
				t.Fatal("the edit did not apply")
			}
			err := Check([]byte(edited))
			var refused *RefusedError
			if !errors.As(err, &refused) {
				t.Fatalf("Check = %v, want a refusal", err)
			}
			msg := refused.Error()
			if !strings.Contains(msg, tc.rule) {
				t.Errorf("refusal %q does not name %s", msg, tc.rule)
			}
			for _, v := range []string{"alice", "corp-test", pat[4:20]} {
				if strings.Contains(msg, v) {
					t.Errorf("the refusal carries the member name: %q", msg)
				}
			}
		})
	}
}

// TestVerifyNamesASurvivingMemberNameWithoutIt: the backstop refuses a
// member name the scrubber collected but did not replace, and says where
// without saying what.
func TestVerifyNamesASurvivingMemberNameWithoutIt(t *testing.T) {
	const slackUser = "U0123ABCDEF"
	ph := newPlaceholders()
	ph.add(classID, slackUser)
	ph.assign()
	c := newMCPCassette(MCPExchange{Method: "tools/call", Params: raw(`{"name":"users_list","arguments":{}}`),
		Result: raw(`{"structuredContent":{"profiles":{"` + slackUser + `":{"title":"QA"}}}}`)})
	c.Format, c.Notice = Format, Notice
	data, err := marshal(c)
	if err != nil {
		t.Fatal(err)
	}
	err = verify(data, ph, nil)
	var refused *RefusedError
	if !errors.As(err, &refused) {
		t.Fatalf("verify = %v, want a refusal", err)
	}
	if msg := refused.Error(); !strings.Contains(msg, "scrub-incomplete:id") || strings.Contains(msg, slackUser) {
		t.Errorf("refusal %q: want scrub-incomplete:id and never the name", msg)
	}
}

// TestVerifyLeavesCassetteNamesAlone: a literal that spells one of the
// cassette's own names (a field, a header, a placeholder class) is scrubbed
// from recorded member names but never refuses the cassette's structure.
func TestVerifyLeavesCassetteNamesAlone(t *testing.T) {
	c := newHTTPCassette(HTTPExchange{
		Request: HTTPRequest{Method: "GET", Path: "/v1/me", Headers: hdr("Accept", "application/json", "Authorization", "Bearer "+fakeToken("bearer"))},
		Response: HTTPResponse{Status: 200, Headers: hdr("Content-Type", "application/json"),
			Body: &Body{JSON: raw(`{"status":"ok","method":"sso"}`)}},
	})
	text := saveOK(t, c, ScrubConfig{Literals: []string{"status", "method", "Accept", "token"}})
	got := loadSaved(t, text).HTTP.Exchanges[0]
	if got.Request.Method != "GET" || got.Response.Status != 200 || got.Request.Headers.Get("Accept") != "application/json" {
		t.Errorf("the cassette's own fields changed: %+v", got)
	}
	if b := compactJSON(t, got.Response.Body.JSON); !regexp.MustCompile(`^\{"scrubbed-literal-[0-9]+":"ok","scrubbed-literal-[0-9]+":"sso"\}$`).MatchString(b) {
		t.Errorf("recorded member names that are literals were not scrubbed: %s", b)
	}
}
