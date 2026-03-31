from contextlib import redirect_stdout
from io import StringIO

buffer = StringIO()


def test_simple_is_better_than_complex():
    with redirect_stdout(buffer):
        import this  # noqa: F401

    buffer.seek(0)
    assert "Simple is better than complex" in buffer.read()
