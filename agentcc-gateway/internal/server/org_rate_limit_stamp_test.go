package server

// applyOrgRateLimitOverrides keeps
// its four keys and adds org_rate_limit_per_key_tpm (a decimal string, only
// when > 0) and org_rate_limit_per_model_rpm (compact JSON object model ->
// rpm, keys sorted, only when set); nothing when rate_limiting.enabled is
// false. The tenant config's per_model_rpm is one limit for every model, so
// the object has one key: the request's model (rc.Model) at stamping time.

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestOrgRateLimitStamp(t *testing.T) {
	h := &Handlers{}
	stamp := func(rl *tenant.RateLimitConfig, model string) map[string]string {
		rc := models.AcquireRequestContext()
		defer rc.Release()
		rc.Model = model
		h.applyOrgRateLimitOverrides(&tenant.OrgConfig{RateLimiting: rl}, rc)
		out := map[string]string{}
		for k, v := range rc.Metadata {
			out[k] = v
		}
		return out
	}

	all := stamp(&tenant.RateLimitConfig{Enabled: true, GlobalRPM: 60, GlobalTPM: 1000, PerKeyRPM: 10, PerKeyTPM: 500, PerModelRPM: 7, PerUserRPM: 3}, `gpt-"4o"`)
	want := map[string]string{
		"org_rate_limit_rpm":           "60",
		"org_rate_limit_tpm":           "1000",
		"org_rate_limit_per_key_rpm":   "10",
		"org_rate_limit_per_user_rpm":  "3",
		"org_rate_limit_per_key_tpm":   "500",
		"org_rate_limit_per_model_rpm": `{"gpt-\"4o\"":7}`,
	}
	for k, v := range want {
		if all[k] != v {
			t.Errorf("%s = %q, want %q", k, all[k], v)
		}
	}
	if len(all) != len(want) {
		t.Errorf("stamped %v, want exactly %v", all, want)
	}

	// The object's one key is the requested name (stamped at call-order step
	// 4, before auto routing and route resolution): "auto" for an auto
	// request, the alias for a model_map alias. Its single value is the
	// per-model RPM of whichever model is counted.
	if auto := stamp(&tenant.RateLimitConfig{Enabled: true, PerModelRPM: 7}, "auto"); auto["org_rate_limit_per_model_rpm"] != `{"auto":7}` {
		t.Errorf("auto request: org_rate_limit_per_model_rpm = %q, want {\"auto\":7}", auto["org_rate_limit_per_model_rpm"])
	}

	none := stamp(&tenant.RateLimitConfig{Enabled: true, GlobalRPM: 60}, "m")
	if _, ok := none["org_rate_limit_per_key_tpm"]; ok {
		t.Errorf("per_key_tpm 0 stamped %q", none["org_rate_limit_per_key_tpm"])
	}
	if _, ok := none["org_rate_limit_per_model_rpm"]; ok {
		t.Errorf("per_model_rpm 0 stamped %q", none["org_rate_limit_per_model_rpm"])
	}

	if off := stamp(&tenant.RateLimitConfig{Enabled: false, GlobalRPM: 60, PerKeyTPM: 5, PerModelRPM: 5}, "m"); len(off) != 0 {
		t.Errorf("rate_limiting disabled stamped %v, want nothing", off)
	}
}
