package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// TestCheckCommand: check passes a saved cassette and fails one edited to
// hold an email, a credential shape or an unknown key.
func TestCheckCommand(t *testing.T) {
	dir := t.TempDir()
	good := saveCassette(t, dir, mcpCassette())
	if code, out := runCLI(t, "check", good); code != exitOK || !strings.Contains(out, "ok "+good) {
		t.Fatalf("check on a saved cassette: %d %s", code, out)
	}
	data, err := os.ReadFile(good)
	if err != nil {
		t.Fatal(err)
	}
	pat := "gh" + "p_" + strings.Repeat("a", 36)
	for name, tc := range map[string]struct{ from, to, want string }{
		"an email":        {`"Sunny"`, `"Sunny, says alice@corp-test.dev"`, "email"},
		"a credential":    {`"Sunny"`, `"Sunny ` + pat + `"`, "github-pat"},
		"an unknown key":  {`"kind": "mcp",`, `"kind": "mcp", "extra": 1,`, "unknown field"},
		"no test account": {`"test_account": "ci-loud"`, `"test_account": ""`, "test-account"},
	} {
		t.Run(name, func(t *testing.T) {
			edited := strings.Replace(string(data), tc.from, tc.to, 1)
			if edited == string(data) {
				t.Fatalf("the edit %q did not apply", tc.from)
			}
			path := filepath.Join(t.TempDir(), "edited.json")
			if err := os.WriteFile(path, []byte(edited), 0o600); err != nil {
				t.Fatal(err)
			}
			code, out := runCLI(t, "check", path)
			if code != exitFail || !strings.Contains(out, tc.want) || strings.Contains(out, pat) {
				t.Errorf("check = %d %q, want exit 1 naming %s (and never the value)", code, out, tc.want)
			}
		})
	}
	if code, _ := runCLI(t, "check"); code != exitUsage {
		t.Errorf("check with no file = %d", code)
	}
}

// TestRecordFlagGuards: record stops before any network call on a
// credential variable that is unset, a proxy listen address off loopback,
// a plain-http remote upstream, or flags of the other kind.
func TestRecordFlagGuards(t *testing.T) {
	upstream, hits := countingServer(t)
	out := filepath.Join(t.TempDir(), "c.json")
	const shortSecret = "Qz8vK3" // under 8 characters: too short to hunt down in free text
	t.Setenv("CASSETTE_TEST_SHORT_SECRET", shortSecret)
	for name, tc := range map[string]struct {
		args []string
		want string
	}{
		"unset credential variable": {[]string{"--kind", "mcp", "--url", upstream.URL + "/mcp", "--auth-env", "CASSETTE_TEST_UNSET_VAR"}, "empty or unset"},
		"bad header-env":            {[]string{"--kind", "mcp", "--url", upstream.URL + "/mcp", "--header-env", "X-Key"}, "Header=VAR"},
		"proxy off loopback":        {[]string{"--kind", "http", "--upstream", upstream.URL, "--listen", "0.0.0.0:0"}, "loopback"},
		"plain http to a remote":    {[]string{"--kind", "http", "--upstream", "http://api.vendor.example"}, "loopback host only"},
		"http with mcp flags":       {[]string{"--kind", "http", "--upstream", upstream.URL, "--auth-env", "HOME"}, "--kind mcp flags"},
		"mcp with http flags":       {[]string{"--kind", "mcp", "--url", upstream.URL + "/mcp", "--upstream", upstream.URL}, "--kind http flags"},
		"url and url-env":           {[]string{"--kind", "mcp", "--url", upstream.URL + "/mcp", "--url-env", "HOME"}, "exclusive"},
		"unset url-env":             {[]string{"--kind", "mcp", "--url-env", "CASSETTE_TEST_UNSET_URL"}, "empty or unset"},
		"unset secret-env, http":    {[]string{"--kind", "http", "--upstream", upstream.URL, "--secret-env", "CASSETTE_TEST_UNSET_SECRET"}, "empty or unset"},
		"unset secret-env, mcp":     {[]string{"--kind", "mcp", "--url", upstream.URL + "/mcp", "--secret-env", "CASSETTE_TEST_UNSET_SECRET"}, "empty or unset"},
		"empty secret-param":        {[]string{"--kind", "http", "--upstream", upstream.URL, "--secret-param", ""}, "--secret-param"},
		"punctuation secret-key":    {[]string{"--kind", "http", "--upstream", upstream.URL, "--secret-key", "--"}, "--secret-key"},
		"short secret-env, mcp": {[]string{"--kind", "mcp", "--url", upstream.URL + "/mcp", "--secret-env", "CASSETTE_TEST_SHORT_SECRET"},
			"CASSETTE_TEST_SHORT_SECRET is shorter than 8"},
		"short secret-env, http": {[]string{"--kind", "http", "--upstream", upstream.URL, "--secret-env", "CASSETTE_TEST_SHORT_SECRET"},
			"CASSETTE_TEST_SHORT_SECRET is shorter than 8"},
		"a redact literal in a secret-key": {[]string{"--kind", "mcp", "--url", upstream.URL + "/mcp", "--redact", "acmecorp",
			"--secret-key", "acmecorp_handle"}, "spells a literal to scrub"},
		"a redact literal in a secret-param": {[]string{"--kind", "mcp", "--url", upstream.URL + "/mcp", "--redact", "token",
			"--secret-param", "private_token"}, "spells a literal to scrub"},
		"a short redact literal": {[]string{"--kind", "mcp", "--url", upstream.URL + "/mcp", "--redact", "abc"}, "at least 4 characters"},
		"unknown kind":           {[]string{"--kind", "grpc"}, "want mcp or http"},
		"no out":                 {[]string{"--kind", "mcp", "--url", upstream.URL + "/mcp", "--out", ""}, "--out is required"},
	} {
		t.Run(name, func(t *testing.T) {
			args := append([]string{"record", "--test-account", "ci-guard", "--out", out}, tc.args...)
			code, logs := runCLI(t, args...)
			if code != exitUsage || !strings.Contains(logs, tc.want) {
				t.Errorf("exit %d, logs %q: want exit 2 naming %q", code, logs, tc.want)
			}
			if strings.Contains(logs, shortSecret) {
				t.Error("the logs carry a --secret-env value")
			}
		})
	}
	if hits.Load() != 0 {
		t.Fatalf("a refused record reached the upstream %d time(s)", hits.Load())
	}
	if _, err := os.Stat(out); !os.IsNotExist(err) {
		t.Fatal("a refused record wrote a file")
	}
}

// TestRecordURLFromEnv: an endpoint that carries a credential comes from the
// environment, and when the server echoes it, the cassette holds neither its
// secret path segment nor its query key.
func TestRecordURLFromEnv(t *testing.T) {
	token := tokenShaped("url-bearer")
	segment := "u" + strings.Repeat("rlvar", 6)
	t.Setenv("CASSETTE_TEST_URL_TOKEN", token)
	_, srv := newVendorMCP(t, token)
	t.Setenv("CASSETTE_TEST_URL", srv.URL+"/"+segment+"/mcp?key=query-key-value-42")
	dir := t.TempDir()
	calls, out := filepath.Join(dir, "calls.json"), filepath.Join(dir, "c.json")
	if err := os.WriteFile(calls, []byte(`[{"name":"search_threads","arguments":{"query":"in:inbox"}}]`), 0o600); err != nil {
		t.Fatal(err)
	}
	code, logs := runCLI(t, "record", "--kind", "mcp", "--test-account", "ci-url", "--url-env", "CASSETTE_TEST_URL",
		"--auth-env", "CASSETTE_TEST_URL_TOKEN", "--calls", calls, "--out", out)
	if code != exitOK {
		t.Fatalf("record exited %d: %s", code, logs)
	}
	data, err := os.ReadFile(out)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(data), "endpoint") || strings.Contains(string(data), segment) ||
		strings.Contains(string(data), "query-key-value-42") || strings.Contains(logs, segment) {
		t.Errorf("the URL's credential reached the cassette or the log")
	}
}
