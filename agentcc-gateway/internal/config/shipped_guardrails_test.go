package config

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"gopkg.in/yaml.v3"
)

// The gateway configs the install paths mount by default (docker-compose.yml
// and docker-compose.distributed.yml mount config.example.yaml, the hosted
// local profile mounts config.hosted-local.yaml, and the Helm chart renders
// agentccGateway.config) run the guardrail plugin with no rule of their own,
// so the checks an organization saves in the console run, and only those. A
// fresh organization saves none, so its requests pass through unchanged. This
// is the shape the E2E profiles carry (e2e/stack/gateway.e2e.yaml).
var shippedGuardrails = map[string]interface{}{"enabled": true}

func shippedConfigRoot() string { return filepath.Join("..", "..", "..") }

func TestShippedConfigsRunTheChecksAnOrganizationSaves(t *testing.T) {
	root := shippedConfigRoot()
	for _, path := range []string{
		filepath.Join(root, "agentcc-gateway", "config.example.yaml"),
		filepath.Join(root, "agentcc-gateway", "config.hosted-local.yaml"),
	} {
		t.Run(filepath.Base(path), func(t *testing.T) {
			raw, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			var doc map[string]interface{}
			if err := yaml.Unmarshal(raw, &doc); err != nil {
				t.Fatalf("parse %s: %v", path, err)
			}
			if got := doc["guardrails"]; !reflect.DeepEqual(got, shippedGuardrails) {
				t.Errorf("guardrails = %#v, want exactly %#v", got, shippedGuardrails)
			}

			unsetEnv(t, adminTokenEnv...)
			t.Setenv("AGENTCC_ADMIN_TOKEN", strongToken)
			t.Setenv("AGENTCC_CONTROL_PLANE_URL", "http://backend")
			t.Setenv("AGENTCC_CONTROL_PLANE_TOKEN", strongToken)
			cfg, err := Load(path)
			if err != nil {
				t.Fatalf("Load(%s): %v", path, err)
			}
			if !cfg.Guardrails.Enabled {
				t.Error("the guardrail plugin is off: an organization's saved checks would not run")
			}
			if n := len(cfg.Guardrails.Rules); n != 0 {
				t.Errorf("the file carries %d rule(s) of its own; it must carry none", n)
			}
		})
	}
}

func TestHelmChartRunsTheChecksAnOrganizationSaves(t *testing.T) {
	path := filepath.Join(shippedConfigRoot(), "deploy", "helm", "futureagi", "values.yaml")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var values struct {
		AgentccGateway struct {
			Config map[string]interface{} `yaml:"config"`
		} `yaml:"agentccGateway"`
	}
	if err := yaml.Unmarshal(raw, &values); err != nil {
		t.Fatalf("parse %s: %v", path, err)
	}
	if got := values.AgentccGateway.Config["guardrails"]; !reflect.DeepEqual(got, shippedGuardrails) {
		t.Errorf("agentccGateway.config.guardrails = %#v, want exactly %#v", got, shippedGuardrails)
	}
}
