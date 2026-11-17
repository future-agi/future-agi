package credtemplate_test

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp/credtemplate"
)

// TestHeaderPrefixRule: a header part's prefix is printable characters
// other than quotes, with at most one space inside and at most one at the
// end (connector-manifest/v1 credentialPrefix), so PagerDuty's
// "Token token=" is placed and a prefix that would split or quote the header
// value is refused, naming the prefix.
func TestHeaderPrefixRule(t *testing.T) {
	const u = "https://h.example.com/mcp"
	compile := func(prefix string) error {
		_, err := credtemplate.Compile(u, credtemplate.Part{In: credtemplate.InHeader, Name: "Authorization", Value: "v-1", Prefix: prefix})
		return err
	}
	for _, ok := range []string{"Bearer ", "Basic ", "Token", "Token token=", "Token token= ", "Key="} {
		if err := compile(ok); err != nil {
			t.Errorf("prefix %q refused: %v", ok, err)
		}
	}
	for _, bad := range []string{" x", " ", "Bearer  ", "Token  token=", "Token token=  ", "a b c", "Token\ttoken=",
		"Token token=\r\n", "Token\x7f", `"Bearer" `, "Token token='", "Tokén "} {
		var ce *credtemplate.Error
		if err := compile(bad); !errors.As(err, &ce) || ce.Field != "parts[0].prefix" {
			t.Errorf("prefix %q: err %v, want a refusal on parts[0].prefix", bad, err)
		}
	}
}

// TestTokenPrefixPlacedVerbatimAndNeverLogged: a key whose declared prefix is
// "Token token=" goes upstream as "Authorization: Token token=<key>", on
// every request to the server's origin, and the template prints, encodes,
// logs and scrubs without the key, the prefix kept.
func TestTokenPrefixPlacedVerbatimAndNeverLogged(t *testing.T) {
	const key = "pd-test-value-7c41"
	srv, rec := serve(t, http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	part := credtemplate.Part{In: credtemplate.InHeader, Name: "Authorization", Value: key, Prefix: "Token token="}
	tpl, err := credtemplate.Compile(srv.URL+"/mcp", part)
	if err != nil {
		t.Fatalf("Compile: %v", err)
	}
	rt := tpl.RoundTripper(http.DefaultTransport)
	get(t, rt, srv.URL+"/mcp")
	get(t, rt, srv.URL+"/mcp?cursor=2")
	reqs := rec.all()
	if len(reqs) != 2 {
		t.Fatalf("%d requests reached the server, want 2", len(reqs))
	}
	for i, r := range reqs {
		if got := r.header.Values("Authorization"); len(got) != 1 || got[0] != "Token token="+key {
			t.Errorf("request %d: Authorization carries the key %t with %d value(s), want Token token=<key>", i, len(leaks(fmt.Sprint(got), key)) > 0, len(got))
		}
		if carrying := headerCarrying(r.header, key); len(carrying) != 1 {
			t.Errorf("request %d: %d headers carry the key, want only Authorization", i, len(carrying))
		}
		if len(leaks(r.rawQuery, key)) > 0 {
			t.Errorf("request %d: the query carries the key", i)
		}
	}

	var text, js bytes.Buffer
	slog.New(slog.NewTextHandler(&text, &slog.HandlerOptions{Level: slog.LevelDebug})).Debug("cred", "template", tpl, "part", part)
	slog.New(slog.NewJSONHandler(&js, &slog.HandlerOptions{Level: slog.LevelDebug})).Debug("cred", "template", tpl, "part", part)
	raw, _ := json.Marshal(map[string]any{"template": tpl, "part": part})
	for what, out := range map[string]string{
		"%v": fmt.Sprintf("%v %v", tpl, part), "%+v": fmt.Sprintf("%+v %+v", tpl, part), "%#v": fmt.Sprintf("%#v %#v", tpl, part),
		"json": string(raw), "slog text": text.String(), "slog json": js.String(),
	} {
		if l := leaks(out, key); len(l) > 0 {
			t.Errorf("%s carries the key", what)
		}
	}
	if got := tpl.Scrub("Authorization: Token token=" + key); got != "Authorization: Token token=[redacted]" {
		t.Errorf("Scrub kept the key %t", len(leaks(got, key)) > 0)
	}
}
