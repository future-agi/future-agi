package quota

import (
	"context"
	"fmt"
	"log/slog"
	"net/http"
	"strconv"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"github.com/redis/go-redis/v9"
)

const (
	defaultFreeGatewayLimit = 100000
	redisCmdTimeout         = 50 * time.Millisecond
)

type RedisClient interface {
	Get(ctx context.Context, key string) *redis.StringCmd
	Pipeline() redis.Pipeliner
}

type Plugin struct {
	rdb     RedisClient
	enabled bool
}

func New(rdb RedisClient, enabled bool) *Plugin {
	return &Plugin{rdb: rdb, enabled: enabled}
}

func (p *Plugin) Name() string  { return "quota" }
func (p *Plugin) Priority() int { return 35 }

func (p *Plugin) ProcessRequest(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if !p.enabled || p.rdb == nil {
		return pipeline.ResultContinue()
	}

	orgID := rc.Metadata[tenant.MetadataKeyOrgID]
	if orgID == "" {
		return pipeline.ResultContinue()
	}

	period := time.Now().UTC().Format("2006-01")
	quotaKey := fmt.Sprintf("quota:%s:gateway_requests", orgID)
	usageKey := fmt.Sprintf("usage:%s:gateway_requests:%s", orgID, period)
	pauseKey := fmt.Sprintf("pause:%s:gateway_requests", orgID)

	ctx, cancel := context.WithTimeout(context.Background(), redisCmdTimeout)
	defer cancel()

	pipe := p.rdb.Pipeline()
	quotaCmd := pipe.Get(ctx, quotaKey)
	usageCmd := pipe.Get(ctx, usageKey)
	pauseCmd := pipe.Get(ctx, pauseKey)
	_, err := pipe.Exec(ctx)
	if err != nil && err != redis.Nil {
		slog.Debug("quota redis pipeline error, allowing request", "error", err, "org_id", orgID)
		return pipeline.ResultContinue()
	}

	if pauseVal, _ := pauseCmd.Result(); pauseVal != "" {
		return pipeline.ResultError(&models.APIError{
			Status:  http.StatusTooManyRequests,
			Type:    models.ErrTypeRateLimit,
			Code:    "usage_paused",
			Message: "Gateway requests paused — budget limit reached. Adjust your budget or contact support.",
		})
	}

	limit := defaultFreeGatewayLimit
	if quotaVal, err := quotaCmd.Result(); err == nil {
		if parsed, parseErr := strconv.Atoi(quotaVal); parseErr == nil {
			if parsed < 0 {
				return pipeline.ResultContinue()
			}
			limit = parsed
		}
	}

	current := 0
	if usageVal, err := usageCmd.Result(); err == nil {
		if parsed, parseErr := strconv.Atoi(usageVal); parseErr == nil {
			current = parsed
		}
	}

	if current >= limit {
		return pipeline.ResultError(&models.APIError{
			Status:  http.StatusTooManyRequests,
			Type:    models.ErrTypeRateLimit,
			Code:    "quota_exceeded",
			Message: fmt.Sprintf("Free tier gateway request limit reached (%d / %d). Upgrade to Pay-as-you-go for unlimited requests.", current, limit),
		})
	}

	return pipeline.ResultContinue()
}

func (p *Plugin) ProcessResponse(_ context.Context, _ *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
