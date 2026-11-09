package credtemplate_test

import (
	"errors"
	"net/http"
	"strconv"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp/credtemplate"
)

// TestPartsOfPushedSecret: the pushed secret (tenant.MCPCredentialSecret
// with its credential's header) maps to its parts. A header placement
// follows the gateway's rules as they are; query and url_variable carry
// the raw value under their name; further parts follow; a secret whose
// fields contradict each other, or that the gateway could not place exactly
// as stated, is refused naming the field (AM-C9, AM-C20 item 2).
func TestPartsOfPushedSecret(t *testing.T) {
	const v = "v-1"
	hdr := func(name, value, prefix string) credtemplate.Part {
		return credtemplate.Part{In: credtemplate.InHeader, Name: name, Value: value, Prefix: prefix}
	}
	for _, tc := range []struct {
		name string
		sec  credtemplate.Secret
		want []credtemplate.Part
	}{
		{"legacy bearer", credtemplate.Secret{Type: "bearer", Header: "Authorization", Value: v},
			[]credtemplate.Part{hdr("Authorization", v, "Bearer ")}},
		{"legacy api_key", credtemplate.Secret{Type: "api_key", Header: "X-Key", Value: v},
			[]credtemplate.Part{hdr("X-Key", v, "")}},
		{"legacy api_key in its reference's header", credtemplate.Secret{Type: "api_key", ReferenceHeader: "X-Ref", Value: v},
			[]credtemplate.Part{hdr("X-Ref", v, "")}},
		{"legacy, a name equal to the header", credtemplate.Secret{Type: "api_key", Header: "X-Key", Name: "x-key", Value: v},
			[]credtemplate.Part{hdr("X-Key", v, "")}},
		{"in header", credtemplate.Secret{Type: "api_key", Header: "x-key", In: "header", Name: "X-Key", Value: v},
			[]credtemplate.Part{hdr("X-Key", v, "")}},
		{"in header with no header field", credtemplate.Secret{Type: "api_key", In: "header", Name: "X-Key", Value: v},
			[]credtemplate.Part{hdr("X-Key", v, "")}},
		{"in header with no name is its header", credtemplate.Secret{Type: "api_key", Header: "X-Key", In: "header", Value: v},
			[]credtemplate.Part{hdr("X-Key", v, "")}},
		{"in header with no name or header is a bearer, as before", credtemplate.Secret{Type: "api_key", In: "header", Value: v},
			[]credtemplate.Part{hdr("Authorization", v, "Bearer ")}},
		{"in header, Authorization is a bearer", credtemplate.Secret{Type: "api_key", In: "header", Name: "authorization", Value: v},
			[]credtemplate.Part{hdr("Authorization", v, "Bearer ")}},
		{"in query", credtemplate.Secret{Type: "api_key", In: "query", Name: "browserbaseApiKey", Value: v},
			[]credtemplate.Part{{In: credtemplate.InQuery, Name: "browserbaseApiKey", Value: v}}},
		{"in query, the reference header is not the placement", credtemplate.Secret{Type: "api_key", ReferenceHeader: "X-Ref", In: "query", Name: "k", Value: v},
			[]credtemplate.Part{{In: credtemplate.InQuery, Name: "k", Value: v}}},
		{"in url_variable", credtemplate.Secret{Type: "api_key", In: "url_variable", Name: "token", Value: v},
			[]credtemplate.Part{{In: credtemplate.InURLVariable, Name: "token", Value: v}}},
		{"two secret headers (Datadog)", credtemplate.Secret{Type: "api_key", In: "header", Name: "DD-API-KEY", Value: v,
			Parts: []credtemplate.SecretPart{{In: "header", Name: "DD-APPLICATION-KEY", Value: "v-2"}}},
			[]credtemplate.Part{hdr("DD-API-KEY", v, ""), hdr("DD-APPLICATION-KEY", "v-2", "")}},
		{"a legacy header with a query part", credtemplate.Secret{Type: "bearer", Value: v,
			Parts: []credtemplate.SecretPart{{In: "query", Name: "account", Value: "v-2"}, {In: "url_variable", Name: "site", Value: "v-3"}}},
			[]credtemplate.Part{hdr("Authorization", v, "Bearer "), {In: credtemplate.InQuery, Name: "account", Value: "v-2"},
				{In: credtemplate.InURLVariable, Name: "site", Value: "v-3"}}},
	} {
		got, err := credtemplate.Parts(tc.sec)
		if err != nil {
			t.Errorf("%s: %v", tc.name, err)
			continue
		}
		if len(got) != len(tc.want) {
			t.Errorf("%s: %d parts, want %d", tc.name, len(got), len(tc.want))
			continue
		}
		for i := range got {
			if got[i] != tc.want[i] {
				t.Errorf("%s: part %d is %s, want %s", tc.name, i, describe(got[i]), describe(tc.want[i]))
			}
		}
	}

	eight := make([]credtemplate.SecretPart, 8)
	for i := range eight {
		eight[i] = credtemplate.SecretPart{In: "header", Name: "X-P" + strconv.Itoa(i), Value: v}
	}
	for _, tc := range []struct {
		name  string
		sec   credtemplate.Secret
		field string // the error's field
	}{
		{"an unknown type", credtemplate.Secret{Type: "basic", Header: "X-Key", Value: v}, "type"},
		{"an unknown in", credtemplate.Secret{Type: "api_key", In: "cookie", Name: "c", Value: v}, "in"},
		{"a legacy name that is not its header", credtemplate.Secret{Type: "api_key", Header: "X-Key", Name: "X-Other", Value: v}, "name"},
		{"a legacy bearer named for another header", credtemplate.Secret{Type: "bearer", Header: "X-Key", Name: "X-Key", Value: v}, "name"},
		{"in header whose header is another name", credtemplate.Secret{Type: "api_key", Header: "X-Other", In: "header", Name: "X-Key", Value: v}, "header"},
		{"in header whose reference names another header", credtemplate.Secret{Type: "api_key", ReferenceHeader: "X-Other", In: "header", Name: "X-Key", Value: v}, "name"},
		{"in header, a bearer outside Authorization", credtemplate.Secret{Type: "bearer", In: "header", Name: "X-Token", Value: v}, "name"},
		{"in query without a name", credtemplate.Secret{Type: "api_key", In: "query", Value: v}, "name"},
		{"in query with a header too", credtemplate.Secret{Type: "api_key", Header: "Authorization", In: "query", Name: "k", Value: v}, "header"},
		{"in query, a bearer", credtemplate.Secret{Type: "bearer", In: "query", Name: "k", Value: v}, "type"},
		{"in url_variable with a header too", credtemplate.Secret{Type: "api_key", Header: "X-Key", In: "url_variable", Name: "k", Value: v}, "header"},
		{"in url_variable, a bearer", credtemplate.Secret{Type: "bearer", In: "url_variable", Name: "k", Value: v}, "type"},
		{"a query name that is not a queryName", credtemplate.Secret{Type: "api_key", In: "query", Name: "a b", Value: v}, "name"},
		{"a variable name that is not a varName", credtemplate.Secret{Type: "api_key", In: "url_variable", Name: "1x", Value: v}, "name"},
		{"a reserved legacy header", credtemplate.Secret{Type: "api_key", Header: "Content-Type", Value: v}, "header"},
		{"a reserved header by name", credtemplate.Secret{Type: "api_key", In: "header", Name: "Mcp-Session-Id", Value: v}, "name"},
		{"no value", credtemplate.Secret{Type: "api_key", Header: "X-Key"}, "value"},
		{"a value with surrounding space", credtemplate.Secret{Type: "api_key", Header: "X-Key", Value: " v"}, "value"},
		{"a value with a newline", credtemplate.Secret{Type: "api_key", Header: "X-Key", Value: "v\nX-Evil: 1"}, "value"},
		{"a value over 8192 characters", credtemplate.Secret{Type: "api_key", Header: "X-Key", Value: strings.Repeat("v", 8193)}, "value"},
		{"a part with an unknown in", credtemplate.Secret{Type: "api_key", In: "header", Name: "A", Value: v,
			Parts: []credtemplate.SecretPart{{In: "body", Name: "b", Value: v}}}, "parts[0].in"},
		{"a part without a name", credtemplate.Secret{Type: "api_key", In: "header", Name: "A", Value: v,
			Parts: []credtemplate.SecretPart{{In: "query", Value: v}}}, "parts[0].name"},
		{"a part with no value", credtemplate.Secret{Type: "api_key", In: "header", Name: "A", Value: v,
			Parts: []credtemplate.SecretPart{{In: "header", Name: "B"}}}, "parts[0].value"},
		{"a part in a reserved header", credtemplate.Secret{Type: "api_key", In: "header", Name: "A", Value: v,
			Parts: []credtemplate.SecretPart{{In: "header", Name: "B", Value: v}, {In: "header", Name: "Mcp-Param-Region", Value: v}}}, "parts[1].name"},
		{"a part in Authorization", credtemplate.Secret{Type: "api_key", In: "header", Name: "A", Value: v,
			Parts: []credtemplate.SecretPart{{In: "header", Name: "authorization", Value: v}}}, "parts[0].name"},
		{"more than 7 parts", credtemplate.Secret{Type: "api_key", In: "header", Name: "A", Value: v, Parts: eight}, "parts"},
	} {
		_, err := credtemplate.Parts(tc.sec)
		var ce *credtemplate.Error
		if !errors.As(err, &ce) || ce.Field != tc.field {
			t.Errorf("%s: err %v, want a *credtemplate.Error on field %q", tc.name, err, tc.field)
		}
	}
}

// TestCompileRefusesWhatCannotBePlaced: Compile refuses a URL or a part the
// template could not place safely, naming the part, never its value.
func TestCompileRefusesWhatCannotBePlaced(t *testing.T) {
	const u = "https://h.example.com/mcp"
	part := func(in credtemplate.In, name, value string) credtemplate.Part {
		return credtemplate.Part{In: in, Name: name, Value: value}
	}
	for _, tc := range []struct {
		name  string
		raw   string
		parts []credtemplate.Part
	}{
		{"no parts", u, nil},
		{"more than 8 parts", u, []credtemplate.Part{
			part("header", "A", "1"), part("header", "B", "1"), part("header", "C", "1"), part("header", "D", "1"),
			part("header", "E", "1"), part("header", "F", "1"), part("header", "G", "1"), part("header", "H", "1"), part("header", "I", "1")}},
		{"a URL that does not parse", "https://h.example.com/%zz", []credtemplate.Part{part("header", "X-Key", "1")}},
		{"a URL that is not http(s)", "ftp://h.example.com/mcp", []credtemplate.Part{part("header", "X-Key", "1")}},
		{"a URL with no host", "https:///mcp", []credtemplate.Part{part("header", "X-Key", "1")}},
		{"a URL with user info", "https://u:p@h.example.com/mcp", []credtemplate.Part{part("header", "X-Key", "1")}},
		{"a URL with a fragment", u + "#f", []credtemplate.Part{part("header", "X-Key", "1")}},
		{"a placeholder nothing fills", u + "/{other}", []credtemplate.Part{part("header", "X-Key", "1")}},
		{"an unknown in", u, []credtemplate.Part{part("cookie", "c", "1")}},
		{"an empty value", u, []credtemplate.Part{part("header", "X-Key", "")}},
		{"a control character in a value", u, []credtemplate.Part{part("header", "X-Key", "a\r\nX-Evil: 1")}},
		{"a DEL in a value", u, []credtemplate.Part{part("query", "k", "a\x7f")}},
		{"a header name that is not a token", u, []credtemplate.Part{part("header", "X Key", "1")}},
		{"a header name over 64 characters", u, []credtemplate.Part{part("header", strings.Repeat("x", 65), "1")}},
		{"a header the transport sets: Content-Type", u, []credtemplate.Part{part("header", "content-type", "1")}},
		{"a header the transport sets: Mcp-Session-Id", u, []credtemplate.Part{part("header", "Mcp-Session-Id", "1")}},
		{"a header the transport sets: MCP-Protocol-Version", u, []credtemplate.Part{part("header", "mcp-protocol-version", "1")}},
		{"a header the transport sets: Host", u, []credtemplate.Part{part("header", "Host", "1")}},
		{"a header the transport sets: an Mcp-Param- header", u, []credtemplate.Part{part("header", "Mcp-Param-Region", "1")}},
		{"a header the transport sets: Mcp-Method", u, []credtemplate.Part{part("header", "Mcp-Method", "1")}},
		{"a header the transport sets: Accept-Encoding", u, []credtemplate.Part{part("header", "accept-encoding", "1")}},
		{"a header of the gateway's namespace: X-Agentcc-Agent-Depth", u, []credtemplate.Part{part("header", "X-Agentcc-Agent-Depth", "1")}},
		{"a header of the gateway's namespace: X-Omega-Run-Id", u, []credtemplate.Part{part("header", "x-omega-run-id", "1")}},
		{"a header the gateway's tracing sets: traceparent", u, []credtemplate.Part{part("header", "traceparent", "1")}},
		{"a header prefix that is not printable", u, []credtemplate.Part{{In: "header", Name: "X-Key", Value: "1", Prefix: "Bearer\t"}}},
		{"a prefix on a query part", u, []credtemplate.Part{{In: "query", Name: "k", Value: "1", Prefix: "Bearer "}}},
		{"a query name with a space", u, []credtemplate.Part{part("query", "a b", "1")}},
		{"a query name with '='", u, []credtemplate.Part{part("query", "a=b", "1")}},
		{"a query parameter the URL already sets", u + "?region=eu&k=old", []credtemplate.Part{part("query", "k", "1")}},
		{"a variable name that is not a varName", u + "/{1x}", []credtemplate.Part{part("url_variable", "1x", "1")}},
		{"a variable the URL does not hold", u, []credtemplate.Part{part("url_variable", "token", "1")}},
		{"a variable only in the fragment", u + "#{token}", []credtemplate.Part{part("url_variable", "token", "1")}},
		{"a variable spelled with escapes inside its name", u + "/%7B%74oken%7D", []credtemplate.Part{part("url_variable", "token", "1")}},
		{"a value with surrounding space", u, []credtemplate.Part{part("header", "X-Key", " 1")}},
		{"a dot segment value", u + "/{token}", []credtemplate.Part{part("url_variable", "token", "..")}},
		{"the same header twice, in any case", u, []credtemplate.Part{part("header", "X-Key", "1"), part("header", "x-key", "2")}},
		{"the same query parameter twice", u, []credtemplate.Part{part("query", "k", "1"), part("query", "k", "2")}},
		{"the same variable twice", u + "/{t}", []credtemplate.Part{part("url_variable", "t", "1"), part("url_variable", "t", "2")}},
		{"a query part that would remove a variable's parameter", u + "?k={t}", []credtemplate.Part{part("url_variable", "t", "1"), part("query", "k", "2")}},
	} {
		if _, err := credtemplate.Compile(tc.raw, tc.parts...); err == nil {
			t.Errorf("%s: Compile accepted it", tc.name)
		}
	}

	t.Run("an http URL, a port and an IPv6 host are accepted", func(t *testing.T) {
		for _, raw := range []string{"http://127.0.0.1:8080/mcp", "https://h.example.com:8443/a/b?x=1", "https://[2001:db8::1]/mcp"} {
			if _, err := credtemplate.Compile(raw, part("header", "X-Key", "1")); err != nil {
				t.Errorf("%s: %v", raw, err)
			}
		}
	})
}

// TestMultiPartCredential: a template carries every part of a multi-part
// credential (AM-C12 a: Datadog's two secret headers; Close's secret header
// with a constant one; a key in the query beside a header) and places each
// on the same request.
func TestMultiPartCredential(t *testing.T) {
	srv, rec := serve(t, http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	tpl, err := credtemplate.Compile(srv.URL+"/api/{site}/mcp?v=2",
		credtemplate.Part{In: credtemplate.InHeader, Name: "DD-API-KEY", Value: "dd-api-1"},
		credtemplate.Part{In: credtemplate.InHeader, Name: "DD-APPLICATION-KEY", Value: "dd-app-2"},
		credtemplate.Part{In: credtemplate.InHeader, Name: "Close-Scope", Value: "read", Constant: true},
		credtemplate.Part{In: credtemplate.InQuery, Name: "key", Value: "q-3"},
		credtemplate.Part{In: credtemplate.InURLVariable, Name: "site", Value: "us5"},
	)
	if err != nil {
		t.Fatal(err)
	}
	get(t, tpl.RoundTripper(http.DefaultTransport), srv.URL+"/api/{site}/mcp?v=2")
	r := rec.all()[0]
	for name, want := range map[string]string{"Dd-Api-Key": "dd-api-1", "Dd-Application-Key": "dd-app-2", "Close-Scope": "read"} {
		if got := r.header.Get(name); got != want {
			t.Errorf("header %s = %q, want %q", name, got, want)
		}
	}
	if r.path != "/api/us5/mcp" || r.rawQuery != "v=2&key=q-3" {
		t.Errorf("URL path %q query %q, want /api/us5/mcp and v=2&key=q-3", r.path, r.rawQuery)
	}
	if got := tpl.HeaderNames(); strings.Join(got, ",") != "Dd-Api-Key,Dd-Application-Key,Close-Scope" {
		t.Errorf("HeaderNames() = %v, want the three header parts in order", got)
	}
	// A constant is not a secret: Scrub leaves it, and removes the others.
	scrubbed := tpl.Scrub("dd-api-1 dd-app-2 q-3 us5 read")
	if scrubbed != "[redacted] [redacted] [redacted] [redacted] read" {
		t.Errorf("Scrub = %q", scrubbed)
	}
}

// describe prints a part's fields for a failure message, its value
// replaced by its length.
func describe(p credtemplate.Part) string {
	return string(p.In) + " " + p.Name + " prefix=" + strconv.Quote(p.Prefix) + " value-len=" + strconv.Itoa(len(p.Value))
}
