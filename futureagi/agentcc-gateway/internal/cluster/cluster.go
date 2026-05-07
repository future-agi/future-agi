package cluster

import (
	"crypto/rand"
	"encoding/hex"
	"log/slog"
	"os"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// Node represents a gateway instance in the cluster.
type Node struct {
	ID        string    `json:"id"`
	Addr      string    `json:"addr"`
	StartedAt time.Time `json:"started_at"`
	Version   string    `json:"version"`
	Status    string    `json:"status"` // "active", "draining"
}

// Manager handles node registration, heartbeat, and discovery.
// This initial implementation uses an in-memory registry suitable for
// single-node operation. The interface supports future Redis backing.
type Manager struct {
	mu       sync.RWMutex
	node     Node
	nodes    map[string]*Node
	ttl      time.Duration
	interval time.Duration
	drainTTL time.Duration
	stopCh   chan struct{}
	stopped  bool
}

// NewManager creates a new cluster manager from config.
func NewManager(cfg config.ClusterConfig, addr, version string) *Manager {
	nodeID := cfg.NodeID
	if nodeID == "" {
		nodeID = generateNodeID()
	}

	interval := cfg.HeartbeatInterval
	if interval <= 0 {
		interval = 10 * time.Second
	}

	ttl := cfg.HeartbeatTTL
	if ttl <= 0 {
		ttl = 30 * time.Second
	}

	drainTTL := cfg.DrainTimeout
	if drainTTL <= 0 {
		drainTTL = 30 * time.Second
	}

	m := &Manager{
		node: Node{
			ID:        nodeID,
			Addr:      addr,
			StartedAt: time.Now(),
			Version:   version,
			Status:    "active",
		},
		nodes:    make(map[string]*Node),
		ttl:      ttl,
		interval: interval,
		drainTTL: drainTTL,
		stopCh:   make(chan struct{}),
	}

	// Register self.
	m.nodes[nodeID] = &m.node

	return m
}

// Start begins the heartbeat goroutine.
func (m *Manager) Start() {
	slog.Info("cluster node started",
		"node_id", m.node.ID,
		"addr", m.node.Addr,
		"heartbeat_interval", m.interval,
		"ttl", m.ttl,
	)

	go m.heartbeatLoop()
}

// Stop deregisters the node and stops the heartbeat.
func (m *Manager) Stop() {
	m.mu.Lock()
	if m.stopped {
		m.mu.Unlock()
		return
	}
	m.stopped = true
	close(m.stopCh)
	m.mu.Unlock()

	slog.Info("cluster node stopping", "node_id", m.node.ID)
}

// Drain marks this node as draining (no new requests).
func (m *Manager) Drain() {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.node.Status = "draining"
	slog.Info("cluster node draining", "node_id", m.node.ID)
}

// IsDraining returns true if this node is in drain mode.
func (m *Manager) IsDraining() bool {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return m.node.Status == "draining"
}

// NodeID returns this node's ID.
func (m *Manager) NodeID() string {
	return m.node.ID
}

// ListNodes returns all active nodes.
func (m *Manager) ListNodes() []Node {
	m.mu.RLock()
	defer m.mu.RUnlock()

	nodes := make([]Node, 0, len(m.nodes))
	for _, n := range m.nodes {
		nodes = append(nodes, *n)
	}
	return nodes
}

// NodeCount returns the number of known nodes.
func (m *Manager) NodeCount() int {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return len(m.nodes)
}

// heartbeatLoop periodically refreshes the node's TTL.
func (m *Manager) heartbeatLoop() {
	ticker := time.NewTicker(m.interval)
	defer ticker.Stop()

	for {
		select {
		case <-m.stopCh:
			m.mu.Lock()
			delete(m.nodes, m.node.ID)
			m.mu.Unlock()
			slog.Info("cluster node deregistered", "node_id", m.node.ID)
			return
		case <-ticker.C:
			// In-memory: nothing to refresh. In Redis-backed implementation,
			// this would SETEX the node key.
			slog.Debug("cluster heartbeat", "node_id", m.node.ID)
		}
	}
}

// generateNodeID creates a unique node ID from hostname + random suffix.
func generateNodeID() string {
	hostname, err := os.Hostname()
	if err != nil {
		hostname = "unknown"
	}

	b := make([]byte, 4)
	if _, err := rand.Read(b); err != nil {
		panic("crypto/rand.Read failed: " + err.Error())
	}
	suffix := hex.EncodeToString(b)

	return hostname + "-" + suffix
}
