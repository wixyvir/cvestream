from collections.abc import Iterator

import pytest
from valkey import Valkey

from tests.integration import valkey_support as support


@pytest.fixture
def admin() -> Iterator[Valkey]:
    """A `cvestream-admin` client; the namespace is empty before and after."""
    support.require_server()
    connection = support.client(support.ADMIN)
    support.wipe(connection)
    yield connection
    support.wipe(connection)
    connection.close()
