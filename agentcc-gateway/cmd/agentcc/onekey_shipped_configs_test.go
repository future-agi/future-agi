package main

import (
	"fmt"
	"io"
	"io/fs"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

// A new organization's first key turns Future AGI models on only where the
// deployment offers them. The backend's default
// (futureagi/agentcc/services/managed_models_default.py) needs the deployment
// to declare AGENTCC_MANAGED_MODELS_ON_FOR_NEW_ORGS and this gateway to answer
// GET /-/orgs/{org}/credits; a 404 managed_models_disabled reads as not
// offered. These tests pin the gateway's side and the shipped files:
//
//   - no gateway configuration the repository ships (the example, the
//     hosted-local config, the Helm chart's default, the file each compose
//     file mounts by default) offers a Future AGI-supplied model, that is a
//     provider with managed: true, and no shipped deployment file sets the
//     declaration, so the default does nothing there;
//   - the gateway answers the credits route only when its configuration marks
//     a provider managed.

// onekeyRoot is the repository root, seen from this package.
var onekeyRoot = filepath.Join("..", "..", "..")

const (
	newOrgDeclaration = "AGENTCC_MANAGED_MODELS_ON_FOR_NEW_ORGS"
	exampleConfig     = "agentcc-gateway/config.example.yaml"
	helmValues        = "deploy/helm/futureagi/values.yaml"
)

// composeFiles mount a gateway config, with ${AGENTCC_CONFIG_PATH:-<file>}.
var composeFiles = []string{"docker-compose.yml", "docker-compose.distributed.yml", "futureagi/docker-compose.yml"}

var composeConfigDefault = regexp.MustCompile(`\$\{AGENTCC_CONFIG_PATH:-([^}]+)\}`)

type shippedConfig struct{ name, text string }

func readRepoFile(t *testing.T, rel string) string {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(onekeyRoot, filepath.FromSlash(rel)))
	if err != nil {
		t.Fatalf("reading %s: %v", rel, err)
	}
	return string(raw)
}

// shippedGatewayConfigs is every gateway configuration the repository ships.
func shippedGatewayConfigs(t *testing.T) []shippedConfig {
	t.Helper()
	out := []shippedConfig{
		{exampleConfig, readRepoFile(t, exampleConfig)},
		{"agentcc-gateway/config.hosted-local.yaml", readRepoFile(t, "agentcc-gateway/config.hosted-local.yaml")},
	}
	var values struct {
		AgentccGateway struct {
			Config map[string]any `yaml:"config"`
		} `yaml:"agentccGateway"`
	}
	if err := yaml.Unmarshal([]byte(readRepoFile(t, helmValues)), &values); err != nil {
		t.Fatalf("parsing %s: %v", helmValues, err)
	}
	if len(values.AgentccGateway.Config) == 0 {
		t.Fatalf("%s has no agentccGateway.config", helmValues)
	}
	helm, err := yaml.Marshal(values.AgentccGateway.Config)
	if err != nil {
		t.Fatal(err)
	}
	out = append(out, shippedConfig{helmValues + " agentccGateway.config", string(helm)})
	for _, compose := range composeFiles {
		defaults := composeConfigDefault.FindAllStringSubmatch(readRepoFile(t, compose), -1)
		if len(defaults) == 0 {
			t.Fatalf("%s mounts no default gateway config (${AGENTCC_CONFIG_PATH:-...}); update this list", compose)
		}
		for _, d := range defaults {
			rel := filepath.ToSlash(filepath.Join(filepath.Dir(compose), d[1]))
			out = append(out, shippedConfig{compose + " default " + rel, readRepoFile(t, rel)})
		}
	}
	return out
}

// managedInText lists the providers a configuration's own text marks
// managed, read from the YAML before any loader. Any value but false (or
// none) counts.
func managedInText(t *testing.T, text string) []string {
	t.Helper()
	var doc struct {
		Providers map[string]map[string]any `yaml:"providers"`
	}
	if err := yaml.Unmarshal([]byte(text), &doc); err != nil {
		t.Fatalf("parsing the config: %v", err)
	}
	var ids []string
	for id, p := range doc.Providers {
		switch v := p["managed"].(type) {
		case nil:
		case bool:
			if v {
				ids = append(ids, id)
			}
		default:
			ids = append(ids, id)
		}
	}
	sort.Strings(ids)
	return ids
}

// managedLoaded lists the providers the gateway's own loader marks managed:
// the ones providers.NewRegistry makes Future AGI providers.
func managedLoaded(t *testing.T, text string) ([]string, error) {
	t.Helper()
	cfg, err := loadExample(t, text)
	if err != nil {
		return nil, err
	}
	var ids []string
	for id, p := range cfg.Providers {
		if p.Managed {
			ids = append(ids, id)
		}
	}
	sort.Strings(ids)
	return ids, nil
}

// declarationFiles are the shipped deployment files that could set the
// backend's declaration: the compose files, the env examples, and every file
// under deploy/ (the Helm chart, its examples, the production compose file),
// but not documentation (Markdown, and deploy/env-reference.toml, which
// documents settings and sets none).
func declarationFiles(t *testing.T) []string {
	t.Helper()
	files := []string{
		"docker-compose.yml", "docker-compose.dev.yml", "docker-compose.distributed.yml",
		"docker-compose.distributed.dev.yml", "futureagi/docker-compose.yml",
		".env.example", "futureagi/.env.example",
	}
	err := filepath.WalkDir(filepath.Join(onekeyRoot, "deploy"), func(path string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return err
		}
		rel, err := filepath.Rel(onekeyRoot, path)
		if err != nil {
			return err
		}
		rel = filepath.ToSlash(rel)
		if strings.HasSuffix(rel, ".md") || rel == "deploy/env-reference.toml" {
			return nil
		}
		files = append(files, rel)
		return nil
	})
	if err != nil {
		t.Fatalf("walking deploy/: %v", err)
	}
	return files
}

// TestShippedConfigsOfferNoFutureAGIModels: where the repository's own
// configs run, a new organization gets no Future AGI models by default,
// because none of them offers one and none declares the default.
func TestShippedConfigsOfferNoFutureAGIModels(t *testing.T) {
	// The checks can fail: the example's own Future AGI block, uncommented,
	// is found by both. Where the block is no longer commented out the
	// example itself is checked below and names it.
	if on, ok := uncommentBlock(readRepoFile(t, exampleConfig), "openrouter-managed"); !ok {
		t.Errorf("%s has no commented openrouter-managed block", exampleConfig)
	} else {
		if got := managedInText(t, on); strings.Join(got, ",") != "openrouter-managed" {
			t.Fatalf("the text check finds %v in the example with openrouter-managed on; want [openrouter-managed]", got)
		}
		if got, err := managedLoaded(t, on); err != nil || strings.Join(got, ",") != "openrouter-managed" {
			t.Fatalf("the loader check finds %v (%v) in the example with openrouter-managed on; want [openrouter-managed]", got, err)
		}
	}

	configs := shippedGatewayConfigs(t)
	for _, c := range configs {
		t.Run(c.name, func(t *testing.T) {
			if got := managedInText(t, c.text); len(got) > 0 {
				t.Errorf("%s marks %v managed: a Future AGI-supplied model is offered where it ships", c.name, got)
			}
			got, err := managedLoaded(t, c.text)
			if err != nil {
				t.Fatalf("%s does not load: %v", c.name, err)
			}
			if len(got) > 0 {
				t.Errorf("%s loads with %v managed: a Future AGI-supplied model is offered where it ships", c.name, got)
			}
		})
	}
	if len(configs) < 6 {
		t.Errorf("checked %d shipped configs, want the example, hosted-local, Helm and three compose defaults", len(configs))
	}

	for _, f := range declarationFiles(t) {
		if strings.Contains(readRepoFile(t, f), newOrgDeclaration) {
			t.Errorf("%s names %s: a shipped deployment must not declare Future AGI models on for new orgs", f, newOrgDeclaration)
		}
	}
}

// onekeyGatewayText is a config's text made runnable in a test: the
// gateway on loopback with the port left as %d for startReadinessGateway, an
// admin token (the credits route is an admin route), and every provider sent
// to upstream, so nothing leaves the machine.
func onekeyGatewayText(t *testing.T, text, upstream string, memoryLedger bool) string {
	t.Helper()
	var doc map[string]any
	if err := yaml.Unmarshal([]byte(text), &doc); err != nil {
		t.Fatalf("parsing the config: %v", err)
	}
	server, _ := doc["server"].(map[string]any)
	if server == nil {
		server = map[string]any{}
	}
	server["host"] = "127.0.0.1"
	server["port"] = "__PORT__"
	doc["server"] = server
	doc["admin"] = map[string]any{"token": testAdminToken}
	providers, _ := doc["providers"].(map[string]any)
	for id, p := range providers {
		pm, _ := p.(map[string]any)
		if pm == nil {
			continue
		}
		pm["base_url"] = upstream
		pm["api_key"] = "onekey-test-upstream-key"
		providers[id] = pm
	}
	if memoryLedger {
		// Without Redis, managed.require_redis (on by default) makes the
		// credits read 503, which the backend also reads as not offered; the
		// memory ledger lets the route answer as a deployment with Redis does.
		managed, _ := doc["managed"].(map[string]any)
		if managed == nil {
			managed = map[string]any{}
		}
		managed["require_redis"] = false
		doc["managed"] = managed
	}
	out, err := yaml.Marshal(doc)
	if err != nil {
		t.Fatal(err)
	}
	s := strings.ReplaceAll(string(out), "%", "%%")
	for _, p := range []string{`"__PORT__"`, `'__PORT__'`, `__PORT__`} {
		s = strings.ReplaceAll(s, p, "%d")
	}
	return s
}

// TestNewOrgDefaultNeedsAManagedProvider: the gateway, started on the
// example config, answers the credits route the backend's new-org default
// reads with 404 managed_models_disabled, so the default leaves Future AGI
// models off; the same config with a provider marked managed answers it.
// With the backend's test_off_where_the_gateway_does_not_offer_them this is
// the whole chain: the default takes effect only where a provider is managed.
func TestNewOrgDefaultNeedsAManagedProvider(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		w.Header().Set("Content-Type", "application/json")
		if strings.HasSuffix(r.URL.Path, "/models") {
			fmt.Fprint(w, `{"data":[{"id":"openai/gpt-4o","created":1790000000,"context_length":128000,`+
				`"pricing":{"prompt":"0.0000025","completion":"0.00001"}}]}`)
			return
		}
		fmt.Fprint(w, `{"id":"x","object":"chat.completion","created":1,"model":"gpt-4o",`+
			`"choices":[{"index":0,"message":{"role":"assistant","content":"hi"},"finish_reason":"stop"}],`+
			`"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`)
	}))
	t.Cleanup(upstream.Close)

	example := readRepoFile(t, exampleConfig)
	cases := []struct {
		name    string
		text    string
		managed bool
	}{
		{"example as shipped", example, false},
	}
	if on, ok := uncommentBlock(example, "openrouter-managed"); ok {
		cases = append(cases, struct {
			name    string
			text    string
			managed bool
		}{"example with openrouter-managed on", on, true})
	} else {
		// The example as shipped then answers the route, and its case says so.
		t.Errorf("%s has no commented openrouter-managed block", exampleConfig)
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			g := startReadinessGateway(t, onekeyGatewayText(t, tc.text, upstream.URL, tc.managed))
			code, body := getWithRetry(t, g.url+"/-/orgs/org-onekey-new/credits", "Bearer "+testAdminToken)
			if tc.managed {
				if code != http.StatusOK || !strings.Contains(body, `"markup_percent"`) {
					t.Errorf("credits with a managed provider = %d %s; want 200 with the markup (offered)", code, body)
				}
				return
			}
			if code != http.StatusNotFound || !strings.Contains(body, "managed_models_disabled") {
				t.Errorf("credits on the shipped example = %d %s; want 404 managed_models_disabled (not offered)", code, body)
			}
		})
	}
}
