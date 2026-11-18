// Package testhttp runs httptest servers over in-memory connections. Tests can
// exercise net/http without requiring TCP listeners or external network access.
package testhttp

import (
	"context"
	"net"
	"net/http"
	"net/http/httptest"
	"sync"
)

type Server struct {
	*httptest.Server
	listener *listener
}

func NewServer(handler http.Handler) *Server {
	l := &listener{connections: make(chan net.Conn), done: make(chan struct{})}
	srv := &httptest.Server{Listener: l, Config: &http.Server{Handler: handler}}
	srv.Start()
	return &Server{Server: srv, listener: l}
}

// DialContext replaces the provider's network dialer with an in-memory pipe.
func (s *Server) DialContext(ctx context.Context, network, address string) (net.Conn, error) {
	client, server := net.Pipe()
	select {
	case s.listener.connections <- server:
		return client, nil
	case <-ctx.Done():
		client.Close()
		server.Close()
		return nil, ctx.Err()
	case <-s.listener.done:
		client.Close()
		server.Close()
		return nil, net.ErrClosed
	}
}

type listener struct {
	connections chan net.Conn
	done        chan struct{}
	once        sync.Once
}

func (l *listener) Accept() (net.Conn, error) {
	select {
	case conn := <-l.connections:
		return conn, nil
	case <-l.done:
		return nil, net.ErrClosed
	}
}
func (l *listener) Close() error   { l.once.Do(func() { close(l.done) }); return nil }
func (l *listener) Addr() net.Addr { return &net.TCPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 80} }
