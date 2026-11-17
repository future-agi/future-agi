package realtime

import (
	"log/slog"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"
)

// providerCloseGrace is how long a provider that failed a write has to close its side; once it
// has, delivering what it sent is bounded by the client write deadline instead.
const providerCloseGrace = 5 * time.Second

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
	// single client-reading goroutine, so it needs no locking of its own. An error refuses the
	// message: it is not relayed, and the client is closed with a policy violation.
	OnClientMessage func(messageType int, data []byte) error
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
	// clientDraining is closed once writeToClient has delivered everything and asked the client
	// to close; readFromClient then discards what the client still sends until it answers, and
	// closes clientReadDone when it returns.
	clientDraining chan struct{}
	clientReadDone chan struct{}
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
		clientDraining:   make(chan struct{}),
		clientReadDone:   make(chan struct{}),
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
	defer close(r.clientReadDone)

	r.session.ClientConn.SetReadLimit(r.config.MaxMessageSize)
	r.config.keepReading(r.session.ClientConn)

	for {
		messageType, data, err := r.session.ClientConn.ReadMessage()
		if err != nil {
			if r.draining() {
				return // writeToClient ends the session once the client has answered its close
			}
			if websocket.IsCloseError(err, websocket.CloseNormalClosure, websocket.CloseGoingAway) {
				r.logger.Info("client closed connection", "session_id", r.session.ID)
			} else if !r.session.IsClosed() {
				r.logger.Warn("client read error", "session_id", r.session.ID, "error", err)
			}
			r.session.Close("client_disconnected")
			return
		}
		if r.draining() {
			continue // nobody is left to send it to
		}
		r.session.ClientConn.SetReadDeadline(time.Now().Add(r.config.idleTimeout()))
		r.session.Usage.IncrementMessages()
		if r.config.OnClientMessage != nil {
			if err := r.config.OnClientMessage(messageType, data); err != nil {
				r.refuseClient(err)
				return
			}
		}

		select {
		case r.clientToProvider <- relayMessage{messageType, data}:
		case <-r.clientDraining:
		case <-r.session.StopChan():
			return
		}
	}
}

// refuseClient ends the session over a client message the relay will not forward, telling the
// client why in the close frame.
func (r *Relay) refuseClient(err error) {
	reason := err.Error()
	if len(reason) > maxCloseReason {
		reason = strings.ToValidUTF8(reason[:maxCloseReason], "")
	}
	r.session.ClientConn.WriteControl(websocket.CloseMessage,
		websocket.FormatCloseMessage(websocket.ClosePolicyViolation, reason),
		time.Now().Add(clientCloseTimeout))
	r.session.Close("client_message_refused")
}

// maxCloseReason is the most a close frame's reason can hold: a control frame's 125 bytes less
// the status code.
const maxCloseReason = 123

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
				// A write can fail because the provider is closing before readFromProvider has
				// seen its close, with final results still unread or queued. Ending the session
				// here would drop them, so close the provider's side instead: readFromProvider
				// then reads to the end and writeToClient delivers what is queued before ending
				// the session. The grace period bounds a provider that never answers the close.
				if !r.providerClosed.Load() && !r.session.IsClosed() {
					r.logger.Warn("provider write error", "session_id", r.session.ID, "error", err)
				}
				r.session.ProviderConn.WriteControl(websocket.CloseMessage,
					websocket.FormatCloseMessage(websocket.CloseNormalClosure, ""),
					time.Now().Add(2*time.Second))
				time.AfterFunc(providerCloseGrace, func() {
					if !r.providerClosed.Load() {
						r.session.Close("provider_write_error")
					}
				})
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
				r.closeClient()
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

// clientCloseTimeout bounds how long the client has to answer the relay's close.
const clientCloseTimeout = 2 * time.Second

// closeClient completes the close handshake with the client before the session drops the
// connection. Dropping it while the client is still sending audio makes the gateway's TCP stack
// reset the connection, and a reset discards whatever the client has not read yet: the last
// results just delivered.
func (r *Relay) closeClient() {
	if r.session.IsClosed() {
		return
	}
	close(r.clientDraining)
	r.session.ClientConn.WriteControl(websocket.CloseMessage,
		websocket.FormatCloseMessage(websocket.CloseNormalClosure, ""),
		time.Now().Add(clientCloseTimeout))
	select {
	case <-r.clientReadDone:
	case <-time.After(clientCloseTimeout):
	case <-r.session.StopChan():
	}
}

func (r *Relay) draining() bool {
	select {
	case <-r.clientDraining:
		return true
	default:
		return false
	}
}
