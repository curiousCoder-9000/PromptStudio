"""The hand-rolled multipart parser (stdlib `cgi` is gone in 3.13+).

This is the only code path for `POST /api/photo/upload`, so a parsing slip
means corrupted image bytes on disk rather than a visible error.
"""

import io

from promptstudio.server.multipart import parse_multipart_data

BOUNDARY = "----PromptStudioBoundary7MA4YWxkTrZu0gW"


class FakeHeaders(dict):
    """Minimal stand-in for http.client.HTTPMessage."""

    def get(self, key, default=None):
        for k, v in self.items():
            if k.lower() == key.lower():
                return v
        return default


def build_body(parts):
    """parts: list of (name, filename|None, content bytes)."""
    out = b""
    for name, filename, content in parts:
        out += f"--{BOUNDARY}\r\n".encode()
        disp = f'Content-Disposition: form-data; name="{name}"'
        if filename is not None:
            disp += f'; filename="{filename}"'
        out += disp.encode() + b"\r\n"
        if filename is not None:
            out += b"Content-Type: application/octet-stream\r\n"
        out += b"\r\n" + content + b"\r\n"
    out += f"--{BOUNDARY}--\r\n".encode()
    return out


def parse(body):
    headers = FakeHeaders(
        {
            "Content-Type": f"multipart/form-data; boundary={BOUNDARY}",
            "Content-Length": str(len(body)),
        }
    )
    return parse_multipart_data(io.BytesIO(body), headers)


def test_parses_a_single_text_field():
    fields, files = parse(build_body([("creator", None, b"alexa_model")]))
    assert fields == {"creator": "alexa_model"}
    assert files == {}


def test_parses_field_plus_file():
    body = build_body(
        [
            ("creator", None, b"alexa_model"),
            ("file", "shot.jpg", b"\xff\xd8\xff\xe0JFIFbytes\x00\x01"),
        ]
    )
    fields, files = parse(body)
    assert fields["creator"] == "alexa_model"
    assert files["file"]["filename"] == "shot.jpg"
    assert files["file"]["content"] == b"\xff\xd8\xff\xe0JFIFbytes\x00\x01"


def test_binary_content_is_preserved_byte_for_byte():
    # Every byte value, including CR/LF and the NUL byte
    blob = bytes(range(256)) * 4
    _, files = parse(build_body([("file", "all_bytes.bin", blob)]))
    assert files["file"]["content"] == blob
    assert len(files["file"]["content"]) == len(blob)


def test_content_containing_crlf_survives():
    blob = b"line1\r\nline2\r\nline3"
    _, files = parse(build_body([("file", "text.bin", blob)]))
    assert files["file"]["content"] == blob


def test_empty_file_content():
    _, files = parse(build_body([("file", "empty.jpg", b"")]))
    assert files["file"]["content"] == b""
    assert files["file"]["filename"] == "empty.jpg"


def test_utf8_field_value():
    fields, _ = parse(build_body([("creator", None, "café_模型".encode())]))
    assert fields["creator"] == "café_模型"


def test_multiple_fields_and_files():
    body = build_body(
        [
            ("creator", None, b"c1"),
            ("note", None, b"hello"),
            ("file", "a.jpg", b"AAA"),
        ]
    )
    fields, files = parse(body)
    assert fields == {"creator": "c1", "note": "hello"}
    assert set(files) == {"file"}


def test_part_without_content_disposition_is_skipped():
    body = (
        f"--{BOUNDARY}\r\n".encode()
        + b"X-Not-Disposition: nope\r\n\r\nignored\r\n"
        + f"--{BOUNDARY}\r\n".encode()
        + b'Content-Disposition: form-data; name="creator"\r\n\r\nkept\r\n'
        + f"--{BOUNDARY}--\r\n".encode()
    )
    fields, files = parse(body)
    assert fields == {"creator": "kept"}
    assert files == {}


def test_empty_body_yields_nothing():
    fields, files = parse(b"")
    assert fields == {}
    assert files == {}


def test_filename_with_spaces_and_unicode():
    _, files = parse(build_body([("file", "my photo café.jpg", b"x")]))
    assert files["file"]["filename"] == "my photo café.jpg"
