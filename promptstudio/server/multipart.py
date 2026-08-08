"""Multipart form parser for file uploads."""

import re


def parse_multipart_data(fp, headers):
    content_type = headers.get("Content-Type", "")
    boundary = content_type.split("boundary=")[-1].encode("utf-8")
    content_length = int(headers.get("Content-Length", 0))
    body = fp.read(content_length)

    parts = body.split(b"--" + boundary)
    fields = {}
    files = {}

    for part in parts:
        if not part or part == b"--\r\n" or part == b"--":
            continue
        if b"\r\n\r\n" not in part:
            continue
        header_part, content_part = part.split(b"\r\n\r\n", 1)
        content_part = content_part.rsplit(b"\r\n", 1)[0]
        header_text = header_part.decode("utf-8", errors="ignore")
        disposition = [h for h in header_text.split("\r\n") if "Content-Disposition" in h]
        if not disposition:
            continue
        disp_str = disposition[0]
        name_match = re.search(r'name="([^"]+)"', disp_str)
        filename_match = re.search(r'filename="([^"]+)"', disp_str)
        name = name_match.group(1) if name_match else None
        if filename_match and name:
            files[name] = {
                "filename": filename_match.group(1),
                "content": content_part,
            }
        elif name:
            fields[name] = content_part.decode("utf-8", errors="ignore").strip()
    return fields, files
