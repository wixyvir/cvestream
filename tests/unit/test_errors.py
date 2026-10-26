from cvestream.errors import CvestreamError


def test_cvestream_error_is_an_exception() -> None:
    assert issubclass(CvestreamError, Exception)
    assert str(CvestreamError("boom")) == "boom"
