package cassette

import (
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"testing"
)

// Fake credential shapes are built at run time, never written as literals,
// so no source file holds a string a secret scanner would flag.
func fakeShape(rule string) string {
	switch rule {
	case "anthropic-key":
		return "sk-" + "ant-" + strings.Repeat("A1b2", 6)
	case "openai-style-key":
		return "sk-" + "proj-" + strings.Repeat("Zx9", 8)
	case "google-api-key":
		return "AI" + "za" + strings.Repeat("Q", 35)
	case "slack-token":
		return "xo" + "xb-" + strings.Repeat("1234567890", 2)
	case "github-pat":
		return "gh" + "p_" + strings.Repeat("a", 36)
	case "github-fine-grained-pat":
		return "github" + "_pat_" + strings.Repeat("B", 30)
	case "aws-access-key-id":
		return "AK" + "IA" + strings.Repeat("Q", 16)
	case "private-key-pem":
		return "-----BEGIN " + "RSA PRIVATE KEY-----"
	case "stripe-secret-key":
		return "sk" + "_live_" + strings.Repeat("c", 24)
	case "jwt":
		return "ey" + "J" + strings.Repeat("h", 12) + ".ey" + "J" + strings.Repeat("p", 12) + "." + strings.Repeat("s", 16)
	}
	panic("no fake shape for " + rule)
}

func rulesIn(s string) []string {
	var rules []string
	for _, f := range FindSecretShapes(s) {
		rules = append(rules, f.Rule)
	}
	sort.Strings(rules)
	return rules
}

// TestSecretShapesMatchP6 pins the Go port of P6's secrets-lint.sh rules,
// including the lookbehind and lookahead cases RE2 cannot express.
func TestSecretShapesMatchP6(t *testing.T) {
	for _, rule := range []string{"anthropic-key", "openai-style-key", "google-api-key", "slack-token",
		"github-pat", "github-fine-grained-pat", "aws-access-key-id", "private-key-pem",
		"stripe-secret-key", "jwt"} {
		t.Run("finds "+rule, func(t *testing.T) {
			got := rulesIn("prefix " + fakeShape(rule) + " suffix")
			if len(got) != 1 || got[0] != rule {
				t.Fatalf("rules = %v, want [%s]", got, rule)
			}
		})
	}
	cases := []struct {
		name, text string
		want       []string
	}{
		{"short openai placeholder", "sk-...", nil},
		{"openai 19 chars", "sk-" + strings.Repeat("a", 19), nil},
		{"openai after a letter", "x" + "sk-" + strings.Repeat("a", 20), nil},
		{"openai after a dash", "-" + "sk-" + strings.Repeat("a", 20), []string{"openai-style-key"}},
		{"anthropic is not also openai", fakeShape("anthropic-key"), []string{"anthropic-key"}},
		{"rejected candidate restarts one byte later",
			"x" + "sk-" + strings.Repeat("a", 20) + "-" + "sk-" + strings.Repeat("b", 20), []string{"openai-style-key"}},
		{"aws inside a longer upper run", "AK" + "IA" + strings.Repeat("Q", 17), nil},
		{"aws after an upper letter", "X" + "AK" + "IA" + strings.Repeat("Q", 16), nil},
		{"aws then lowercase", "AS" + "IA" + strings.Repeat("Q", 16) + "abc", []string{"aws-access-key-id"}},
		{"aws 15 chars", "AK" + "IA" + strings.Repeat("Q", 15), nil},
		{"public key pem", "-----BEGIN " + "PUBLIC KEY-----", nil},
		{"pgp private block", "-----BEGIN PGP " + "PRIVATE KEY BLOCK-----", []string{"private-key-pem"}},
		{"slack app token is not P6", "xo" + "xa-" + strings.Repeat("1", 12), nil},
		{"github pat 35 chars", "gh" + "p_" + strings.Repeat("a", 35), nil},
		{"google key 34 chars", "AI" + "za" + strings.Repeat("Q", 34), nil},
		{"placeholders are clean", "Bearer scrubbed-token-1 user-1@example.com scrubbed-id-2 00000000-0000-4000-8000-000000000001", nil},
		{"empty", "", nil},
		{"two rules", fakeShape("github-pat") + " and " + fakeShape("slack-token"), []string{"github-pat", "slack-token"}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := rulesIn(c.text)
			if strings.Join(got, ",") != strings.Join(c.want, ",") {
				t.Fatalf("rules = %v, want %v", got, c.want)
			}
		})
	}
	t.Run("a finding carries no value", func(t *testing.T) {
		f := FindSecretShapes("a " + fakeShape("github-pat"))
		if len(f) != 1 || f[0].Offset != 2 || strings.Contains(fmt.Sprintf("%+v", f[0]), "aaaa") {
			t.Fatalf("finding = %+v", f)
		}
	})
}

// TestSecretShapesAgreeWithP6Script runs P6's own secrets-lint.sh over the
// same lines and checks the Go port finds exactly the P6 rules it finds.
func TestSecretShapesAgreeWithP6Script(t *testing.T) {
	script, err := filepath.Abs("../../../scripts/release/secrets-lint.sh")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(script); err != nil {
		t.Fatalf("P6 script missing: %v", err)
	}
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skip("python3 not on PATH; P6's script needs it")
	}
	lines := []string{
		fakeShape("anthropic-key"), fakeShape("openai-style-key"), fakeShape("google-api-key"),
		fakeShape("slack-token"), fakeShape("github-pat"), fakeShape("github-fine-grained-pat"),
		fakeShape("aws-access-key-id"), fakeShape("private-key-pem"),
		"x" + "sk-" + strings.Repeat("a", 20) + "-" + "sk-" + strings.Repeat("b", 20),
		"AK" + "IA" + strings.Repeat("Q", 17), "X" + "AK" + "IA" + strings.Repeat("Q", 16),
		"-----BEGIN " + "PUBLIC KEY-----", "sk-...", "clean value",
	}
	var yaml strings.Builder
	for i, l := range lines {
		fmt.Fprintf(&yaml, "case%d: \"%s\"\n", i, l)
	}
	path := filepath.Join(t.TempDir(), "cases.yaml")
	if err := os.WriteFile(path, []byte(yaml.String()), 0o600); err != nil {
		t.Fatal(err)
	}
	out, _ := exec.Command("bash", script, path).Output() // exit 1 means findings
	p6 := map[int][]string{}
	sc := bufio.NewScanner(strings.NewReader(string(out)))
	for sc.Scan() {
		var line int
		var rule string
		rest := strings.TrimPrefix(sc.Text(), path+":")
		if _, err := fmt.Sscanf(rest, "%d: %s", &line, &rule); err != nil {
			t.Fatalf("unparsed P6 line %q: %v", sc.Text(), err)
		}
		p6[line-1] = append(p6[line-1], rule)
	}
	for i, l := range lines {
		var goRules []string
		for _, r := range rulesIn(l) {
			if p6Rules[r] {
				goRules = append(goRules, r)
			}
		}
		want := p6[i]
		sort.Strings(want)
		if strings.Join(goRules, ",") != strings.Join(want, ",") {
			t.Errorf("line %d: go %v, P6 %v", i, goRules, want)
		}
	}
}
