package main

import (
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"encoding/hex"
	"encoding/pem"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/orgfactory"
	"github.com/futureagi/agentcc-gateway/internal/routing"
)

// DOCS-TRUTH-GW-1: the example gateway configs the repository ships
// (agentcc-gateway/config*.yaml) and the YAML in agentcc-gateway/README.md are
// what an operator copies. Each one, and each commented-out block of an
// example uncommented on its own, must:
//
//   - load through the gateway's own loader (config.Load, which the gateway
//     runs at start-up and on /-/reload), so the gateway starts on it;
//   - name no key the loader does not read: such a key is dropped with only a
//     start-up warning, so an example that holds one documents a setting
//     that does nothing;
//   - hold only conditional routes that compile, since the gateway starts
//     without them when they do not;
//   - name only guardrail rules the gateway registers, at stage pre or post,
//     since the engine skips any other rule.
//
// The operator's part is supplied as the docs tell an operator to supply it:
// every ${VAR} an example names is set, AGENTCC_ADMIN_TOKEN and
// AGENTCC_MCP_STATE_KEYS hold generated values (release notes U1 and U2), and
// a placeholder public key (a PEM body of "<...>" or "...") is replaced by a
// generated one.

// docsEnvRef is a ${NAME} reference, which the loader expands from the
// environment.
var docsEnvRef = regexp.MustCompile(`\$\{([A-Za-z_][A-Za-z0-9_]*)\}`)

// docsBlockHead is the first line of a commented-out block: "# key:" at any
// indent, with nothing after the colon but a comment.
var docsBlockHead = regexp.MustCompile(`^( *)# ([a-z][a-z0-9_-]*):(\s+#.*)?$`)

// docsOperatorValues are the ${NAME} inputs an example documents as an
// address rather than a secret: config.hosted.example.yaml's exact proxy
// address, control-plane URL and shared Redis host:port. They take the
// synthetic values TestShippedHostedIntegrationActivationOff supplies; every
// other ${NAME} is a secret reference and takes a generated secret.
var docsOperatorValues = map[string]string{
	"AGENTCC_TRUSTED_PROXY_CIDR": "192.0.2.10/32",
	"AGENTCC_CONTROL_PLANE_URL":  "http://backend:8000",
	"AGENTCC_REDIS_ADDRESS":      "redis:6379",
}

// docsBlock is one YAML text to check, and where it comes from.
type docsBlock struct {
	name string
	text string
}

// docsExampleConfigs returns every agentcc-gateway/config*.yaml as shipped,
// then each of its commented-out blocks uncommented on its own.
func docsExampleConfigs(t *testing.T) []docsBlock {
	t.Helper()
	paths, err := filepath.Glob(filepath.Join(onekeyRoot, "agentcc-gateway", "config*.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	sort.Strings(paths)
	var out []docsBlock
	for _, path := range paths {
		rel := "agentcc-gateway/" + filepath.Base(path)
		text := readRepoFile(t, rel)
		out = append(out, docsBlock{rel + " as shipped", text})
		out = append(out, docsCommentedBlocks(rel, text)...)
	}
	if len(out) < 4 {
		t.Fatalf("found %d example configs and blocks under agentcc-gateway/config*.yaml; the glob no longer matches them", len(out))
	}
	return out
}

// docsCommentedBlocks returns text with each commented-out block uncommented
// on its own: a "# key:" head and the lines under it that carry the same
// indent, "#" and at least two spaces.
func docsCommentedBlocks(rel, text string) []docsBlock {
	lines := strings.Split(text, "\n")
	var out []docsBlock
	for i, line := range lines {
		m := docsBlockHead.FindStringSubmatch(line)
		if m == nil {
			continue
		}
		indent := m[1]
		j := i + 1
		for j < len(lines) && strings.HasPrefix(lines[j], indent+"#  ") {
			j++
		}
		if j == i+1 {
			continue // a "# key:" line with nothing under it is prose
		}
		on := append([]string(nil), lines...)
		for k := i; k < j; k++ {
			on[k] = indent + strings.TrimPrefix(strings.TrimPrefix(lines[k], indent+"#"), " ")
		}
		out = append(out, docsBlock{fmt.Sprintf("%s:%d %s uncommented", rel, i+1, m[2]), strings.Join(on, "\n")})
	}
	return out
}

// docsReadmeYAML returns every ```yaml block of agentcc-gateway/README.md.
func docsReadmeYAML(t *testing.T) []docsBlock {
	t.Helper()
	lines := strings.Split(readRepoFile(t, "agentcc-gateway/README.md"), "\n")
	var out []docsBlock
	for i := 0; i < len(lines); i++ {
		if strings.TrimSpace(lines[i]) != "```yaml" {
			continue
		}
		j := i + 1
		for j < len(lines) && strings.TrimSpace(lines[j]) != "```" {
			j++
		}
		out = append(out, docsBlock{fmt.Sprintf("agentcc-gateway/README.md:%d", i+2), strings.Join(lines[i+1:j], "\n")})
		i = j
	}
	if len(out) == 0 {
		t.Fatal("agentcc-gateway/README.md has no ```yaml block; update this test")
	}
	return out
}

// docsUnreadKeys names the keys of text that no config field reads, found as
// the loader's own start-up warning finds them: a strict decode into the
// default config, each ${NAME} standing in as $NAME.
func docsUnreadKeys(text string) []string {
	stand := os.Expand(text, func(name string) string { return "$" + name })
	dec := yaml.NewDecoder(strings.NewReader(stand))
	dec.KnownFields(true)
	var typeErr *yaml.TypeError
	if !errors.As(dec.Decode(config.DefaultConfig()), &typeErr) {
		return nil
	}
	var unread []string
	for _, e := range typeErr.Errors {
		if strings.Contains(e, "not found in type") {
			unread = append(unread, e)
		}
	}
	return unread
}

// docsSecret is a generated value strong enough for the admin token and the
// MCP state key.
func docsSecret(t *testing.T) string {
	t.Helper()
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		t.Fatal(err)
	}
	return hex.EncodeToString(b)
}

// docsWithGeneratedKeys replaces each PEM public key whose body is one
// placeholder line ("<your-...-here>" or "...") with a generated RSA key.
func docsWithGeneratedKeys(t *testing.T, text string) string {
	t.Helper()
	lines := strings.Split(text, "\n")
	var body []string
	var out []string
	for i := 0; i < len(lines); i++ {
		out = append(out, lines[i])
		if strings.TrimSpace(lines[i]) != "-----BEGIN PUBLIC KEY-----" || i+2 >= len(lines) {
			continue
		}
		placeholder := strings.TrimSpace(lines[i+1])
		if strings.TrimSpace(lines[i+2]) != "-----END PUBLIC KEY-----" ||
			!(placeholder == "..." || strings.HasPrefix(placeholder, "<") && strings.HasSuffix(placeholder, ">")) {
			continue
		}
		if body == nil {
			key, err := rsa.GenerateKey(rand.Reader, 2048)
			if err != nil {
				t.Fatal(err)
			}
			der, err := x509.MarshalPKIXPublicKey(&key.PublicKey)
			if err != nil {
				t.Fatal(err)
			}
			encoded := strings.Split(strings.TrimSpace(string(pem.EncodeToMemory(&pem.Block{Type: "PUBLIC KEY", Bytes: der}))), "\n")
			body = encoded[1 : len(encoded)-1]
		}
		indent := lines[i+1][:len(lines[i+1])-len(strings.TrimLeft(lines[i+1], " "))]
		for _, b := range body {
			out = append(out, indent+b)
		}
		i++ // the placeholder line
	}
	return strings.Join(out, "\n")
}

// docsLoadAsOperator loads text through config.Load with the environment an
// operator who follows the docs sets. Every other AGENTCC_* variable of the
// test process is cleared, since it would override the file.
func docsLoadAsOperator(t *testing.T, text string) (*config.Config, error) {
	t.Helper()
	for _, kv := range os.Environ() {
		if strings.HasPrefix(kv, "AGENTCC_") {
			name, _, _ := strings.Cut(kv, "=")
			t.Setenv(name, "")
			os.Unsetenv(name)
		}
	}
	secret := docsSecret(t)
	for _, m := range docsEnvRef.FindAllStringSubmatch(text, -1) {
		value, documented := docsOperatorValues[m[1]]
		if !documented {
			value = secret
		}
		t.Setenv(m[1], value)
	}
	t.Setenv("AGENTCC_ADMIN_TOKEN", secret)
	t.Setenv(config.EnvMCPStateKeys, secret)
	path := filepath.Join(t.TempDir(), "config.yaml")
	if err := os.WriteFile(path, []byte(docsWithGeneratedKeys(t, text)), 0o600); err != nil {
		t.Fatal(err)
	}
	return config.Load(path)
}

// docsCheckConfig reports every way text fails the checks at the top of this
// file.
func docsCheckConfig(t *testing.T, b docsBlock) {
	t.Helper()
	if unread := docsUnreadKeys(b.text); len(unread) > 0 {
		t.Errorf("%s holds keys the gateway does not read, so they do nothing: %s", b.name, strings.Join(unread, "; "))
	}
	cfg, err := docsLoadAsOperator(t, b.text)
	if err != nil {
		t.Errorf("%s does not load through config.Load, so the gateway would not start on it: %v", b.name, err)
		return
	}
	if len(cfg.Routing.ConditionalRoutes) > 0 {
		if _, err := routing.NewConditionalRouter(cfg.Routing.ConditionalRoutes); err != nil {
			t.Errorf("%s: its routing.conditional_routes do not compile, so the gateway starts without them: %v", b.name, err)
		}
	}
	build := orgfactory.New(nil)
	for i, rule := range cfg.Guardrails.Rules {
		if rule.Stage != "pre" && rule.Stage != "post" {
			t.Errorf("%s: guardrails.rules[%d] %q has stage %q; the engine skips a rule whose stage is not pre or post", b.name, i, rule.Name, rule.Stage)
		}
		if build(rule.Name, rule.Config) == nil {
			t.Errorf("%s: guardrails.rules[%d] %q names no guardrail the gateway registers, so the engine skips it", b.name, i, rule.Name)
		}
	}
}

func TestDocsExampleConfigsLoad(t *testing.T) {
	for _, b := range docsExampleConfigs(t) {
		t.Run(b.name, func(t *testing.T) { docsCheckConfig(t, b) })
	}
}

func TestDocsReadmeYAMLLoads(t *testing.T) {
	for _, b := range docsReadmeYAML(t) {
		t.Run(b.name, func(t *testing.T) { docsCheckConfig(t, b) })
	}
}
