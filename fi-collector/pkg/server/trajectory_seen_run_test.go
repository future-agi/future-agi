package server

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"
	"github.com/future-agi/future-agi/fi-collector/pkg/trajectory"
	"github.com/redis/go-redis/v9"
)

// Server.Run starts the seen store's background work when the trajectory
// store is wired: this collector's traj:contract:v2 field (with the store's
// own reuse age) is written at start and removed on shutdown. A server
// without a seen store writes nothing.
func TestServerRunWritesSeenContractWhileTrajectoryActive(t *testing.T) {
	for _, withStore := range []bool{true, false} {
		mr := miniredis.RunT(t)
		rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
		t.Cleanup(func() { _ = rdb.Close() })
		log := slog.New(slog.NewTextHandler(io.Discard, nil))

		_, w := newCHBodies(t)
		addr := testTCPAddr(t)
		opts := []Option{WithLogger(log)}
		if withStore {
			cfg := trajectory.Config{Mode: trajectory.ModeOn, PreviewMaxBytes: 128}.Normalize()
			seen := trajectory.NewSeenStoreWithOptions(rdb, trajectory.SeenOptions{
				MaxReuseAge: 6 * time.Hour, WriterID: "collector-run-test", Logger: log,
			})
			opts = append(opts, WithTrajectory(cfg, seen))
		}
		s := New(Config{GRPCAddr: addr, BatchMaxRows: 1 << 30, BatchMaxAge: time.Hour}, w, nil, nil, nil, opts...)
		s.cfg.HTTPAddr = ""
		ctx, cancel := context.WithCancel(context.Background())
		done := make(chan struct{})
		go func() { _ = s.Run(ctx); close(done) }()
		if !waitPort(addr, 2*time.Second) {
			cancel()
			<-done
			t.Fatalf("server didn't listen on %s", addr)
		}

		if !withStore {
			time.Sleep(200 * time.Millisecond)
			if mr.Exists(trajectory.SeenContractKey) {
				t.Fatalf("server without a seen store wrote %s", trajectory.SeenContractKey)
			}
			cancel()
			<-done
			continue
		}
		deadline := time.Now().Add(3 * time.Second)
		for mr.HGet(trajectory.SeenContractKey, "collector-run-test") == "" {
			if time.Now().After(deadline) {
				cancel()
				<-done
				t.Fatalf("Run did not write %s", trajectory.SeenContractKey)
			}
			time.Sleep(10 * time.Millisecond)
		}
		var c trajectory.SeenContract
		if err := json.Unmarshal([]byte(mr.HGet(trajectory.SeenContractKey, "collector-run-test")), &c); err != nil {
			t.Fatal(err)
		}
		if c.MaxReuseAgeSeconds != int64((6*time.Hour)/time.Second) || c.Writer != "collector-run-test" || c.WrittenAt == 0 {
			t.Fatalf("contract=%+v", c)
		}
		cancel()
		<-done
		deadline = time.Now().Add(3 * time.Second)
		for mr.HGet(trajectory.SeenContractKey, "collector-run-test") != "" {
			if time.Now().After(deadline) {
				t.Fatalf("contract field left after shutdown")
			}
			time.Sleep(10 * time.Millisecond)
		}
	}
}
