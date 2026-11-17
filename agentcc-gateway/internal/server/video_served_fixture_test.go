package server

// Video generation as the gateway serves it once an operator turns it on
// (handlers_video_test.go's fixture): video on, a Redis job store, the
// lifecycle service with a fake provider adapter, and an org API key. A test
// whose /v1/videos requests should reach the route, rather than its 501 "not
// configured", gives its gateway this set-up.

import (
	"context"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	provider "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"github.com/futureagi/agentcc-gateway/internal/providers/video/videotest"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/video"
	"github.com/futureagi/agentcc-gateway/internal/video/artifacts"
	"github.com/futureagi/agentcc-gateway/internal/video/capability"
	"github.com/futureagi/agentcc-gateway/internal/video/lifecycle"
)

// videoServedSurface is the other-API videos row with a model the video
// provider serves.
var videoServedSurface = otherAPISurface{name: "videos", path: "/v1/videos", body: jsonBody(`{"model":"` + videoTestModel + `","prompt":%s}`), ok: 202}

// withDevVideo gives s a video job store and the video lifecycle service, as
// newVideoHTTPScript does, on the server's own pipeline engine.
func withDevVideo(t *testing.T, s *Server) {
	t.Helper()
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Skip("TEST_REDIS_ADDR unset")
	}
	c, e := redisstate.NewClient(redisstate.Config{Address: addr, Timeout: time.Second})
	if e != nil {
		t.Fatal(e)
	}
	prefix := fmt.Sprintf("test:video:override:%d:", time.Now().UnixNano())
	vc := config.DefaultConfig().Video
	vc.Enabled = true
	vc.Submit.SyncWait = 0
	vc.Artifacts.Disk.Root = t.TempDir()
	vc.Providers["byteplus"] = config.VideoProviderConfig{Enabled: true, Models: []string{strings.TrimPrefix(videoTestModel, "byteplus/")}, Region: "ap-southeast-1", AccountRef: "fake", TariffRevision: capability.BytePlusRevision, Limits: config.VideoProviderLimits{SubmitRPM: 100, PollQPS: 100, MaxActiveTasks: 100}, Deadlines: config.VideoProviderDeadlines{Submit: time.Second, Read: time.Second, Run: time.Hour, Reconcile: time.Second}, Poll: config.VideoProviderPoll{BaseInterval: time.Second}}
	f := videotest.NewFakeProvider(videotest.Script{Capabilities: capability.BytePlus(), Submit: videotest.JSONReply(provider.SubmitResult{ProviderJobID: "fake-id", Normalized: provider.StateQueued})})
	adapters := provider.NewRegistry()
	adapters.Register(f.Adapter())
	budget := redisstate.NewBudgetStore(c, prefix+"budget:")
	st := video.NewRedisStore(c, video.StoreOptions{Prefix: prefix, Budget: budget})
	blobs, e := artifacts.NewDisk(vc.Artifacts.Disk.Root, vc.Artifacts.MaxBytes)
	if e != nil {
		t.Fatal(e)
	}
	svc, e := lifecycle.New(lifecycle.Options{Config: vc, Store: st, Redis: c, Budget: budget, Adapters: adapters, Artifacts: blobs, Engine: s.handlers.engine, Prefix: prefix})
	if e != nil {
		t.Fatal(e)
	}
	s.handlers.videoStore = st
	s.handlers.videoService = svc
	s.handlers.videoSyncWait = 0
	t.Cleanup(func() {
		// Shut the gateway down before its video store's Redis client closes, as
		// newVideoHTTPScript does; Shutdown runs once, so releaseTestServer's later
		// call is a no-op.
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		_ = s.Shutdown(ctx)
		cancel()
		f.Close()
		keys, _ := c.Redis().Keys(context.Background(), prefix+"*").Result()
		if len(keys) > 0 {
			c.Redis().Del(context.Background(), keys...)
		}
		c.Close()
	})
}
