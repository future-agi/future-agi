package server

import (
	"encoding/json"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/batch"
	"github.com/futureagi/agentcc-gateway/internal/config"
)

// reportBatchStore is a shared batch store the config report only asks
// about; no batch runs, so none of its methods is called.
type reportBatchStore struct{ batch.SharedStore }

type delegatedReport struct {
	Playground *bool `json:"playground"`
	Batch      *bool `json:"batch"`
	Cluster    *bool `json:"cluster"`
}

// GET /-/config reports which delegated calls this gateway serves, so the
// console offers Try it, the Guardrail test and Batch only where they work:
// a single gateway serves both; a cluster serves the playground calls only
// with the nonce store its replicas share, and a delegated batch only with
// both that and the batch store they share.
func TestConfigReportsTheDelegatedCallsItServes(t *testing.T) {
	for _, tc := range []struct {
		name                         string
		cluster, shared, sharedBatch bool
		playground, batch            bool
	}{
		{"single gateway", false, false, false, true, true},
		{"single gateway with a shared store", false, true, false, true, true},
		{"cluster without a shared store", true, false, false, false, false},
		{"cluster with a shared nonce store only", true, true, false, true, false},
		{"cluster with a shared batch store only", true, false, true, false, false},
		{"cluster with shared nonce and batch stores", true, true, true, true, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			srv := r7ConfigServer(t, func(*config.Config) {})
			srv.delegated = auth.NewEnvelopeVerifier("synthetic-admin-token", time.Now().Add(-time.Minute))
			srv.cfg.Cluster.Enabled = tc.cluster
			if tc.shared {
				srv.SetEnvelopeNonceStore(&replicaNonceStore{})
			}
			if tc.sharedBatch {
				if srv.batchProcessor != nil {
					srv.batchProcessor.Stop()
				}
				srv.batchProcessor = batch.NewProcessor(nil, batch.WithSharedStore(reportBatchStore{}, "node-a"))
				t.Cleanup(srv.batchProcessor.Stop)
			}
			out, _ := r7GetConfig(t, srv)
			var got delegatedReport
			if err := json.Unmarshal(out["delegated"], &got); err != nil {
				t.Fatalf("delegated %s: %v", out["delegated"], err)
			}
			if got.Playground == nil || *got.Playground != tc.playground || got.Batch == nil || *got.Batch != tc.batch ||
				got.Cluster == nil || *got.Cluster != tc.cluster {
				t.Errorf("delegated = %s, want {playground:%v, batch:%v, cluster:%v}", out["delegated"], tc.playground, tc.batch, tc.cluster)
			}
		})
	}
}
