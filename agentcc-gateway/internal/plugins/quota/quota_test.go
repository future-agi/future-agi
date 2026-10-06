package quota

import (
	"context"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"github.com/redis/go-redis/v9"
)

// fakeRedis serves quota:, usage: and pause: keys from a map and counts how
// often the plugin opens a pipeline (i.e. reads them at all).
type fakeRedis struct {
	data      map[string]string
	pipelines int
}

func (f *fakeRedis) Get(_ context.Context, key string) *redis.StringCmd {
	if v, ok := f.data[key]; ok {
		return redis.NewStringResult(v, nil)
	}
	return redis.NewStringResult("", redis.Nil)
}

func (f *fakeRedis) Pipeline() redis.Pipeliner {
	f.pipelines++
	return &fakePipe{data: f.data}
}

type fakePipe struct {
	redis.Pipeliner
	data map[string]string
}

func (p *fakePipe) Get(_ context.Context, key string) *redis.StringCmd {
	if v, ok := p.data[key]; ok {
		return redis.NewStringResult(v, nil)
	}
	return redis.NewStringResult("", redis.Nil)
}

func (p *fakePipe) Exec(context.Context) ([]redis.Cmder, error) { return nil, nil }

func requestFor(orgID string) *models.RequestContext {
	return &models.RequestContext{Metadata: map[string]string{tenant.MetadataKeyOrgID: orgID}}
}

// staleCloudKeys are what a self-hosted install can carry over from Cloud.
func staleCloudKeys(orgID string) *fakeRedis {
	period := time.Now().UTC().Format("2006-01")
	return &fakeRedis{data: map[string]string{
		"usage:" + orgID + ":gateway_requests:" + period: "999999",
		"pause:" + orgID + ":gateway_requests":           "1",
	}}
}

func TestCommercialQuotasEnabledDefaultsOn(t *testing.T) {
	for _, tc := range []struct {
		value string
		want  bool
	}{
		{"", true}, {"true", true}, {"TRUE", true}, {"1", true}, {"yes", true}, {"on", true},
		{"false", false}, {"False", false}, {"0", false}, {"no", false}, {"off", false}, {" OFF ", false},
	} {
		got, err := CommercialQuotasEnabled(func(string) string { return tc.value })
		if err != nil {
			t.Fatalf("COMMERCIAL_QUOTAS_ENABLED=%q: %v", tc.value, err)
		}
		if got != tc.want {
			t.Errorf("COMMERCIAL_QUOTAS_ENABLED=%q: %v, want %v", tc.value, got, tc.want)
		}
	}
}

func TestCommercialQuotasEnabledRejectsTypos(t *testing.T) {
	for _, bad := range []string{"flase", "disabled", "2"} {
		if _, err := CommercialQuotasEnabled(func(string) string { return bad }); err == nil {
			t.Errorf("COMMERCIAL_QUOTAS_ENABLED=%q must be rejected", bad)
		}
	}
}

// TH-8084 B2: with commercial quotas off (every shipped self-hosted
// manifest), carried-over quota:/usage:/pause: keys are never read.
func TestDisabledQuotaIgnoresStaleCloudKeys(t *testing.T) {
	rdb := staleCloudKeys("org-stale")
	result := New(rdb, false).ProcessRequest(context.Background(), requestFor("org-stale"))
	if result.Error != nil {
		t.Fatalf("self-hosted request capped by stale Cloud keys: %+v", result.Error)
	}
	if rdb.pipelines != 0 {
		t.Fatalf("disabled quota plugin read Redis %d times", rdb.pipelines)
	}
}

// Unset (Cloud): today's 429s are unchanged.
func TestEnabledQuotaKeepsCloudBehaviour(t *testing.T) {
	paused := New(staleCloudKeys("org-paused"), true).ProcessRequest(context.Background(), requestFor("org-paused"))
	if paused.Error == nil || paused.Error.Code != "usage_paused" || paused.Error.Status != 429 {
		t.Fatalf("expected usage_paused 429, got %+v", paused.Error)
	}

	period := time.Now().UTC().Format("2006-01")
	over := &fakeRedis{data: map[string]string{"usage:org-free:gateway_requests:" + period: "100000"}}
	exceeded := New(over, true).ProcessRequest(context.Background(), requestFor("org-free"))
	if exceeded.Error == nil || exceeded.Error.Code != "quota_exceeded" {
		t.Fatalf("expected quota_exceeded, got %+v", exceeded.Error)
	}

	under := &fakeRedis{data: map[string]string{"usage:org-free:gateway_requests:" + period: "10"}}
	if r := New(under, true).ProcessRequest(context.Background(), requestFor("org-free")); r.Error != nil {
		t.Fatalf("under the free limit must pass, got %+v", r.Error)
	}
}
