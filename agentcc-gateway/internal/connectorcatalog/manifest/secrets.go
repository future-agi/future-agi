package manifest

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	pathpkg "path"
	"regexp"
	"strings"

	"gopkg.in/yaml.v3"
)

// secretRule is one provider-key shape. The first eight are P6's
// scripts/release/secrets-lint.sh rules; Go's RE2 has no lookaround, so
// their boundaries are checked by hand in boundaryOK. The two Google OAuth
// client shapes enforce the open-data rule's "no client ids" for the
// shape that is recognisable (omega-coordination.md, 05:40 item 1c).
type secretRule struct {
	name string
	re   *regexp.Regexp
}

var secretRules = []secretRule{
	{"anthropic-key", regexp.MustCompile(`sk-ant-[A-Za-z0-9_-]{20,}`)},
	{"openai-style-key", regexp.MustCompile(`sk-[A-Za-z0-9_-]{20,}`)},
	{"google-api-key", regexp.MustCompile(`AIza[0-9A-Za-z_-]{35}`)},
	{"slack-token", regexp.MustCompile(`xox[bp]-[0-9A-Za-z-]{10,}`)},
	{"github-pat", regexp.MustCompile(`ghp_[0-9A-Za-z]{36}`)},
	{"github-fine-grained-pat", regexp.MustCompile(`github_pat_[0-9A-Za-z_]{22,}`)},
	{"aws-access-key-id", regexp.MustCompile(`(AKIA|ASIA)[0-9A-Z]{16}`)},
	{"private-key-pem", regexp.MustCompile(`-----BEGIN ([A-Z0-9]+ )*PRIVATE KEY( BLOCK)?-----`)},
	{"google-oauth-client-id", regexp.MustCompile(`[0-9]{6,}-[a-z0-9]{32}[.]apps[.]googleusercontent[.]com`)},
	{"google-oauth-client-secret", regexp.MustCompile(`GOCSPX-[A-Za-z0-9_-]{28}`)},
}

func isAlnum(b byte) bool {
	return b >= 'a' && b <= 'z' || b >= 'A' && b <= 'Z' || b >= '0' && b <= '9'
}

func isUpperOrDigit(b byte) bool { return b >= 'A' && b <= 'Z' || b >= '0' && b <= '9' }

// boundaryOK applies secrets-lint.sh's lookarounds to a match s[i:j]:
// openai-style-key is (?<![A-Za-z0-9])sk-(?!ant-), and aws-access-key-id is
// (?<![A-Z0-9])...(?![A-Z0-9]).
func boundaryOK(rule string, s string, i, j int) bool {
	switch rule {
	case "openai-style-key":
		return (i == 0 || !isAlnum(s[i-1])) && !strings.HasPrefix(s[i:], "sk-ant-")
	case "aws-access-key-id":
		return (i == 0 || !isUpperOrDigit(s[i-1])) && (j == len(s) || !isUpperOrDigit(s[j]))
	}
	return true
}

// findSecret returns the first rule a key shape in s matches, or "".
func findSecret(s string) string {
	rule, _, _ := locateSecret(s)
	return rule
}

// locateSecret returns the first rule a key shape in s matches and where,
// s[i:j], or "". A match the lookarounds refuse does not hide a later one:
// the scan resumes one byte past the refused match's start.
func locateSecret(s string) (rule string, i, j int) {
	for _, r := range secretRules {
		for from := 0; from < len(s); {
			loc := r.re.FindStringIndex(s[from:])
			if loc == nil {
				break
			}
			i, j := from+loc[0], from+loc[1]
			if boundaryOK(r.name, s, i, j) {
				return r.name, i, j
			}
			from = i + 1
		}
	}
	return "", 0, 0
}

// scanFile checks a file for secret-shaped values: its bytes line by line,
// so a key in a comment or in a file that does not parse is found, then the
// decoded strings of a JSON, JSON Lines or YAML file, cassettes and eval
// suites included, so a key written with escapes is found too. A message
// names the rule and the line, never the value.
func scanFile(path string, data []byte) []error {
	if errs := scanSecrets(path, data); len(errs) > 0 {
		return errs
	}
	var errs []error
	visit := func(line int, s string) {
		if rule := findSecret(s); rule != "" {
			errs = append(errs, secretError(path, fmt.Sprintf("line %d", line), rule))
		}
	}
	switch strings.ToLower(pathpkg.Ext(path)) {
	case ".json", ".jsonl", ".ndjson", ".har":
		jsonStrings(data, visit)
	case ".yaml", ".yml":
		yamlStrings(data, visit)
	}
	return errs
}

// scanSecrets checks a file's bytes line by line.
func scanSecrets(path string, data []byte) []error {
	var errs []error
	for n, line := range strings.Split(string(data), "\n") {
		if rule := findSecret(line); rule != "" {
			errs = append(errs, secretError(path, fmt.Sprintf("line %d", n+1), rule))
		}
	}
	return errs
}

// jsonStrings calls visit with every string of a JSON file, keys included,
// decoded, and the line it ends on. It reads value after value, so a JSON
// Lines file is read whole, and it is lenient (a repeated key is read
// twice); it stops at the first syntax error, where the byte scan stands.
func jsonStrings(data []byte, visit func(line int, s string)) {
	dec := json.NewDecoder(bytes.NewReader(data))
	line, at := 1, 0
	for {
		tok, err := dec.Token()
		if err != nil {
			return
		}
		if s, ok := tok.(string); ok {
			off := int(dec.InputOffset())
			line += bytes.Count(data[at:off], []byte{'\n'})
			at = off
			visit(line, s)
		}
	}
}

// yamlStrings calls visit with every scalar of a YAML file, keys included,
// decoded, and its line. It reads every document and allows what the record
// decoder refuses (anchors, tags, YAML 1.1 booleans); an alias is not
// expanded, since its anchor is visited where it stands. It stops at the
// first syntax error, where the byte scan stands.
func yamlStrings(data []byte, visit func(line int, s string)) {
	dec := yaml.NewDecoder(bytes.NewReader(data))
	for {
		var doc yaml.Node
		if dec.Decode(&doc) != nil {
			return
		}
		walkYAML(&doc, visit)
	}
}

func walkYAML(n *yaml.Node, visit func(int, string)) {
	if n.Kind == yaml.ScalarNode {
		visit(n.Line, n.Value)
	}
	for _, c := range n.Content {
		walkYAML(c, visit)
	}
}

func secretError(path, where, rule string) error {
	return fmt.Errorf("%s: %s: holds a value shaped like a %s; catalog data never holds a secret or a client id", path, where, rule)
}

// redacted returns err with each secret-shaped part of its message replaced
// by the rule's name. It is a backstop: LoadDir decodes no record from a
// file that holds a secret, so no message of a later check can quote one.
func redacted(err error) error {
	msg := err.Error()
	out := msg
	for range len(msg) { // each pass removes a match, so this bounds the loop
		rule, i, j := locateSecret(out)
		if rule == "" {
			break
		}
		out = out[:i] + "[" + rule + "]" + out[j:] // a bracketed rule name matches no rule
	}
	if out == msg {
		return err
	}
	return errors.New(out)
}
