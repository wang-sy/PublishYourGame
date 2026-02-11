# GameAI Publisher API Contract

This reference maps to `src/service/gameaipublisher` server behavior.

## 1) POST /api/upload

Purpose: publish from a zip package.

Request:
- Content-Type: `multipart/form-data`
- Fields:
  - `file` (required): `.zip` file
  - `title` (required)
  - `description` (optional)

Server validation:
- `file` and `title` are required.
- `file.name` must end with `.zip`.
- zip must be parseable.
- zip must contain `index.html`.

Zip parsing behavior:
- Skips folders and metadata files (`__MACOSX`, `.DS_Store`).
- Normalizes path separators to `/`.
- If every entry is wrapped under one top-level folder, that folder is stripped.
- Supports zip methods:
  - store (0)
  - deflate (8)
- Unsupported compression entries are skipped.

Success response:
- HTTP 201
- Body:
```json
{
  "success": true,
  "data": {
    "id": "uuid",
    "title": "...",
    "description": "...",
    "gameUrl": "https://.../games/<id>/index.html",
    "ossPrefix": "games/<id>/",
    "fileCount": 12,
    "totalSize": 123456,
    "status": "published",
    "viewCount": 0,
    "likeCount": 0,
    "createdAt": "...",
    "updatedAt": "..."
  }
}
```

Common failures:
- 400 `file and title are required`
- 400 `Only zip files are supported`
- 400 `Invalid zip file`
- 400 `Zip file is empty`
- 400 `index.html not found in zip`
- 500 `Failed to upload game`

## 2) POST /api/publish

Purpose: publish from a JSON payload containing file list.

Request:
- Content-Type: `application/json`
- Body:
```json
{
  "title": "My Game",
  "description": "optional",
  "files": [
    {
      "path": "index.html",
      "content": "<html>...</html>"
    },
    {
      "path": "assets/sprite.png",
      "contentBase64": "iVBORw0KGgo..."
    }
  ]
}
```

Server validation:
- `title` required.
- `files` required and non-empty.
- At least one file path is `index.html` or `./index.html`.

Upload behavior:
- `path` leading `./` is removed.
- If `contentBase64` exists, server decodes Base64.
- Else if `content` exists, server uses UTF-8 bytes.
- If both missing for one file, the file is skipped.

Success response:
- HTTP 201
- Body shape matches `/api/upload` success data.

Common failures:
- 400 `title and files are required`
- 400 `index.html is required as entry point`
- 500 `Failed to publish game`

## 3) Game URL behavior

`gameUrl` is generated from OSS prefix + `index.html`.

Priority:
1. `OSS_DISPLAY_HOST` if configured
2. else `{bucket}.{oss endpoint host}`

Typical final URL:
- `https://cdn.combos.fun/games/<id>/index.html`

## 4) Useful follow-up APIs

- `GET /api/games?page=1&pageSize=12` list published games.
- `GET /api/games/:id?fingerprint=<fp>` fetch detail and increment view count.
- `POST /api/games/:id/like` body `{ "fingerprint": "..." }` toggle like.
- `POST /api/games/:id/comments` body `{ "nickname": "...", "content": "..." }` add comment.
