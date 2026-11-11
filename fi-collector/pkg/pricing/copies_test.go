package pricing

import (
	"bytes"
	"os"
	"testing"

	"gopkg.in/yaml.v3"
)

// The collector's two data files are byte copies of the gateway's. The
// refresh script (agentcc-gateway/scripts/update-litellm-pricing.sh) writes
// the price table to both; the schedule is copied by hand.
func TestDataFilesAreCopiesOfTheGatewaysOwn(t *testing.T) {
	for _, pair := range [][2]string{
		{"model_prices.json", "../../../agentcc-gateway/internal/modeldb/litellm.json"},
		{"price_schedule.json", "../../../agentcc-gateway/internal/modeldb/price_schedule.json"},
	} {
		own, err := os.ReadFile(pair[0])
		if err != nil {
			t.Fatal(err)
		}
		gateways, err := os.ReadFile(pair[1])
		if err != nil {
			t.Fatalf("read the gateway's file (run these tests from a full checkout): %v", err)
		}
		if !bytes.Equal(own, gateways) {
			t.Errorf("%s differs from %s: copy the gateway's file over it", pair[0], pair[1])
		}
	}
}

// The collector's CI runs when either gateway file changes. A bare `on`
// key reads as a bool in YAML 1.1, so the struct is tagged "on" explicitly.
func TestCollectorCIWatchesTheGatewayFiles(t *testing.T) {
	raw, err := os.ReadFile("../../../.github/workflows/fi-collector-ci.yml")
	if err != nil {
		t.Fatal(err)
	}
	var workflow struct {
		On struct {
			PullRequest struct {
				Paths []string `yaml:"paths"`
			} `yaml:"pull_request"`
			Push struct {
				Paths []string `yaml:"paths"`
			} `yaml:"push"`
		} `yaml:"on"`
	}
	if err := yaml.Unmarshal(raw, &workflow); err != nil {
		t.Fatal(err)
	}
	for name, paths := range map[string][]string{
		"pull_request.paths": workflow.On.PullRequest.Paths,
		"push.paths":         workflow.On.Push.Paths,
	} {
		for _, want := range []string{
			"agentcc-gateway/internal/modeldb/litellm.json",
			"agentcc-gateway/internal/modeldb/price_schedule.json",
		} {
			found := false
			for _, path := range paths {
				found = found || path == want
			}
			if !found {
				t.Errorf("fi-collector-ci.yml %s does not list %s", name, want)
			}
		}
	}
}
