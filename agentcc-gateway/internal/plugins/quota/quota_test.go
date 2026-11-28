package quota

import (
	"context"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"github.com/redis/go-redis/v9"
)

type quotaRedis struct {
	redis.Pipeliner
	values map[string]string
}

func (r *quotaRedis) Pipeline() redis.Pipeliner { return r }

func (r *quotaRedis) Get(_ context.Context, key string) *redis.StringCmd {
	if value, ok := r.values[key]; ok {
		return redis.NewStringResult(value, nil)
	}
	return redis.NewStringResult("", redis.Nil)
}

func (r *quotaRedis) Exec(context.Context) ([]redis.Cmder, error) {
	// The usage counter is missing, so the pipeline returns redis.Nil.
	return nil, redis.Nil
}

func TestTotalSpendPauseRefusesGatewayRequests(t *testing.T) {
	r := &quotaRedis{values: map[string]string{"quota:org:gateway_requests": "-1"}}
	plugin := New(r, true)
	rc := &models.RequestContext{Metadata: map[string]string{tenant.MetadataKeyOrgID: "org"}}
	for _, scope := range []string{"gateway_requests", "total_spend"} {
		key := "pause:org:" + scope
		r.values[key] = "1"
		result := plugin.ProcessRequest(t.Context(), rc)
		if result.Action != pipeline.ShortCircuit || result.Error == nil || result.Error.Code != "usage_paused" || result.Error.Status != http.StatusTooManyRequests {
			t.Fatalf("%s: expected a usage_paused 429, got %+v", scope, result)
		}
		delete(r.values, key)
	}
	if result := plugin.ProcessRequest(t.Context(), rc); result.Action != pipeline.Continue {
		t.Fatalf("expected requests to resume after clearing the pause, got %+v", result)
	}
}
