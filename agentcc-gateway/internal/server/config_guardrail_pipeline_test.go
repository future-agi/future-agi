package server

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// GET /-/config reports how this node runs an organization's guardrail
// checks (guardrail_pipeline), which the console's Guardrails tab shows
// read-only in place of an org's own Fail Open and Mode: enabled is
// guardrails.enabled, fail_open the node's
// guardrails.fail_open (every org check's error or timeout follows it), and
// mode "sequential" (org checks run in turn; the first block stops the rest:
// internal/guardrails TestOrgChecksRunInTurnAndStopAtTheFirstBlock).
func TestConfigReportsTheGuardrailPipeline(t *testing.T) {
	for _, tc := range []struct {
		enabled, failOpen bool
	}{{true, true}, {true, false}, {false, false}} {
		srv := configTestServer(t, func(c *config.Config) {
			c.Guardrails.Enabled, c.Guardrails.FailOpen = tc.enabled, tc.failOpen
		})
		out, _ := getConfigForTest(t, srv)
		var got struct {
			Enabled  *bool   `json:"enabled"`
			FailOpen *bool   `json:"fail_open"`
			Mode     *string `json:"mode"`
		}
		if err := json.Unmarshal(out["guardrail_pipeline"], &got); err != nil {
			t.Fatalf("guardrail_pipeline %s: %v", out["guardrail_pipeline"], err)
		}
		if got.Enabled == nil || *got.Enabled != tc.enabled || got.FailOpen == nil || *got.FailOpen != tc.failOpen ||
			got.Mode == nil || *got.Mode != "sequential" {
			t.Errorf("guardrails enabled %v fail_open %v: guardrail_pipeline = %s, want {enabled:%v, fail_open:%v, mode:sequential}",
				tc.enabled, tc.failOpen, out["guardrail_pipeline"], tc.enabled, tc.failOpen)
		}
	}
}
