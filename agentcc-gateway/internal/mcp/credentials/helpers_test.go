package credentials

import (
	"context"
	"fmt"
	"os"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// Test fixtures. None is a real credential; every address is a local test
// server or an example.test name that is never contacted.
const (
	testStateKey     = "m25-test-state-key-not-a-real-secret-000"
	testDjangoSecret = "m25-test-internal-secret-not-real"
	testAdminToken   = "m25-test-admin-token-not-real"
	testConnectHost  = "https://connect.example.test"
)

func testCodec(t *testing.T) *mcp.StateCodec {
	t.Helper()
	c, err := mcp.NewStateCodec([]string{testStateKey}, false)
	if err != nil {
		t.Fatal(err)
	}
	return c
}

// testRedis connects to the test Redis (TEST_REDIS_ADDR, default
// localhost:6379; TEST_REDIS_DB, else this lane's DB 13) and returns the
// client and a key prefix of this test's own, whose keys are deleted when it
// ends (SCAN and DEL, never FLUSHDB). It skips without Redis.
func testRedis(t *testing.T) (*redisstate.Client, string) {
	t.Helper()
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		addr = "localhost:6379"
	}
	db := 13
	if v := os.Getenv("TEST_REDIS_DB"); v != "" {
		db, _ = strconv.Atoi(v)
	}
	client, err := redisstate.NewClient(redisstate.Config{Address: addr, DB: db, Timeout: time.Second})
	if err != nil {
		t.Skipf("skipping: Redis not available at %s: %v", addr, err)
	}
	prefix := fmt.Sprintf("test:M25:%s:%d:", t.Name(), time.Now().UnixNano())
	t.Cleanup(func() {
		ctx := context.Background()
		rdb := client.Redis()
		iter := rdb.Scan(ctx, 0, globEscape(prefix)+"*", 1000).Iterator()
		var keys []string
		for iter.Next(ctx) {
			keys = append(keys, iter.Val())
		}
		if len(keys) > 0 {
			rdb.Del(ctx, keys...)
		}
		client.Close()
	})
	return client, prefix
}

// globEscape escapes Redis SCAN pattern characters.
func globEscape(s string) string {
	var b strings.Builder
	for _, r := range s {
		switch r {
		case '*', '?', '[', ']', '\\', '^':
			b.WriteByte('\\')
		}
		b.WriteRune(r)
	}
	return b.String()
}

// testClock is a settable clock.
type testClock struct {
	mu sync.Mutex
	t  time.Time
}

func newTestClock() *testClock { return &testClock{t: time.Now()} }

func (c *testClock) now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.t
}

func (c *testClock) advance(d time.Duration) {
	c.mu.Lock()
	c.t = c.t.Add(d)
	c.mu.Unlock()
}
