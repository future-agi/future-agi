package restmcp

import (
	"encoding/json"
	"io"
	"log"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-cassette/cassette"
	"github.com/futureagi/agentcc-gateway/cmd/agentcc-cassette/record"
	"github.com/futureagi/agentcc-gateway/cmd/agentcc-cassette/replay"
)

// cn10Fixtures holds scenario 94's fixtures: calls.json (the tool calls it
// makes) and gmail.cassette.json (the vendor stand-in it replays).
const cn10Fixtures = "agentcc-gateway/scripts/scenarios/fixtures/CN10"

// scenarioCall is one entry of calls.json.
type scenarioCall struct {
	Tool      string         `json:"tool"`
	Arguments map[string]any `json:"arguments"`
}

func scenarioCalls(t *testing.T) []scenarioCall {
	t.Helper()
	data, err := os.ReadFile(repoPath(t, cn10Fixtures+"/calls.json"))
	if err != nil {
		t.Fatal(err)
	}
	var calls []scenarioCall
	if err := json.Unmarshal(data, &calls); err != nil || len(calls) == 0 {
		t.Fatalf("calls.json: %v", err)
	}
	return calls
}

// TestGmailCassetteFixture: scenario 94's vendor stand-in is a CN2 HTTP
// cassette that agentcc-cassette's check accepts, and replaying it answers
// every call of calls.json as the runtime makes it (no miss), with the
// results the scenario asserts. CN10_WRITE_CASSETTE=1 re-records it: CN2's
// recording proxy in front of the synthetic Gmail fake, with the runtime
// making calls.json's calls, scrubbed and verified by cassette.Save.
func TestGmailCassetteFixture(t *testing.T) {
	path := repoPath(t, cn10Fixtures+"/gmail.cassette.json")
	calls := scenarioCalls(t)
	gmail := compileOne(t, cn4Catalog(t), "gmail")
	if os.Getenv("CN10_WRITE_CASSETTE") == "1" {
		fake := gmailFake(t)
		proxy, err := record.NewProxy(record.ProxyOptions{Upstream: fake.URL, TestAccount: "cn10-synthetic-gmail",
			Timeout: 10 * time.Second})
		if err != nil {
			t.Fatal(err)
		}
		ps := httptest.NewServer(proxy)
		rt := startRuntime(t, []*App{gmail}, map[string]string{gmailOrigin: ps.URL})
		for i, c := range calls {
			rt.callTool(t, "/gmail/mcp", []string{testToken, otherToken}[i%2], c.Tool, c.Arguments)
		}
		ps.Close()
		if err := cassette.Save(path, proxy.Cassette(), cassette.ScrubConfig{KnownSecrets: []string{testToken, otherToken}}); err != nil {
			t.Fatalf("the recording was refused: %v", err)
		}
		t.Logf("wrote %s", path)
	}

	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := cassette.Check(data); err != nil {
		t.Fatalf("agentcc-cassette check refuses the fixture: %v", err)
	}
	c, err := cassette.Load(data)
	if err != nil {
		t.Fatal(err)
	}
	if c.Kind != cassette.KindHTTP || len(c.HTTP.Exchanges) != len(calls) {
		t.Fatalf("the fixture holds %s with %d exchanges, want an http cassette with %d", c.Kind, len(c.HTTP.Exchanges), len(calls))
	}
	player, err := replay.New([]*cassette.Cassette{c}, log.New(io.Discard, "", 0))
	if err != nil {
		t.Fatal(err)
	}
	ps := httptest.NewServer(player.Handler("/mcp"))
	defer ps.Close()
	rt := startRuntime(t, []*App{gmail}, map[string]string{gmailOrigin: ps.URL})
	var got []callResult
	for i, call := range calls {
		got = append(got, rt.callTool(t, "/gmail/mcp", []string{testToken, otherToken}[i%2], call.Tool, call.Arguments))
	}
	if misses := player.Misses(); len(misses) > 0 {
		t.Fatalf("the cassette has no reply for %v: re-record it (CN10_WRITE_CASSETTE=1)", misses)
	}
	want := []struct {
		isError bool
		text    string
	}{
		{false, `"emailAddress":`},
		{false, `"snippet":"Quarterly numbers"`},
		{false, `"labelIds":["INBOX"]`},
		{false, `"name":"Receipts"`},
		{false, `"labelIds":["SENT"]`},
		{true, "Google API error from gmail.googleapis.com: HTTP 404 Not Found: NOT_FOUND: Requested entity was not found."},
	}
	if len(want) != len(calls) {
		t.Fatalf("calls.json has %d calls; this test expects %d", len(calls), len(want))
	}
	for i, w := range want {
		if got[i].IsError != w.isError || !strings.Contains(got[i].text(), w.text) {
			t.Errorf("call %d (%s): isError %v, %q; want isError %v with %q", i, calls[i].Tool, got[i].IsError, got[i].text(), w.isError, w.text)
		}
	}
	for _, secret := range []string{testToken, otherToken, "someone@example.test"} {
		if strings.Contains(string(data), secret) {
			t.Errorf("the fixture holds %q", secret)
		}
	}
}
