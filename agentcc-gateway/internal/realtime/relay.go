package realtime

import (
	"log/slog"
	"sync"
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
	MaxMessageSize    int64
}

// Relay bidirectionally forwards WebSocket messages between client and provider.
type Relay struct {
	session          *Session
	clientToProvider chan relayMessage
	providerToClient chan relayMessage
	config           RelayConfig
	logger           *slog.Logger
	wg               sync.WaitGroup
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
	r.wg.Wait()
}

func (r *Relay) readFromClient() {
	defer r.wg.Done()
	defer close(r.clientToProvider)

	r.session.ClientConn.SetReadLimit(r.config.MaxMessageSize)
	r.session.ClientConn.SetPongHandler(func(string) error {
		r.session.ClientConn.SetReadDeadline(time.Now().Add(r.config.PongTimeout + r.config.PingInterval))
		return nil
	})
	r.session.ClientConn.SetReadDeadline(time.Now().Add(r.config.PongTimeout + r.config.PingInterval))

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
		r.session.Usage.IncrementMessages()

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
	r.session.ProviderConn.SetPongHandler(func(string) error {
		r.session.ProviderConn.SetReadDeadline(time.Now().Add(r.config.PongTimeout + r.config.PingInterval))
		return nil
	})
	r.session.ProviderConn.SetReadDeadline(time.Now().Add(r.config.PongTimeout + r.config.PingInterval))

	for {
		messageType, data, err := r.session.ProviderConn.ReadMessage()
		if err != nil {
			if websocket.IsCloseError(err, websocket.CloseNormalClosure, websocket.CloseGoingAway) {
				r.logger.Info("provider closed connection", "session_id", r.session.ID)
			} else if !r.session.IsClosed() {
				r.logger.Warn("provider read error", "session_id", r.session.ID, "error", err)
			}
			r.session.Close("provider_disconnected")
			return
		}
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
				r.session.ClientConn.WriteControl(websocket.CloseMessage,
					websocket.FormatCloseMessage(websocket.CloseNormalClosure, ""),
					time.Now().Add(2*time.Second))
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
