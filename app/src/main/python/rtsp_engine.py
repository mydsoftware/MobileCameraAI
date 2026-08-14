import hashlib
import re
import socket
from urllib.parse import unquote


def _read_response(sock):
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
        if len(data) > 1024 * 1024:
            raise RuntimeError("RTSP response too large")
    header, _, body = data.partition(b"\r\n\r\n")
    headers = header.decode("utf-8", "replace")
    m = re.search(r"Content-Length:\s*(\d+)", headers, re.I)
    if m:
        length = int(m.group(1))
        while len(body) < length:
            chunk = sock.recv(min(4096, length - len(body)))
            if not chunk:
                break
            body += chunk
    return headers, body


def _header(headers, name):
    m = re.search(rf"^{re.escape(name)}:\s*(.+)$", headers, re.I | re.M)
    return m.group(1).strip() if m else ""


def _digest_params(value):
    params = {}
    for m in re.finditer(r'(\w+)\s*=\s*("([^"]*)"|([^,\s]+))', value):
        params[m.group(1).lower()] = m.group(3) if m.group(3) is not None else m.group(4)
    return params


def _make_digest(username, password, method, uri, challenge, nc="00000001", cnonce=""):
    p = _digest_params(challenge)
    realm = p.get("realm", "")
    nonce = p.get("nonce", "")
    qop = p.get("qop", "")
    algorithm = p.get("algorithm", "MD5").upper()
    if algorithm != "MD5":
        raise RuntimeError(f"Unsupported RTSP digest algorithm: {algorithm}")

    ha1 = hashlib.md5(f"{username}:{realm}:{password}".encode()).hexdigest()
    ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
    qop_value = qop.split(",")[0].strip() if qop else ""
    if qop_value:
        response = hashlib.md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop_value}:{ha2}".encode()).hexdigest()
    else:
        response = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()

    auth = f'Digest username="{username}", realm="{realm}", nonce="{nonce}", uri="{uri}", response="{response}"'
    if p.get("opaque"):
        auth += f', opaque="{p["opaque"]}"'
    if qop_value:
        auth += f', qop={qop_value}, nc={nc}, cnonce="{cnonce}"'
    if p.get("algorithm"):
        auth += f', algorithm={p["algorithm"]}'
    return auth


def probe(host, port, path, username="", password="", timeout=8):
    if not host:
        raise ValueError("RTSP host is missing")

    port = int(port)
    path = str(path or "/")
    if not path.startswith("/"):
        path = "/" + path
    uri = f"rtsp://{host}:{port}{path}"
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    cseq = 1

    def request(method, authorization="", extra=""):
        nonlocal cseq
        auth = f"Authorization: {authorization}\r\n" if authorization else ""
        msg = f"{method} {uri} RTSP/1.0\r\nCSeq: {cseq}\r\nUser-Agent: MobileCameraAI-Python\r\n{auth}{extra}\r\n"
        cseq += 1
        sock.sendall(msg.encode())
        return _read_response(sock)

    try:
        options, _ = request("OPTIONS")
        describe, body = request("DESCRIBE", extra="Accept: application/sdp\r\n")

        if describe.startswith("RTSP/1.0 401") and username:
            challenge = _header(describe, "WWW-Authenticate")
            if not challenge:
                raise RuntimeError("RTSP 401 without WWW-Authenticate challenge")
            cnonce = hashlib.md5(f"{host}:{port}:{path}".encode()).hexdigest()[:16]
            authorization = _make_digest(unquote(str(username)), unquote(str(password)), "DESCRIBE", uri, challenge, cnonce=cnonce)
            describe, body = request("DESCRIBE", authorization=authorization, extra="Accept: application/sdp\r\n")

        sdp = body.decode("utf-8", "replace")
        first_options = options.splitlines()[0] if options else ""
        first_describe = describe.splitlines()[0] if describe else ""
        upper_sdp = sdp.upper()
        lower_sdp = sdp.lower()

        return {
            "ok": first_describe.startswith("RTSP/1.0 200"),
            "options": first_options,
            "describe": first_describe,
            "uri": uri,
            "sdp": sdp,
            "h265": "H265" in upper_sdp or "HEVC" in upper_sdp,
            "sprop_vps": "sprop-vps" in lower_sdp,
            "sprop_sps": "sprop-sps" in lower_sdp,
            "sprop_pps": "sprop-pps" in lower_sdp,
        }
    finally:
        sock.close()
