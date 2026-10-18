package cost

import (
	"context"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"testing"
)

func TestVideoSettlementWithoutModelDB(t *testing.T) {
	p := New(true, nil, nil, nil)
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.EndpointType = "video"
	rc.Metadata["video_cost_usd"] = "1.234567"
	p.ProcessResponse(context.Background(), rc)
	if rc.Metadata["cost"] != "1.234567" {
		t.Fatal(rc.Metadata)
	}
}
