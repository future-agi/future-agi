"""Keep the socket boundary in Python child processes started by tests."""

from socket_guard import install

install()
