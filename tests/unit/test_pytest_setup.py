import socket

import pytest
from pytest_socket import SocketBlockedError


@pytest.mark.anyio
async def test_async_tests_run_with_sockets_disabled() -> None:
    assert True


@pytest.mark.filterwarnings("ignore:A test tried to use")
def test_tcp_is_blocked() -> None:
    with pytest.raises(SocketBlockedError), socket.socket() as sock:
        sock.connect(("127.0.0.1", 9))
