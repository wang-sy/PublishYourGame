#!/usr/bin/env python3
"""
Cross-platform GameAI Publisher CLI.

Subcommands:
- upload-zip: POST /api/upload (multipart/form-data)
- publish-files: POST /api/publish (application/json)
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import pathlib
import sys
import urllib.error
import urllib.request
import uuid
from typing import Dict, List, Optional, Sequence, Tuple


def _normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def _build_endpoint(base_url: str, path: str) -> str:
    return f"{_normalize_base_url(base_url)}{path}"


def _parse_header_items(items: Sequence[str]) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    for item in items:
        if ":" not in item:
            raise ValueError(f"Invalid --header value: {item!r}, expected 'Key: Value'")
        key, value = item.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f"Invalid --header value: {item!r}, empty key")
        headers[key] = value
    return headers


def _read_json_response(raw: bytes) -> Dict[str, object]:
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {"raw": raw.decode("utf-8", errors="replace")}


def _http_post(
    url: str,
    data: bytes,
    content_type: str,
    timeout: int,
    headers: Dict[str, str],
) -> Tuple[int, Dict[str, object], Dict[str, str]]:
    req_headers = {
        "Content-Type": content_type,
        "Accept": "application/json",
        "x-request-id": str(uuid.uuid4()),
        **headers,
    }

    req = urllib.request.Request(url=url, data=data, method="POST", headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.getcode()
            body = resp.read()
            response_headers = {k.lower(): v for k, v in resp.headers.items()}
            return status, _read_json_response(body), response_headers
    except urllib.error.HTTPError as e:
        body = e.read() if hasattr(e, "read") else b""
        response_headers = {k.lower(): v for k, v in e.headers.items()} if e.headers else {}
        return e.code, _read_json_response(body), response_headers


def _build_multipart_form(
    fields: Sequence[Tuple[str, str]],
    file_field: str,
    file_name: str,
    file_bytes: bytes,
    file_content_type: str,
) -> Tuple[bytes, str]:
    boundary = f"----GamePublishBoundary{uuid.uuid4().hex}"
    crlf = b"\r\n"
    chunks: List[bytes] = []

    for name, value in fields:
        chunks.extend(
            [
                f"--{boundary}".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"'.encode("utf-8"),
                b"",
                value.encode("utf-8"),
            ]
        )

    chunks.extend(
        [
            f"--{boundary}".encode("utf-8"),
            (
                f'Content-Disposition: form-data; name="{file_field}"; '
                f'filename="{file_name}"'
            ).encode("utf-8"),
            f"Content-Type: {file_content_type}".encode("utf-8"),
            b"",
            file_bytes,
        ]
    )

    chunks.append(f"--{boundary}--".encode("utf-8"))
    chunks.append(b"")

    body = crlf.join(chunks)
    return body, f"multipart/form-data; boundary={boundary}"


def _find_files(root: pathlib.Path) -> List[pathlib.Path]:
    files = [p for p in root.rglob("*") if p.is_file()]
    files.sort()
    return files


def _to_posix_rel(path: pathlib.Path, root: pathlib.Path) -> str:
    return path.relative_to(root).as_posix()


def _load_files_for_publish(
    root: pathlib.Path,
    prefer_text: bool,
) -> List[Dict[str, str]]:
    files: List[Dict[str, str]] = []
    for abs_path in _find_files(root):
        rel_path = _to_posix_rel(abs_path, root)
        raw = abs_path.read_bytes()

        if prefer_text:
            try:
                text = raw.decode("utf-8")
                files.append({"path": rel_path, "content": text})
                continue
            except UnicodeDecodeError:
                pass

        files.append(
            {
                "path": rel_path,
                "contentBase64": base64.b64encode(raw).decode("ascii"),
            }
        )
    return files


def _print_result(status: int, payload: Dict[str, object], response_headers: Dict[str, str]) -> int:
    success = bool(payload.get("success"))
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    game_id = data.get("id") if isinstance(data, dict) else None
    game_url = data.get("gameUrl") if isinstance(data, dict) else None
    request_id = response_headers.get("x-request-id")

    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if success:
        if game_id:
            print(f"game_id: {game_id}", file=sys.stderr)
        if game_url:
            print(f"game_url: {game_url}", file=sys.stderr)
        if request_id:
            print(f"request_id: {request_id}", file=sys.stderr)
        return 0

    error_msg = payload.get("error")
    if error_msg:
        print(f"error: {error_msg}", file=sys.stderr)
    if request_id:
        print(f"request_id: {request_id}", file=sys.stderr)

    return 1 if status >= 400 else 0


def cmd_upload_zip(args: argparse.Namespace) -> int:
    zip_path = pathlib.Path(args.zip).expanduser().resolve()
    if not zip_path.exists() or not zip_path.is_file():
        print(f"zip file not found: {zip_path}", file=sys.stderr)
        return 2
    if zip_path.suffix.lower() != ".zip":
        print(f"zip file must end with .zip: {zip_path.name}", file=sys.stderr)
        return 2

    fields: List[Tuple[str, str]] = [("title", args.title)]
    if args.description:
        fields.append(("description", args.description))

    file_bytes = zip_path.read_bytes()
    content_type = mimetypes.guess_type(zip_path.name)[0] or "application/zip"
    body, multipart_ct = _build_multipart_form(
        fields=fields,
        file_field="file",
        file_name=zip_path.name,
        file_bytes=file_bytes,
        file_content_type=content_type,
    )

    url = _build_endpoint(args.base_url, "/api/upload")
    headers = _parse_header_items(args.header)
    status, payload, response_headers = _http_post(
        url=url,
        data=body,
        content_type=multipart_ct,
        timeout=args.timeout,
        headers=headers,
    )

    return _print_result(status, payload, response_headers)


def cmd_publish_files(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.dir).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        print(f"directory not found: {root}", file=sys.stderr)
        return 2

    index_path = root / "index.html"
    if not index_path.exists() or not index_path.is_file():
        print(f"index.html is required at directory root: {index_path}", file=sys.stderr)
        return 2

    files = _load_files_for_publish(root, prefer_text=args.prefer_text)
    payload: Dict[str, object] = {
        "title": args.title,
        "files": files,
    }
    if args.description:
        payload["description"] = args.description

    url = _build_endpoint(args.base_url, "/api/publish")
    headers = _parse_header_items(args.header)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    status, resp_payload, response_headers = _http_post(
        url=url,
        data=body,
        content_type="application/json",
        timeout=args.timeout,
        headers=headers,
    )

    return _print_result(status, resp_payload, response_headers)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Publish web games to GameAI Publisher")
    sub = parser.add_subparsers(dest="command", required=True)

    upload = sub.add_parser("upload-zip", help="Publish from zip package via /api/upload")
    upload.add_argument("--base-url", required=True, help="Publisher base url, e.g. http://nlb-8rcqh9faj37iiwegkc.ap-southeast-1.nlb.aliyuncsslbintl.com:3000")
    upload.add_argument("--zip", required=True, help="Path to game zip file")
    upload.add_argument("--title", required=True, help="Game title")
    upload.add_argument("--description", default="", help="Game description")
    upload.add_argument("--timeout", type=int, default=120, help="Request timeout seconds")
    upload.add_argument(
        "--header",
        action="append",
        default=[],
        help="Extra request header, repeatable, format: 'Key: Value'",
    )
    upload.set_defaults(func=cmd_upload_zip)

    publish = sub.add_parser("publish-files", help="Publish from directory via /api/publish")
    publish.add_argument("--base-url", required=True, help="Publisher base url, e.g. http://nlb-8rcqh9faj37iiwegkc.ap-southeast-1.nlb.aliyuncsslbintl.com:3000")
    publish.add_argument("--dir", required=True, help="Directory containing index.html")
    publish.add_argument("--title", required=True, help="Game title")
    publish.add_argument("--description", default="", help="Game description")
    publish.add_argument("--timeout", type=int, default=120, help="Request timeout seconds")
    publish.add_argument(
        "--prefer-text",
        action="store_true",
        help="Prefer UTF-8 text content for text files; binary files remain Base64",
    )
    publish.add_argument(
        "--header",
        action="append",
        default=[],
        help="Extra request header, repeatable, format: 'Key: Value'",
    )
    publish.set_defaults(func=cmd_publish_files)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        return args.func(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
