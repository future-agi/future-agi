package replay

import (
	"bytes"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-cassette/cassette"
)

// saved scrubs and saves c through cassette.Save, as the recorder does, and
// loads it back: the player only ever sees cassettes in their on-disk form.
func saved(t *testing.T, c *cassette.Cassette) *cassette.Cassette {
	t.Helper()
	return savedWith(t, c, cassette.ScrubConfig{})
}

// savedWith is saved with recording-specific scrub rules.
func savedWith(t *testing.T, c *cassette.Cassette, cfg cassette.ScrubConfig) *cassette.Cassette {
	t.Helper()
	c.TestAccount, c.RecordedAt = "ci-test-account", time.Now().UTC().Format(time.RFC3339)
	path := filepath.Join(t.TempDir(), "c.cassette.json")
	if err := cassette.Save(path, c, cfg); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	out, err := cassette.Load(data)
	if err != nil {
		t.Fatal(err)
	}
	return out
}

func serve(t *testing.T, cs ...*cassette.Cassette) (*Player, *httptest.Server, *bytes.Buffer) {
	t.Helper()
	var logs bytes.Buffer
	p, err := New(cs, log.New(&logs, "", 0))
	if err != nil {
		t.Fatal(err)
	}
	srv := httptest.NewServer(p.Handler("/mcp"))
	t.Cleanup(srv.Close)
	return p, srv, &logs
}

type rpcResult struct {
	Result json.RawMessage `json:"result"`
	Error  *struct {
		Code    int    `json:"code"`
		Message string `json:"message"`
	} `json:"error"`
	miss bool
}

func callTool(t *testing.T, url, name, args string) rpcResult {
	t.Helper()
	body := `{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"` + name + `","arguments":` + args + `}}`
	resp, err := http.Post(url+"/mcp", "application/json", strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var r rpcResult
	if err := json.NewDecoder(resp.Body).Decode(&r); err != nil {
		t.Fatal(err)
	}
	r.miss = resp.Header.Get(MissHeader) == "miss"
	return r
}

func textOf(t *testing.T, r rpcResult) string {
	t.Helper()
	if r.Error != nil {
		t.Fatalf("error %d %s", r.Error.Code, r.Error.Message)
	}
	var res struct {
		Content []struct{ Text string } `json:"content"`
	}
	if err := json.Unmarshal(r.Result, &res); err != nil || len(res.Content) == 0 {
		t.Fatalf("result %s", r.Result)
	}
	return res.Content[0].Text
}

func toolExchange(name, args, text string) cassette.MCPExchange {
	return cassette.MCPExchange{Method: "tools/call",
		Params: json.RawMessage(`{"name":"` + name + `","arguments":` + args + `}`),
		Result: json.RawMessage(`{"content":[{"type":"text","text":"` + text + `"}]}`)}
}

// TestArgumentKeyedReplay: MCP replies are keyed by tool and normalized
// arguments (member order, whitespace and number spelling do not count; a
// credential-named argument is blinded), HTTP replies by method, canonical
// path, sorted query and body sha256; one key recorded twice replays in
// order and then repeats its last reply.
func TestArgumentKeyedReplay(t *testing.T) {
	t.Run("mcp", func(t *testing.T) {
		c := saved(t, &cassette.Cassette{Kind: cassette.KindMCP, MCP: &cassette.MCPRecording{ProtocolVersion: "2025-11-25",
			Exchanges: []cassette.MCPExchange{
				toolExchange("get_weather", `{"city":"Paris","units":"metric"}`, "Paris 21C"),
				toolExchange("get_weather", `{"city":"Tokyo","units":"metric"}`, "Tokyo 18C"),
				toolExchange("get_weather", `{"city":"Paris","units":"imperial"}`, "Paris 70F"),
				toolExchange("get_weather", `{"city":"Lyon","api_key":"recorded-credential-value"}`, "Lyon 19C"),
				toolExchange("count", `{"n":1,"tags":["a","b"]}`, "one"),
				toolExchange("inbox", `{}`, "empty"),
				toolExchange("inbox", `{}`, "one new message"),
			}}})
		_, srv, _ := serve(t, c)
		// Called out of recorded order, so only argument keying can pass.
		for _, tc := range []struct{ name, args, want string }{
			{"get_weather", `{"city":"Paris","units":"imperial"}`, "Paris 70F"},
			{"get_weather", `{"city":"Lyon","api_key":"a-live-credential-the-cassette-never-held"}`, "Lyon 19C"},
			{"get_weather", "{ \"city\" : \"Tokyo\",\n \"units\" : \"metric\" }", "Tokyo 18C"},
			{"get_weather", `{"units":"metric","city":"Paris"}`, "Paris 21C"},
			{"count", `{"tags":["a","b"],"n":1.0}`, "one"},
			{"inbox", `{}`, "empty"},
			{"inbox", `{}`, "one new message"},
			{"inbox", `{}`, "one new message"},
		} {
			if got := textOf(t, callTool(t, srv.URL, tc.name, tc.args)); got != tc.want {
				t.Errorf("%s %s = %q, want %q", tc.name, tc.args, got, tc.want)
			}
		}
		for _, args := range []string{`{"city":"Berlin","units":"metric"}`, `{"city":"Paris"}`, `{"tags":["b","a"],"n":1}`} {
			if r := callTool(t, srv.URL, "get_weather", args); r.Error == nil || r.Error.Code != codeMiss || !r.miss {
				t.Errorf("args %s did not miss: %+v", args, r)
			}
		}
	})
	t.Run("http", func(t *testing.T) {
		jsonCT := http.Header{"Content-Type": {"application/json"}}
		resp := func(body string) cassette.HTTPResponse {
			return cassette.HTTPResponse{Status: 200, Headers: jsonCT, Body: &cassette.Body{JSON: json.RawMessage(body)}}
		}
		c := saved(t, &cassette.Cassette{Kind: cassette.KindHTTP, HTTP: &cassette.HTTPRecording{Exchanges: []cassette.HTTPExchange{
			{Request: cassette.HTTPRequest{Method: "GET", Path: "/v1/items", Query: "a=1&b=2"}, Response: resp(`{"r":"list"}`)},
			{Request: cassette.HTTPRequest{Method: "DELETE", Path: "/v1/items", Query: "a=1&b=2"}, Response: resp(`{"r":"deleted"}`)},
			{Request: cassette.HTTPRequest{Method: "GET", Path: "/v1/search", Query: "q=x&api_key=recorded-credential-value"}, Response: resp(`{"r":"search"}`)},
			{Request: cassette.HTTPRequest{Method: "POST", Path: "/v1/items", Headers: jsonCT,
				Body: &cassette.Body{JSON: json.RawMessage(`{"x":1,"y":[1,2]}`)}}, Response: resp(`{"r":"created"}`)},
			{Request: cassette.HTTPRequest{Method: "GET", Path: "/files/a%2Bb"}, Response: resp(`{"r":"file"}`)},
		}}})
		_, srv, _ := serve(t, c)
		for _, tc := range []struct{ method, path, body, want string }{
			{"GET", "/v1/items?b=2&a=1", "", `{"r":"list"}`},
			{"DELETE", "/v1/items?a=1&b=2", "", `{"r":"deleted"}`},
			{"GET", "/v1/search?api_key=a-live-credential&q=x", "", `{"r":"search"}`},
			{"POST", "/v1/items", `{"y":[1,2],"x":1.0}`, `{"r":"created"}`},
			{"GET", "/files/a+b", "", `{"r":"file"}`},
		} {
			code, got, miss := do(t, tc.method, srv.URL+tc.path, tc.body)
			if code != 200 || miss || compact(t, got) != tc.want {
				t.Errorf("%s %s = %d %s (miss %v), want %s", tc.method, tc.path, code, got, miss, tc.want)
			}
		}
		for _, tc := range []struct{ method, path, body string }{
			{"GET", "/v1/items?a=1&b=3", ""}, {"PUT", "/v1/items?a=1&b=2", ""}, {"POST", "/v1/items", `{"x":2,"y":[1,2]}`},
			{"GET", "/v1/items", ""}, {"GET", "/v1/items/", ""},
		} {
			if code, _, miss := do(t, tc.method, srv.URL+tc.path, tc.body); code != http.StatusNotImplemented || !miss {
				t.Errorf("%s %s = %d (miss %v), want a loud miss", tc.method, tc.path, code, miss)
			}
		}
	})
}

func do(t *testing.T, method, url, body string) (int, string, bool) {
	t.Helper()
	req, err := http.NewRequest(method, url, strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	if body != "" {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(resp.Body)
	return resp.StatusCode, string(data), resp.Header.Get(MissHeader) == "miss"
}

func compact(t *testing.T, s string) string {
	var buf bytes.Buffer
	if err := json.Compact(&buf, []byte(s)); err != nil {
		return s
	}
	return buf.String()
}

// TestPlayerRefusesAmbiguousCassettes: two cassettes that record one key
// with different replies cannot replay together; the same reply twice can.
func TestPlayerRefusesAmbiguousCassettes(t *testing.T) {
	a := saved(t, &cassette.Cassette{Kind: cassette.KindMCP, MCP: &cassette.MCPRecording{
		Exchanges: []cassette.MCPExchange{toolExchange("t", `{}`, "A")}}})
	b := saved(t, &cassette.Cassette{Kind: cassette.KindMCP, MCP: &cassette.MCPRecording{
		Exchanges: []cassette.MCPExchange{toolExchange("t", `{}`, "B")}}})
	if _, err := New([]*cassette.Cassette{a, b}, log.New(io.Discard, "", 0)); err == nil {
		t.Fatal("two different replies for one key were accepted")
	}
	if _, err := New([]*cassette.Cassette{a, a}, log.New(io.Discard, "", 0)); err != nil {
		t.Fatalf("the same reply twice: %v", err)
	}
	h := saved(t, &cassette.Cassette{Kind: cassette.KindHTTP, HTTP: &cassette.HTTPRecording{}})
	if _, err := New([]*cassette.Cassette{a, h}, log.New(io.Discard, "", 0)); err == nil {
		t.Fatal("an MCP and an HTTP cassette were accepted together")
	}
}
