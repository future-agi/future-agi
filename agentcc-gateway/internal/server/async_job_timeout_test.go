package server

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	authpkg "github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// An async job's timeout is the timeout ChatCompletion resolves for its
// request, step for step, before server.max_request_timeout holds it: the
// header, else the org's model timeout (the stored org config, or the
// rollout candidate's when step 4 chose it), else the node's model timeout or
// the server default. The synchronous resolvers give the same value held at
// the ceiling, so the two can only differ by that clamp.
func TestAsyncJobTimeoutIsTheRequestTimeoutBeforeTheCeiling(t *testing.T) {
	const ceiling = 200 * time.Second
	stored := &tenant.OrgConfig{Routing: &tenant.RoutingConfig{ModelTimeouts: map[string]string{"m-org": "7m"}}}
	candidate := &tenant.OrgConfig{Routing: &tenant.RoutingConfig{ModelTimeouts: map[string]string{"m-org": "9m"}}}
	tenantStore := tenant.NewStore()
	tenantStore.Set(orgTimeoutOrgID, stored)
	h := &Handlers{
		defaultTimeout: 60 * time.Second,
		maxTimeout:     ceiling,
		keyStore:       authpkg.NewKeyStore(config.AuthConfig{Enabled: true, Keys: orgTimeoutKeys()}),
		tenantStore:    tenantStore,
	}
	mt := map[string]time.Duration{"m-long": 600 * time.Second, "m-short": 90 * time.Second, "m-org": 30 * time.Second}
	h.modelTimeouts.Store(&mt)

	for _, tc := range []struct {
		name      string
		model     string
		header    map[string]string
		orgKey    bool              // the request carries the org's key
		candidate bool              // step 4 chose the rollout candidate's config
		orgCfg    *tenant.OrgConfig // the effective org config routing chose
		want      time.Duration     // the job's timeout
	}{
		{"the operator's model timeout above the ceiling", "m-long", nil, false, false, stored, 600 * time.Second},
		{"the operator's model timeout under the ceiling", "m-short", nil, false, false, stored, 90 * time.Second},
		{"the server default", "m-other", nil, false, false, stored, 60 * time.Second},
		{"a header above the ceiling", "m-short", map[string]string{"x-agentcc-timeout": "10m"}, false, false, stored, 10 * time.Minute},
		{"a milliseconds header under the ceiling", "m-long", map[string]string{"x-agentcc-request-timeout": "5000"}, false, false, stored, 5 * time.Second},
		{"the stored org's model timeout above the ceiling", "m-org", nil, true, false, stored, 7 * time.Minute},
		{"a stable request reads the stored org config, as ChatCompletion does", "m-org", nil, true, false, candidate, 7 * time.Minute},
		{"a rollout candidate's model timeout, not the stored one", "m-org", nil, true, true, candidate, 9 * time.Minute},
		{"a candidate with no timeout for the model falls to the node's", "m-long", nil, true, true, candidate, 600 * time.Second},
		{"a header over the candidate's model timeout", "m-org", map[string]string{"x-agentcc-timeout": "4m"}, true, true, candidate, 4 * time.Minute},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.Model = tc.model
			if tc.orgKey {
				rc.Metadata["authorization"] = "Bearer " + orgTimeoutKey
			}
			if tc.candidate {
				rc.Evidence.PolicyVariant = tenant.PolicyVariantCandidate
			}
			r := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil)
			for k, v := range tc.header {
				r.Header.Set(k, v)
			}

			// What ChatCompletion resolves for the request before the
			// ceiling, which its job runs to (AsyncMiddleware): the stored org
			// config's timeout, or the candidate's.
			job := h.requestTimeout(rc, r)
			if tc.candidate {
				job = h.requestTimeoutFor(rc, r, tc.orgCfg)
			}
			if job != tc.want {
				t.Errorf("the job's timeout = %v, want %v", job, tc.want)
			}
			// What ChatCompletion resolves for the synchronous request: the
			// stored org config's timeout, or the candidate's.
			sync := h.resolveTimeout(rc, r)
			if tc.candidate {
				sync = h.resolveTimeoutFor(rc, r, tc.orgCfg)
			}
			if want := min(tc.want, ceiling); sync != want {
				t.Errorf("the synchronous timeout = %v, want %v: the job's, held at the ceiling", sync, want)
			}
		})
	}
}
