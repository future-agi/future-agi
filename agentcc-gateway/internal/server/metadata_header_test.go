package server

import (
	"sort"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestParseMetadataHeader(t *testing.T) {
	tests := []struct {
		name       string
		header     string
		wantMeta   map[string]string
		wantCustom []string
	}{
		{
			name:       "caller keys are stored and recorded",
			header:     `{"profile_id":"milestone-p1","business_id":"biz-42"}`,
			wantMeta:   map[string]string{"profile_id": "milestone-p1", "business_id": "biz-42"},
			wantCustom: []string{"business_id", "profile_id"},
		},
		{
			name:       "reserved keys are rejected, not recorded",
			header:     `{"cost":"0","org_id":"evil","auth_key_id":"evil","client_ip":"1.2.3.4","tenant":"ok"}`,
			wantMeta:   map[string]string{"tenant": "ok"},
			wantCustom: []string{"tenant"},
		},
		{
			name:       "malformed json is ignored",
			header:     `not json`,
			wantMeta:   map[string]string{},
			wantCustom: nil,
		},
		{
			name:       "non-string values are ignored",
			header:     `{"depth":3}`,
			wantMeta:   map[string]string{},
			wantCustom: nil,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			rc := &models.RequestContext{Metadata: map[string]string{}}
			parseMetadataHeader(tt.header, rc)

			if len(rc.Metadata) != len(tt.wantMeta) {
				t.Fatalf("Metadata = %v, want %v", rc.Metadata, tt.wantMeta)
			}
			for k, v := range tt.wantMeta {
				if rc.Metadata[k] != v {
					t.Errorf("Metadata[%q] = %q, want %q", k, rc.Metadata[k], v)
				}
			}

			got := append([]string(nil), rc.CustomMetadataKeys...)
			sort.Strings(got)
			if len(got) != len(tt.wantCustom) {
				t.Fatalf("CustomMetadataKeys = %v, want %v", got, tt.wantCustom)
			}
			for i, k := range tt.wantCustom {
				if got[i] != k {
					t.Errorf("CustomMetadataKeys[%d] = %q, want %q", i, got[i], k)
				}
			}
		})
	}
}

// A blocked key must not become a telemetry dimension either — CustomMetadataKeys
// is what the OTel plugin exports, so recording one would re-open the injection
// the blocklist exists to prevent.
func TestParseMetadataHeaderDoesNotRecordBlockedKeys(t *testing.T) {
	rc := &models.RequestContext{Metadata: map[string]string{"cost": "0.5"}}
	parseMetadataHeader(`{"cost":"0.0"}`, rc)

	if rc.Metadata["cost"] != "0.5" {
		t.Errorf("caller overwrote a plugin-owned key: cost = %q", rc.Metadata["cost"])
	}
	if len(rc.CustomMetadataKeys) != 0 {
		t.Errorf("CustomMetadataKeys = %v, want empty", rc.CustomMetadataKeys)
	}
}
