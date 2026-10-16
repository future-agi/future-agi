package realtime

import (
	"log/slog"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"
)

// relayMessage holds a WebSocket message to be relayed.
type relayMessage struct {
	messageType int
	data        []byte
}

// RelayConfig holds configuration for the relay.
type RelayConfig struct {
	ChannelBufferSize int
	PingInterval      time.Duration
	PongTimeout       time.Duration
	// IdleTimeout closes a side that sends neither a message nor a pong for this long.
	// Zero means PingInterval+PongTimeout, which suits peers that answer pings while idle.
	IdleTimeout    time.Duration
	MaxMessageSize int64
	// OnClientMessage, when set, sees every client message before it is relayed. It runs on the
	// single client-reading goroutine, so it needs no locking of its own.
	OnClientMessage func(messageType int, data []byte)
}

func (c RelayConfig) idleTimeout() time.Duration {
	if c.IdleTimeout > 0 {
		return c.IdleTimeout
	}
	return c.PingInterval + c.PongTimeout
}

// keepReading extends conn's read deadline now, and again on every pong.
func (c RelayConfig) keepReading(conn *websocket.Conn) {
	conn.SetReadDeadline(time.Now().Add(c.idleTimeout()))
	conn.SetPongHandler(func(string) error {
		return conn.SetReadDeadline(time.Now().Add(c.idleTimeout()))
	})
}

// Relay bidirectionally forwards WebSocket messages between client and provider.
type Relay struct {
	session          *Session
	clientToProvider chan relayMessage
	providerToClient chan relayMessage
	config           RelayConfig
	logger           *slog.Logger
	wg               sync.WaitGroup
	// providerClosed is set once the provider's side has ended; from then on writeToClient
	// delivers what is still queued and ends the session.
	providerClosed atomic.Bool
}

// NewRelay creates a new message relay.
func NewRelay(session *Session, config RelayConfig, logger *slog.Logger) *Relay {
	if config.ChannelBufferSize == 0 {
		config.ChannelBufferSize = 64
	}
	return &Relay{
		session:          session,
		clientToProvider: make(chan relayMessage, config.ChannelBufferSize),
		providerToClient: make(chan relayMessage, config.ChannelBufferSize),
		config:           config,
		logger:           logger,
	}
}

// Start begins relaying messages. Blocks until the session is done.
func (r *Relay) Start() {
	r.wg.Add(4)
	go r.readFromClient()
	go r.writeToProvider()
	go r.readFromProvider()
	go r.writeToClient()
	if r.config.PingInterval > 0 {
		go r.ping()
	}
	r.wg.Wait()
}

// ping keeps a quiet session alive: a pong extends the peer's read deadline, and the traffic
// keeps intermediaries from dropping an idle connection.
func (r *Relay) ping() {
	ticker := time.NewTicker(r.config.PingInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ticker.C:
			deadline := time.Now().Add(r.config.PongTimeout)
			for _, conn := range []*websocket.Conn{r.session.ClientConn, r.session.ProviderConn} {
				if err := conn.WriteControl(websocket.PingMessage, nil, deadline); err != nil && !r.session.IsClosed() {
					r.logger.Warn("ping failed", "session_id", r.session.ID, "error", err)
				}
			}
		case <-r.session.StopChan():
			return
		}
	}
}

func (r *Relay) readFromClient() {
	defer r.wg.Done()
	defer close(r.clientToProvider)

	r.session.ClientConn.SetReadLimit(r.config.MaxMessageSize)
	r.config.keepReading(r.session.ClientConn)

	for {
		messageType, data, err := r.session.ClientConn.ReadMessage()
		if err != nil {
			if websocket.IsCloseError(err, websocket.CloseNormalClosure, websocket.CloseGoingAway) {
				r.logger.Info("client closed connection", "session_id", r.session.ID)
			} else if !r.session.IsClosed() {
				r.logger.Warn("client read error", "session_id", r.session.ID, "error", err)
			}
			r.session.Close("client_disconnected")
			return
		}
		r.session.ClientConn.SetReadDeadline(time.Now().Add(r.config.idleTimeout()))
		r.session.Usage.IncrementMessages()
		if r.config.OnClientMessage != nil {
			r.config.OnClientMessage(messageType, data)
		}

		select {
		case r.clientToProvider <- relayMessage{messageType, data}:
		case <-r.session.StopChan():
			return
		}
	}
}

func (r *Relay) writeToProvider() {
	defer r.wg.Done()

	for {
		select {
		case msg, ok := <-r.clientToProvider:
			if !ok {
				// Channel closed, send close frame.
				r.session.ProviderConn.WriteControl(websocket.CloseMessage,
					websocket.FormatCloseMessage(websocket.CloseNormalClosure, ""),
					time.Now().Add(2*time.Second))
				return
			}
			r.session.ProviderConn.SetWriteDeadline(time.Now().Add(10 * time.Second))
			if err := r.session.ProviderConn.WriteMessage(msg.messageType, msg.data); err != nil {
				// A message written after the provider closed must not cut short the delivery of
				// what the provider sent before closing; writeToClient ends the session.
				if r.providerClosed.Load() {
					return
				}
				if !r.session.IsClosed() {
					r.logger.Warn("provider write error", "session_id", r.session.ID, "error", err)
				}
				r.session.Close("provider_write_error")
				return
			}
		case <-r.session.StopChan():
			return
		}
	}
}

func (r *Relay) readFromProvider() {
	defer r.wg.Done()
	defer close(r.providerToClient)

	r.session.ProviderConn.SetReadLimit(r.config.MaxMessageSize)
	r.config.keepReading(r.session.ProviderConn)

	for {
		messageType, data, err := r.session.ProviderConn.ReadMessage()
		if err != nil {
			if websocket.IsCloseError(err, websocket.CloseNormalClosure, websocket.CloseGoingAway) {
				r.logger.Info("provider closed connection", "session_id", r.session.ID)
			} else if !r.session.IsClosed() {
				r.logger.Warn("provider read error", "session_id", r.session.ID, "error", err)
			}
			// Speech vendors send their final results right before closing, so the session ends
			// only after writeToClient has delivered everything queued.
			r.providerClosed.Store(true)
			return
		}
		r.session.ProviderConn.SetReadDeadline(time.Now().Add(r.config.idleTimeout()))
		r.session.Usage.IncrementMessages()

		select {
		case r.providerToClient <- relayMessage{messageType, data}:
		case <-r.session.StopChan():
			return
		}
	}
}

func (r *Relay) writeToClient() {
	defer r.wg.Done()

	for {
		select {
		case msg, ok := <-r.providerToClient:
			if !ok {
				r.session.Close("provider_disconnected")
				return
			}
			r.session.ClientConn.SetWriteDeadline(time.Now().Add(10 * time.Second))
			if err := r.session.ClientConn.WriteMessage(msg.messageType, msg.data); err != nil {
				if !r.session.IsClosed() {
					r.logger.Warn("client write error", "session_id", r.session.ID, "error", err)
				}
				r.session.Close("client_write_error")
				return
			}
		case <-r.session.StopChan():
			return
		}
	}
}
