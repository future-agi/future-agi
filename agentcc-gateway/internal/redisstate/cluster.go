package redisstate

import (
	"context"
	"encoding/json"
	"log/slog"
	"time"

	"github.com/redis/go-redis/v9"
)

// ClusterNode represents a gateway node for Redis-backed discovery.
type ClusterNode struct {
	ID        string    `json:"id"`
	Addr      string    `json:"addr"`
	StartedAt time.Time `json:"started_at"`
	Version   string    `json:"version"`
	Status    string    `json:"status"`
}

// ClusterStore provides Redis-backed cluster coordination using SETEX
// for heartbeats and SCAN for node discovery.
type ClusterStore struct {
	client *Client
	prefix string
	ttl    time.Duration
}

// NewClusterStore creates a Redis-backed cluster store.
func NewClusterStore(client *Client, ttl time.Duration, prefix string) *ClusterStore {
	if prefix == "" {
		prefix = "agentcc:cluster:node:"
	}
	if ttl <= 0 {
		ttl = 30 * time.Second
	}
	return &ClusterStore{client: client, prefix: prefix, ttl: ttl}
}

// Heartbeat registers or refreshes this node's presence.
// Returns false if Redis is unavailable (caller continues as standalone).
func (s *ClusterStore) Heartbeat(node ClusterNode) bool {
	if s.client == nil || !s.client.Available() {
		return false
	}

	data, err := json.Marshal(node)
	if err != nil {
		return false
	}

	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()

	err = s.client.Do(func(rdb redis.UniversalClient) error {
		return rdb.SetEx(ctx, s.prefix+node.ID, string(data), s.ttl).Err()
	})

	if err != nil {
		slog.Debug("redis cluster heartbeat failed", "node", node.ID, "error", err)
		return false
	}
	return true
}

// Deregister removes this node from the cluster.
func (s *ClusterStore) Deregister(nodeID string) {
	if s.client == nil {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	s.client.Do(func(rdb redis.UniversalClient) error {
		return rdb.Del(ctx, s.prefix+nodeID).Err()
	})
}

// ListNodes returns all registered nodes using SCAN.
// Returns (nil, false) if Redis is unavailable.
func (s *ClusterStore) ListNodes() ([]ClusterNode, bool) {
	if s.client == nil || !s.client.Available() {
		return nil, false
	}

	var nodes []ClusterNode
	ctx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
	defer cancel()

	err := s.client.Do(func(rdb redis.UniversalClient) error {
		var cursor uint64
		for {
			keys, next, err := rdb.Scan(ctx, cursor, s.prefix+"*", 100).Result()
			if err != nil {
				return err
			}
			if len(keys) > 0 {
				vals, err := rdb.MGet(ctx, keys...).Result()
				if err != nil {
					return err
				}
				for _, v := range vals {
					if v == nil {
						continue
					}
					str, ok := v.(string)
					if !ok {
						continue
					}
					var node ClusterNode
					if json.Unmarshal([]byte(str), &node) == nil {
						nodes = append(nodes, node)
					}
				}
			}
			cursor = next
			if cursor == 0 {
				break
			}
		}
		return nil
	})

	if err != nil {
		return nil, false
	}
	return nodes, true
}

// Available returns true if Redis is reachable.
func (s *ClusterStore) Available() bool {
	return s.client != nil && s.client.Available()
}
