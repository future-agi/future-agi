package manifest

import (
	"errors"
	"strings"
	"testing"
	"testing/fstest"
)

// fakeKeys are built at run time so no key-shaped literal sits in a file;
// each holds EXAMPLE, which the repo's gitleaks config allows.
func fakeKeys() map[string]string {
	return map[string]string{
		"anthropic-key":              "sk-" + "ant-" + "EXAMPLE" + strings.Repeat("a", 20),
		"openai-style-key":           "sk-" + "proj-" + "EXAMPLE" + strings.Repeat("b", 20),
		"google-api-key":             "AI" + "za" + "EXAMPLE" + strings.Repeat("c", 28),
		"slack-token":                "xo" + "xb-" + "EXAMPLE" + "1234567890",
		"github-pat":                 "gh" + "p_" + "EXAMPLE" + strings.Repeat("d", 29),
		"github-fine-grained-pat":    "github" + "_pat_" + "EXAMPLE" + strings.Repeat("e", 22),
		"aws-access-key-id":          "AK" + "IA" + "EXAMPLEABCDEFGHI",
		"private-key-pem":            "-----BEGIN " + "PRIVATE KEY-----",
		"google-oauth-client-id":     "123456789012-" + strings.Repeat("a", 32) + ".apps.googleusercontent" + ".com",
		"google-oauth-client-secret": "GOC" + "SPX-" + "EXAMPLE" + strings.Repeat("f", 21),
	}
}

func TestSecretShapedValueRefused(t *testing.T) {
	const closeP = "apps/close/providers/close-mcp.yaml"
	places := []struct {
		name string
		path string
		put  func(t *testing.T, fsys fstest.MapFS, key string)
	}{
		{"a record value", closeP, func(t *testing.T, fsys fstest.MapFS, key string) {
			edit(t, fsys, closeP, "      help: The scope your Close admin granted the key.\n", "      help: \"Use "+key+"\"\n")
		}},
		{"a YAML comment", "apps/linear/app.yaml", func(t *testing.T, fsys fstest.MapFS, key string) {
			edit(t, fsys, "apps/linear/app.yaml", "\nname: Linear\n", "\nname: Linear # "+key+"\n")
		}},
		{"the generator overlay", "apps/gmail/providers/futureagi-rest/tools.yaml", func(t *testing.T, fsys fstest.MapFS, key string) {
			put(fsys, "apps/gmail/providers/futureagi-rest/tools.yaml", "token: "+key+"\n")
		}},
		{"a cassette", "apps/gmail/providers/futureagi-rest/fixtures/threads.json", func(t *testing.T, fsys fstest.MapFS, key string) {
			put(fsys, "apps/gmail/providers/futureagi-rest/fixtures/threads.json", `{"authorization": "Bearer `+key+`"}`)
		}},
		{"generated tools", "apps/gmail/providers/futureagi-rest/tools.generated.json", func(t *testing.T, fsys fstest.MapFS, key string) {
			edit(t, fsys, "apps/gmail/providers/futureagi-rest/tools.generated.json", `"description": "Reply in this thread."`, `"description": "`+key+`"`)
		}},
		// A field with a pattern: a record that holds a secret is not
		// decoded, so no pattern message can quote it.
		{"a pattern-checked field", "apps/linear/app.yaml", func(t *testing.T, fsys fstest.MapFS, key string) {
			edit(t, fsys, "apps/linear/app.yaml", "docsUrl: https://linear.app/docs/mcp\n", "docsUrl: \""+key+"\"\n")
		}},
		{"a provider's URL", "apps/linear/providers/linear-mcp.yaml", func(t *testing.T, fsys fstest.MapFS, key string) {
			edit(t, fsys, "apps/linear/providers/linear-mcp.yaml", "  url: https://mcp.linear.app/mcp\n", "  url: \"https://mcp.linear.app/"+key+"\"\n")
		}},
	}
	for rule, key := range fakeKeys() {
		for _, pl := range places {
			t.Run(rule+" in "+pl.name, func(t *testing.T) {
				fsys := fixture(t)
				pl.put(t, fsys, key)
				_, err := LoadDir(fsys)
				wantError(t, err, pl.path, "a value shaped like a "+rule)
				if err != nil && strings.Contains(err.Error(), key) {
					t.Fatalf("the error echoes the secret: %v", err)
				}
			})
		}
	}
	t.Run("an escaped value is decoded first", func(t *testing.T) {
		key := fakeKeys()["github-pat"]
		yamlEscaped := `\x67` + key[1:] // "g" written as an escape
		jsonEscaped := `\u0067` + key[1:]
		const rest = "apps/gmail/providers/futureagi-rest"
		for _, c := range []struct {
			name, path string
			put        func(fsys fstest.MapFS)
		}{
			{"a record", closeP, func(fsys fstest.MapFS) {
				edit(t, fsys, closeP, "      help: The scope your Close admin granted the key.\n", `      help: "`+yamlEscaped+`"`+"\n")
			}},
			{"generated tools", rest + "/tools.generated.json", func(fsys fstest.MapFS) {
				edit(t, fsys, rest+"/tools.generated.json", `"description": "Reply in this thread."`, `"description": "`+jsonEscaped+`"`)
			}},
			{"a cassette", rest + "/fixtures/threads.json", func(fsys fstest.MapFS) {
				put(fsys, rest+"/fixtures/threads.json", `{"request": {"headers": {"authorization": "Bearer `+jsonEscaped+`"}}}`)
			}},
			{"a JSON Lines eval suite, line 2", rest + "/evals/suite.jsonl: line 2", func(fsys fstest.MapFS) {
				put(fsys, rest+"/evals/suite.jsonl", `{"prompt": "find threads"}`+"\n"+`{"prompt": "use `+jsonEscaped+`"}`+"\n")
			}},
			{"the generator overlay, with what records refuse", rest + "/tools.yaml: line 3", func(fsys fstest.MapFS) {
				put(fsys, rest+"/tools.yaml", "defaults: &d {posture: ask}\nsearch: {<<: *d, on: yes}\nnote: \""+yamlEscaped+"\"\n")
			}},
		} {
			t.Run(c.name, func(t *testing.T) {
				fsys := fixture(t)
				c.put(fsys)
				_, err := LoadDir(fsys)
				wantError(t, err, c.path, "a value shaped like a github-pat")
				if err != nil && strings.Contains(err.Error(), key[1:]) {
					t.Fatalf("the error echoes the secret: %v", err)
				}
			})
		}
		t.Run("a cassette that is not JSON is still byte-scanned", func(t *testing.T) {
			fsys := fixture(t)
			put(fsys, rest+"/fixtures/raw.json", "not json "+key+"\n")
			wantLoadError(t, fsys, rest+"/fixtures/raw.json: line 1", "github-pat")
		})
	})
	t.Run("a record that holds a secret is not decoded", func(t *testing.T) {
		// Otherwise the pattern check of docsUrl would report, and quote, it.
		fsys := fixture(t)
		edit(t, fsys, "apps/linear/app.yaml", "docsUrl: https://linear.app/docs/mcp\n", "docsUrl: \""+fakeKeys()["github-pat"]+"\"\n")
		_, err := LoadDir(fsys)
		wantError(t, err, "apps/linear/app.yaml: line 13: holds a value shaped like a github-pat")
		if strings.Contains(err.Error(), "/docsUrl") {
			t.Fatalf("the record was decoded and checked: %v", err)
		}
	})
	t.Run("messages are redacted as a backstop", func(t *testing.T) {
		key := fakeKeys()["github-pat"]
		err := redacted(errors.New("x: " + key + " and " + fakeKeys()["aws-access-key-id"] + "."))
		if got, want := err.Error(), "x: [github-pat] and [aws-access-key-id]."; got != want {
			t.Fatalf("redacted = %q, want %q", got, want)
		}
		plain := errors.New("nothing to hide")
		if redacted(plain) != plain {
			t.Fatalf("an error without a secret is returned as it is")
		}
	})
	t.Run("boundaries", func(t *testing.T) {
		benign := []string{
			"sk-...",                                          // a short placeholder
			"disk-" + strings.Repeat("a", 24),                 // sk- inside a word
			"AK" + "IA" + "EXAMPLEABCDEFGHIJ",                 // 17 characters after AKIA
			"XAK" + "IA" + "EXAMPLEABCDEFGHI",                 // preceded by an uppercase letter
			"-----BEGIN " + "PUBLIC KEY-----",                 // not private
			"ghp_" + "short",                                  // too short
			"123-" + strings.Repeat("a", 32) + ".example.com", // not a Google client id
		}
		fsys := fixture(t)
		edit(t, fsys, "apps/linear/app.yaml", "\nname: Linear\n", "\nname: Linear # "+strings.Join(benign, " ")+"\n")
		mustLoad(t, fsys)
	})
}
