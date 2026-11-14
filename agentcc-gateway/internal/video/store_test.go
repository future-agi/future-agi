package video_test

import (
	video "github.com/futureagi/agentcc-gateway/internal/video"
	"github.com/futureagi/agentcc-gateway/internal/video/storetest"
	"testing"
)

func TestMemoryStore(t *testing.T) {
	storetest.Run(t, func(t *testing.T) video.Store { return video.NewMemoryStore() })
}
