package main

import (
	"bytes"
	"context"
	"errors"
	"io"
	"sync/atomic"
	"testing"
	"time"

	provider "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"github.com/futureagi/agentcc-gateway/internal/providers/video/videotest"
	"github.com/futureagi/agentcc-gateway/internal/video/media"
	"github.com/redis/go-redis/v9"
)

func TestSyntheticVideoVerifiedAndBounded(t *testing.T) {
	ctx := context.Background()
	a := &loadAdapter{}
	a.active.Store(1)
	const size = 100_000
	r := &syntheticVideo{ctx: ctx, owner: a, header: syntheticHeader(), left: size}
	var copied int64
	fetcher := media.NewFetcher(func(_ context.Context, _ string, body io.Reader) error {
		var err error
		copied, err = io.Copy(io.Discard, body)
		return err
	})
	got, err := fetcher.Verify(ctx, r, "video/mp4", "test.mp4", media.Limits{MaxBytes: size, Deadline: time.Second})
	if err != nil {
		t.Fatal(err)
	}
	if copied != size || got.Bytes != size || a.bytes.Load() != size || got.ContentType != "video/mp4" {
		t.Fatalf("copy=%d result=%+v bytes=%d", copied, got, a.bytes.Load())
	}
	r.Close()
	r.Close()
	if a.active.Load() != 0 {
		t.Fatal("close must decrement once")
	}
	r = &syntheticVideo{ctx: ctx, owner: a, header: syntheticHeader(), left: copyBytes}
	buf := make([]byte, 64<<10)
	n, err := r.Read(buf)
	if err != nil || n != 32<<10 || !bytes.Equal(buf[:len(syntheticHeader())], syntheticHeader()) {
		t.Fatalf("chunk=%d err=%v", n, err)
	}
	cancelled, cancel := context.WithCancel(ctx)
	cancel()
	r.ctx = cancelled
	if _, err := r.Read(buf); !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
}

func TestVideoDuplicateDetectorRejectsMissingAndRepeatedSubmits(t *testing.T) {
	f := videotest.NewFakeProvider(videotest.Script{Submit: videotest.JSONReply(provider.SubmitResult{Normalized: provider.StateQueued})})
	defer f.Close()
	a := &loadAdapter{Adapter: f.Adapter()}
	l := &videoLoad{fake: f}
	if _, _, err := l.submitCounts([]string{"job"}); err == nil {
		t.Fatal("missing submit passed")
	}
	for i := 1; i <= 2; i++ {
		r, err := a.Submit(context.Background(), &provider.JobView{ID: "job"}, provider.Correlation{Token: "job"})
		if err != nil || r.ProviderJobID != "fake-job" {
			t.Fatalf("result=%+v err=%v", r, err)
		}
		total, duplicates, err := l.submitCounts([]string{"job"})
		if total != i || duplicates != i-1 || (err != nil) != (i == 2) {
			t.Fatalf("total=%d duplicates=%d err=%v", total, duplicates, err)
		}
	}
}

func TestRedisCommandsCountsPipelineMembersAndFailedCommands(t *testing.T) {
	h := &redisCommands{}
	ctx := context.Background()
	errSentinel := errors.New("failed command")
	if err := h.ProcessHook(func(context.Context, redis.Cmder) error { return errSentinel })(ctx, redis.NewCmd(ctx, "evalsha")); !errors.Is(err, errSentinel) {
		t.Fatal(err)
	}
	err := h.ProcessPipelineHook(func(context.Context, []redis.Cmder) error { return nil })(ctx, []redis.Cmder{redis.NewCmd(ctx, "get"), redis.NewCmd(ctx, "get")})
	if err != nil || h.n.Load() != 3 {
		t.Fatalf("count=%d err=%v", h.n.Load(), err)
	}
}

func TestParallelVideoPropagatesFailure(t *testing.T) {
	var calls atomic.Int64
	want := errors.New("failed operation")
	err := parallelVideo(context.Background(), 100, 8, func(int) error { calls.Add(1); return want })
	if !errors.Is(err, want) || calls.Load() > 8 {
		t.Fatalf("calls=%d err=%v", calls.Load(), err)
	}
}
