package cache

import (
	"bufio"
	"bytes"
	"compress/gzip"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// RedisBackend stores cached responses in Redis using the RESP protocol.
type RedisBackend struct {
	pool      *redisPool
	keyPrefix string
	compress  bool
}

// NewRedisBackend creates a Redis-backed cache.
func NewRedisBackend(address, password string, db int, keyPrefix string, compress bool, poolSize int, timeout time.Duration) (*RedisBackend, error) {
	if poolSize <= 0 {
		poolSize = 10
	}
	if timeout <= 0 {
		timeout = 5 * time.Second
	}

	pool, err := newRedisPool(address, password, db, poolSize, timeout)
	if err != nil {
		return nil, fmt.Errorf("redis: %w", err)
	}

	return &RedisBackend{
		pool:      pool,
		keyPrefix: keyPrefix,
		compress:  compress,
	}, nil
}

func (r *RedisBackend) Get(key string) (*models.ChatCompletionResponse, bool) {
	conn, err := r.pool.get()
	if err != nil {
		slog.Warn("redis cache get: pool error", "error", err)
		return nil, false
	}
	defer r.pool.put(conn)

	data, err := conn.do("GET", r.keyPrefix+key)
	if err != nil || data == nil {
		return nil, false
	}

	body, ok := data.([]byte)
	if !ok {
		return nil, false
	}

	if r.compress {
		gr, err := gzip.NewReader(bytes.NewReader(body))
		if err != nil {
			return nil, false
		}
		body, err = io.ReadAll(gr)
		gr.Close()
		if err != nil {
			return nil, false
		}
	}

	var resp models.ChatCompletionResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		return nil, false
	}
	return &resp, true
}

func (r *RedisBackend) Set(key string, resp *models.ChatCompletionResponse, ttl time.Duration) {
	if ttl <= 0 || resp == nil {
		return
	}

	data, err := json.Marshal(resp)
	if err != nil {
		return
	}

	var body []byte
	if r.compress {
		var buf bytes.Buffer
		gw := gzip.NewWriter(&buf)
		gw.Write(data)
		gw.Close()
		body = buf.Bytes()
	} else {
		body = data
	}

	conn, err := r.pool.get()
	if err != nil {
		slog.Warn("redis cache set: pool error", "error", err)
		return
	}
	defer r.pool.put(conn)

	ttlSec := int(ttl.Seconds())
	if ttlSec <= 0 {
		ttlSec = 1
	}

	_, err = conn.do("SET", r.keyPrefix+key, string(body), "EX", strconv.Itoa(ttlSec))
	if err != nil {
		slog.Warn("redis cache set error", "key", key, "error", err)
	}
}

func (r *RedisBackend) Delete(key string) {
	conn, err := r.pool.get()
	if err != nil {
		return
	}
	defer r.pool.put(conn)

	conn.do("DEL", r.keyPrefix+key)
}

// Close closes all connections in the pool.
func (r *RedisBackend) Close() {
	r.pool.close()
}

// --- Minimal RESP protocol client ---

type redisConn struct {
	conn    net.Conn
	reader  *bufio.Reader
	timeout time.Duration
}

type redisPool struct {
	address  string
	password string
	db       int
	timeout  time.Duration
	mu       sync.Mutex
	conns    []*redisConn
	maxSize  int
}

func newRedisPool(address, password string, db, poolSize int, timeout time.Duration) (*redisPool, error) {
	pool := &redisPool{
		address:  address,
		password: password,
		db:       db,
		timeout:  timeout,
		conns:    make([]*redisConn, 0, poolSize),
		maxSize:  poolSize,
	}

	// Test connection.
	conn, err := pool.dial()
	if err != nil {
		return nil, fmt.Errorf("connect to %s: %w", address, err)
	}
	pool.conns = append(pool.conns, conn)
	return pool, nil
}

func (p *redisPool) dial() (*redisConn, error) {
	conn, err := net.DialTimeout("tcp", p.address, p.timeout)
	if err != nil {
		return nil, err
	}
	rc := &redisConn{
		conn:    conn,
		reader:  bufio.NewReader(conn),
		timeout: p.timeout,
	}

	// AUTH if password is set.
	if p.password != "" {
		_, err := rc.do("AUTH", p.password)
		if err != nil {
			conn.Close()
			return nil, fmt.Errorf("auth: %w", err)
		}
	}

	// SELECT db if not 0.
	if p.db != 0 {
		_, err := rc.do("SELECT", strconv.Itoa(p.db))
		if err != nil {
			conn.Close()
			return nil, fmt.Errorf("select db %d: %w", p.db, err)
		}
	}

	return rc, nil
}

func (p *redisPool) get() (*redisConn, error) {
	p.mu.Lock()
	if len(p.conns) > 0 {
		conn := p.conns[len(p.conns)-1]
		p.conns = p.conns[:len(p.conns)-1]
		p.mu.Unlock()
		return conn, nil
	}
	p.mu.Unlock()
	return p.dial()
}

func (p *redisPool) put(conn *redisConn) {
	p.mu.Lock()
	if len(p.conns) < p.maxSize {
		p.conns = append(p.conns, conn)
		p.mu.Unlock()
		return
	}
	p.mu.Unlock()
	conn.conn.Close()
}

func (p *redisPool) close() {
	p.mu.Lock()
	defer p.mu.Unlock()
	for _, c := range p.conns {
		c.conn.Close()
	}
	p.conns = nil
}

// do sends a RESP command and reads the reply.
func (rc *redisConn) do(args ...string) (interface{}, error) {
	rc.conn.SetDeadline(time.Now().Add(rc.timeout))

	// Write RESP array. Use explicit length-prefixed writes instead of
	// fmt.Sprintf to handle binary payloads that may contain \r\n.
	var buf bytes.Buffer
	buf.WriteString(fmt.Sprintf("*%d\r\n", len(args)))
	for _, arg := range args {
		buf.WriteString(fmt.Sprintf("$%d\r\n", len(arg)))
		buf.WriteString(arg)
		buf.WriteString("\r\n")
	}
	if _, err := rc.conn.Write(buf.Bytes()); err != nil {
		return nil, err
	}

	return rc.readReply()
}

func (rc *redisConn) readReply() (interface{}, error) {
	line, err := rc.reader.ReadString('\n')
	if err != nil {
		return nil, err
	}
	line = strings.TrimRight(line, "\r\n")

	if len(line) == 0 {
		return nil, fmt.Errorf("empty reply")
	}

	switch line[0] {
	case '+': // Simple string
		return line[1:], nil
	case '-': // Error
		return nil, fmt.Errorf("redis: %s", line[1:])
	case ':': // Integer
		n, err := strconv.ParseInt(line[1:], 10, 64)
		return n, err
	case '$': // Bulk string
		n, err := strconv.Atoi(line[1:])
		if err != nil {
			return nil, err
		}
		if n < 0 {
			return nil, nil // nil bulk string
		}
		data := make([]byte, n+2) // +2 for \r\n
		_, err = io.ReadFull(rc.reader, data)
		if err != nil {
			return nil, err
		}
		return data[:n], nil
	case '*': // Array
		n, err := strconv.Atoi(line[1:])
		if err != nil {
			return nil, err
		}
		if n < 0 {
			return nil, nil
		}
		arr := make([]interface{}, n)
		for i := 0; i < n; i++ {
			arr[i], err = rc.readReply()
			if err != nil {
				return nil, err
			}
		}
		return arr, nil
	default:
		return nil, fmt.Errorf("unknown RESP type: %c", line[0])
	}
}
