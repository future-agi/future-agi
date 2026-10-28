package server

import (
	"errors"
	"net"
	"sync"
	"sync/atomic"
)

// connLimitListener closes, at once, connections past max open ones, so a
// client past the limit gets an error it can retry instead of waiting in the
// accept queue for a slot. Used only when FI_OTLP_MAX_CONNECTIONS is set.
type connLimitListener struct {
	net.Listener
	max     int64
	open    atomic.Int64
	refused *atomic.Int64
}

func newConnLimitListener(l net.Listener, max int, refused *atomic.Int64) net.Listener {
	if max <= 0 {
		return l
	}
	return &connLimitListener{Listener: l, max: int64(max), refused: refused}
}

func (l *connLimitListener) Accept() (net.Conn, error) {
	for {
		c, err := l.Listener.Accept()
		if err != nil {
			return nil, err
		}
		if l.open.Add(1) > l.max {
			l.open.Add(-1)
			l.refused.Add(1)
			_ = c.Close()
			continue
		}
		return &limitedConn{Conn: c, l: l}, nil
	}
}

type limitedConn struct {
	net.Conn
	l    *connLimitListener
	once sync.Once
}

func (c *limitedConn) Close() error {
	err := c.Conn.Close()
	c.once.Do(func() { c.l.open.Add(-1) })
	return err
}

// frameGuardListener wraps every OTLP/gRPC connection in a frameGuardConn.
// It also keeps every open connection in conns, by remote address, so that
// grpcConnTracker can close one.
type frameGuardListener struct {
	net.Listener
	closed *atomic.Int64 // connections closed for small frames
	conns  *sync.Map     // remote address -> *frameGuardConn
}

func (l frameGuardListener) Accept() (net.Conn, error) {
	c, err := l.Listener.Accept()
	if err != nil {
		return nil, err
	}
	fg := &frameGuardConn{Conn: c, preface: len(http2ClientPreface), streams: map[uint32]int64{}, closed: l.closed, conns: l.conns}
	if l.conns != nil {
		l.conns.Store(c.RemoteAddr().String(), fg)
	}
	return fg, nil
}

// HTTP/2 framing, as much of it as frameGuardConn reads (RFC 9113).
const (
	http2ClientPreface = "PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"
	http2FrameHeader   = 9
	http2FrameData     = 0x0
	http2FrameHeaders  = 0x1
	http2FrameRST      = 0x3
	http2FlagEndStream = 0x1
	http2FlagPadded    = 0x8
)

// Small DATA frames on OTLP/gRPC. The transport queues frames until the
// handler reads the message, so frames much smaller than clients normally
// send are limited.
// gRPC clients send frames of up to 16 KiB; a frame under smallDataFrame
// ends a message, or the stream, or sits at a flow-control window boundary.
// Each stream may send smallFrameCredit such frames, plus one per
// smallFrameEvery bytes of its data; a connection whose stream sends more is
// closed. The guard keeps per-stream state for at most guardStreams streams,
// forgetting the oldest (lowest id) first.
const (
	smallDataFrame   = 1 << 10
	smallFrameCredit = 8
	smallFrameEvery  = 16 << 10
	guardStreams     = 4 * grpcMaxStreamsPerConn
)

var errSmallFrames = errors.New("otlp grpc: too many small DATA frames on a stream")

// frameGuardConn reads the HTTP/2 frame headers a client sends, as grpc-go
// reads them, and fails the read (which closes the connection) when a
// stream sends more small DATA frames than it may. It reads nothing more
// than the bytes grpc-go asks for and keeps a few bytes of state per stream.
type frameGuardConn struct {
	net.Conn
	preface int // bytes of the client preface still to pass
	hdr     [http2FrameHeader]byte
	hdrN    int
	payload int64 // bytes of the current frame's payload still to pass
	// streams is the small-frame credit of each stream that sent DATA and
	// has not ended.
	streams map[uint32]int64
	err     error
	closed  *atomic.Int64
	conns   *sync.Map
	once    sync.Once
}

func (c *frameGuardConn) Close() error {
	c.once.Do(func() {
		if c.conns != nil {
			c.conns.CompareAndDelete(c.Conn.RemoteAddr().String(), c)
		}
	})
	return c.Conn.Close()
}

func (c *frameGuardConn) Read(p []byte) (int, error) {
	if c.err != nil {
		return 0, c.err
	}
	n, err := c.Conn.Read(p)
	if n > 0 {
		if gerr := c.observe(p[:n]); gerr != nil {
			c.err = gerr
			if c.closed != nil {
				c.closed.Add(1)
			}
			return 0, gerr
		}
	}
	return n, err
}

func (c *frameGuardConn) observe(b []byte) error {
	for len(b) > 0 {
		switch {
		case c.preface > 0:
			k := min(c.preface, len(b))
			c.preface -= k
			b = b[k:]
		case c.payload > 0:
			k := int(min(c.payload, int64(len(b))))
			c.payload -= int64(k)
			b = b[k:]
		default:
			k := copy(c.hdr[c.hdrN:], b)
			c.hdrN += k
			b = b[k:]
			if c.hdrN < http2FrameHeader {
				return nil
			}
			c.hdrN = 0
			if err := c.frame(); err != nil {
				return err
			}
		}
	}
	return nil
}

// frame looks at one frame header.
func (c *frameGuardConn) frame() error {
	h := c.hdr
	length := int64(h[0])<<16 | int64(h[1])<<8 | int64(h[2])
	typ, flags := h[3], h[4]
	id := (uint32(h[5])<<24 | uint32(h[6])<<16 | uint32(h[7])<<8 | uint32(h[8])) & 0x7fffffff
	c.payload = length
	switch typ {
	case http2FrameData:
		if flags&http2FlagEndStream != 0 {
			delete(c.streams, id)
			return nil
		}
		credit, ok := c.streams[id]
		if !ok {
			c.forgetOldest()
			credit = smallFrameCredit
		}
		credit += length / smallFrameEvery
		if length < smallDataFrame || flags&http2FlagPadded != 0 {
			credit--
			if credit < 0 {
				return errSmallFrames
			}
		}
		c.streams[id] = credit
	case http2FrameHeaders:
		if flags&http2FlagEndStream != 0 {
			delete(c.streams, id)
		}
	case http2FrameRST:
		delete(c.streams, id)
	}
	return nil
}

// forgetOldest makes room for one more stream.
func (c *frameGuardConn) forgetOldest() {
	if len(c.streams) < guardStreams {
		return
	}
	oldest := ^uint32(0)
	for id := range c.streams {
		oldest = min(oldest, id)
	}
	delete(c.streams, oldest)
}
