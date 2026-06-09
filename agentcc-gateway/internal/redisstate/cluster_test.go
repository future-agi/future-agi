package redisstate

import (
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Unit tests (no Redis)
// ---------------------------------------------------------------------------

func TestClusterStore_NilClient(t *testing.T) {
	cs := NewClusterStore(nil, 30*time.Second, "test:cluster:")
	if cs.Available() {
		t.Fatal("expected not available with nil client")
	}

	ok := cs.Heartbeat(ClusterNode{ID: "node-1"})
	if ok {
		t.Fatal("expected Heartbeat to return false with nil client")
	}

	nodes, ok := cs.ListNodes()
	if ok {
		t.Fatal("expected ListNodes to return false with nil client")
	}
	if nodes != nil {
		t.Errorf("expected nil nodes, got %v", nodes)
	}
}

func TestClusterStore_UnavailableClient(t *testing.T) {
	client := &Client{breaker: newCircuitBreaker(1, 10*time.Second)}
	client.breaker.recordFailure()

	cs := NewClusterStore(client, 30*time.Second, "test:cluster:")
	if cs.Available() {
		t.Fatal("expected not available with open circuit")
	}
}

// ---------------------------------------------------------------------------
// Integration tests (require real Redis)
// ---------------------------------------------------------------------------

func TestClusterStore_Redis_HeartbeatAndList(t *testing.T) {
	client := skipIfNoRedis(t)
	defer client.Close()

	ts := time.Now().Format("150405.000")
	prefix := "test:cluster:" + ts + ":"
	cs := NewClusterStore(client, 10*time.Second, prefix)

	node1 := ClusterNode{
		ID:        "node-1-" + ts,
		Addr:      "10.0.0.1:8080",
		StartedAt: time.Now(),
		Version:   "1.0.0",
		Status:    "active",
	}
	node2 := ClusterNode{
		ID:        "node-2-" + ts,
		Addr:      "10.0.0.2:8080",
		StartedAt: time.Now(),
		Version:   "1.0.0",
		Status:    "active",
	}

	// Register both nodes.
	if !cs.Heartbeat(node1) {
		t.Fatal("expected Heartbeat(node1) to succeed")
	}
	if !cs.Heartbeat(node2) {
		t.Fatal("expected Heartbeat(node2) to succeed")
	}

	// List nodes.
	nodes, ok := cs.ListNodes()
	if !ok {
		t.Fatal("expected ListNodes to succeed")
	}
	if len(nodes) != 2 {
		t.Fatalf("ListNodes returned %d nodes, want 2", len(nodes))
	}

	// Verify node data is correct.
	found := make(map[string]bool)
	for _, n := range nodes {
		found[n.ID] = true
		if n.Status != "active" {
			t.Errorf("node %s status = %q, want %q", n.ID, n.Status, "active")
		}
	}
	if !found[node1.ID] {
		t.Errorf("node1 %s not found in list", node1.ID)
	}
	if !found[node2.ID] {
		t.Errorf("node2 %s not found in list", node2.ID)
	}
}

func TestClusterStore_Redis_Deregister(t *testing.T) {
	client := skipIfNoRedis(t)
	defer client.Close()

	ts := time.Now().Format("150405.000")
	prefix := "test:cluster:dereg:" + ts + ":"
	cs := NewClusterStore(client, 10*time.Second, prefix)

	node := ClusterNode{
		ID:        "node-dereg-" + ts,
		Addr:      "10.0.0.1:8080",
		StartedAt: time.Now(),
		Version:   "1.0.0",
		Status:    "active",
	}

	cs.Heartbeat(node)

	// Verify it's registered.
	nodes, ok := cs.ListNodes()
	if !ok || len(nodes) != 1 {
		t.Fatalf("expected 1 node, got %d (ok=%v)", len(nodes), ok)
	}

	// Deregister.
	cs.Deregister(node.ID)

	// Verify it's gone.
	nodes, ok = cs.ListNodes()
	if !ok {
		t.Fatal("expected ListNodes to succeed")
	}
	if len(nodes) != 0 {
		t.Errorf("expected 0 nodes after deregister, got %d", len(nodes))
	}
}

func TestClusterStore_Redis_HeartbeatRefresh(t *testing.T) {
	client := skipIfNoRedis(t)
	defer client.Close()

	ts := time.Now().Format("150405.000")
	prefix := "test:cluster:refresh:" + ts + ":"
	cs := NewClusterStore(client, 2*time.Second, prefix)

	node := ClusterNode{
		ID:        "node-refresh-" + ts,
		Addr:      "10.0.0.1:8080",
		StartedAt: time.Now(),
		Version:   "1.0.0",
		Status:    "active",
	}

	// Register.
	cs.Heartbeat(node)

	// Update status and re-heartbeat.
	node.Status = "draining"
	cs.Heartbeat(node)

	// Verify updated status.
	nodes, ok := cs.ListNodes()
	if !ok || len(nodes) != 1 {
		t.Fatalf("expected 1 node, got %d", len(nodes))
	}
	if nodes[0].Status != "draining" {
		t.Errorf("status = %q, want %q", nodes[0].Status, "draining")
	}
}
