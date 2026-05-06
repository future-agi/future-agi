package cluster

import (
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

func newTestManager() *Manager {
	cfg := config.ClusterConfig{
		Enabled:           true,
		NodeID:            "test-node-1",
		HeartbeatInterval: 100 * time.Millisecond,
		HeartbeatTTL:      1 * time.Second,
		DrainTimeout:      5 * time.Second,
	}
	return NewManager(cfg, "localhost:8080", "0.1.0")
}

func TestNewManager(t *testing.T) {
	m := newTestManager()
	if m.NodeID() != "test-node-1" {
		t.Fatalf("expected node ID 'test-node-1', got %q", m.NodeID())
	}
	if m.NodeCount() != 1 {
		t.Fatalf("expected 1 node (self), got %d", m.NodeCount())
	}
}

func TestAutoGenerateNodeID(t *testing.T) {
	cfg := config.ClusterConfig{
		Enabled:           true,
		HeartbeatInterval: 100 * time.Millisecond,
		HeartbeatTTL:      1 * time.Second,
	}
	m := NewManager(cfg, "localhost:8080", "0.1.0")
	if m.NodeID() == "" {
		t.Fatal("expected auto-generated node ID")
	}
}

func TestListNodes(t *testing.T) {
	m := newTestManager()
	nodes := m.ListNodes()
	if len(nodes) != 1 {
		t.Fatalf("expected 1 node, got %d", len(nodes))
	}
	if nodes[0].ID != "test-node-1" {
		t.Fatalf("expected node ID 'test-node-1', got %q", nodes[0].ID)
	}
	if nodes[0].Addr != "localhost:8080" {
		t.Fatalf("expected addr 'localhost:8080', got %q", nodes[0].Addr)
	}
	if nodes[0].Version != "0.1.0" {
		t.Fatalf("expected version '0.1.0', got %q", nodes[0].Version)
	}
	if nodes[0].Status != "active" {
		t.Fatalf("expected status 'active', got %q", nodes[0].Status)
	}
}

func TestDrain(t *testing.T) {
	m := newTestManager()
	if m.IsDraining() {
		t.Fatal("expected not draining initially")
	}
	m.Drain()
	if !m.IsDraining() {
		t.Fatal("expected draining after Drain()")
	}
	nodes := m.ListNodes()
	if nodes[0].Status != "draining" {
		t.Fatalf("expected status 'draining', got %q", nodes[0].Status)
	}
}

func TestStartStop(t *testing.T) {
	m := newTestManager()
	m.Start()
	time.Sleep(150 * time.Millisecond) // let heartbeat tick
	m.Stop()
	time.Sleep(50 * time.Millisecond) // let deregister happen
	if m.NodeCount() != 0 {
		t.Fatalf("expected 0 nodes after stop, got %d", m.NodeCount())
	}
}

func TestStopIdempotent(t *testing.T) {
	m := newTestManager()
	m.Start()
	m.Stop()
	m.Stop() // should not panic
}

func TestNodeStartedAt(t *testing.T) {
	before := time.Now()
	m := newTestManager()
	after := time.Now()

	nodes := m.ListNodes()
	if nodes[0].StartedAt.Before(before) || nodes[0].StartedAt.After(after) {
		t.Fatalf("StartedAt not in expected range")
	}
}

func TestDefaultIntervals(t *testing.T) {
	cfg := config.ClusterConfig{
		Enabled: true,
		NodeID:  "test",
	}
	m := NewManager(cfg, "localhost:8080", "0.1.0")
	if m.interval != 10*time.Second {
		t.Fatalf("expected default interval 10s, got %v", m.interval)
	}
	if m.ttl != 30*time.Second {
		t.Fatalf("expected default TTL 30s, got %v", m.ttl)
	}
	if m.drainTTL != 30*time.Second {
		t.Fatalf("expected default drain TTL 30s, got %v", m.drainTTL)
	}
}
