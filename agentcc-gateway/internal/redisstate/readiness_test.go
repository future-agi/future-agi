package redisstate

import (
	"context"
	"net"
	"os/exec"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

func TestHostedRedisProbeDoesNotChangeOrdinaryClientPolicy(t *testing.T) {
	binary, err := exec.LookPath("redis-server")
	if err != nil {
		t.Skip("redis-server required; cloud gate runs this in the cached Redis image")
	}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	address := listener.Addr().String()
	_, port, _ := net.SplitHostPort(address)
	listener.Close()
	command := exec.Command(binary, "--bind", "127.0.0.1", "--port", port, "--save", "", "--appendonly", "no", "--dir", t.TempDir())
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() {
		if command.ProcessState == nil {
			command.Process.Kill()
			command.Wait()
		}
	}()
	var client *Client
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		client, err = NewClient(Config{Address: address, Timeout: 100 * time.Millisecond})
		if err == nil {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	if err != nil {
		t.Fatal("synthetic Redis did not start")
	}
	defer client.Close()
	original := client.rdb.(*redis.Client).Options().ContextTimeoutEnabled
	if err := client.Ready(context.Background()); err != nil {
		t.Fatal(err)
	}
	if client.rdb.(*redis.Client).Options().ContextTimeoutEnabled != original {
		t.Fatal("readiness changed ordinary client policy")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := client.Ready(ctx); err == nil {
		t.Fatal("cancelled probe succeeded")
	}
	command.Process.Kill()
	command.Wait()
	started := time.Now()
	ctx, cancel = context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	if err := client.Ready(ctx); err == nil {
		t.Fatal("stopped Redis reported ready")
	}
	if time.Since(started) > time.Second {
		t.Fatal("failed Redis probe exceeded its budget")
	}
}
