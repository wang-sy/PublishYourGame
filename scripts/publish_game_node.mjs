#!/usr/bin/env node

import fs from "node:fs/promises";
import path from "node:path";
import { randomUUID } from "node:crypto";

function printUsage() {
  const lines = [
    "Usage:",
    "  node publish_game_node.mjs upload-zip --base-url <url> --zip <file.zip> --title <title> [--description <desc>] [--timeout <sec>] [--header 'Key: Value']",
    "  node publish_game_node.mjs publish-files --base-url <url> --dir <project_dir> --title <title> [--description <desc>] [--timeout <sec>] [--prefer-text] [--header 'Key: Value']",
  ];
  console.log(lines.join("\n"));
}

function parseArgs(argv) {
  if (argv.length === 0 || argv.includes("--help") || argv.includes("-h")) {
    return { help: true };
  }

  const command = argv[0];
  const args = argv.slice(1);
  const options = { headers: [], preferText: false };

  for (let i = 0; i < args.length; i += 1) {
    const token = args[i];
    if (!token.startsWith("--")) {
      throw new Error(`Unexpected argument: ${token}`);
    }

    const key = token.slice(2);
    if (key === "prefer-text") {
      options.preferText = true;
      continue;
    }

    const value = args[i + 1];
    if (!value || value.startsWith("--")) {
      throw new Error(`Missing value for --${key}`);
    }

    if (key === "header") {
      options.headers.push(value);
    } else {
      options[key] = value;
    }
    i += 1;
  }

  return { command, options };
}

function requireOption(options, key) {
  const value = options[key];
  if (!value) {
    throw new Error(`Missing required option: --${key}`);
  }
  return value;
}

function parseHeaders(headerItems) {
  const headers = {};
  for (const item of headerItems) {
    const idx = item.indexOf(":");
    if (idx < 1) {
      throw new Error(`Invalid header format: ${item}`);
    }
    const key = item.slice(0, idx).trim();
    const value = item.slice(idx + 1).trim();
    if (!key) {
      throw new Error(`Invalid header format: ${item}`);
    }
    headers[key] = value;
  }
  return headers;
}

function normalizeBaseUrl(baseUrl) {
  return baseUrl.replace(/\/+$/, "");
}

function endpoint(baseUrl, routePath) {
  return `${normalizeBaseUrl(baseUrl)}${routePath}`;
}

async function parseJsonResponse(response) {
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return { raw: text };
  }
}

function printResult(status, payload, responseHeaders) {
  const success = Boolean(payload?.success);
  const data = payload && typeof payload.data === "object" ? payload.data : {};
  const gameId = data?.id;
  const gameUrl = data?.gameUrl;
  const requestId = responseHeaders.get("x-request-id");

  console.log(JSON.stringify(payload, null, 2));

  if (success) {
    if (gameId) console.error(`game_id: ${gameId}`);
    if (gameUrl) console.error(`game_url: ${gameUrl}`);
    if (requestId) console.error(`request_id: ${requestId}`);
    return 0;
  }

  if (payload?.error) {
    console.error(`error: ${payload.error}`);
  }
  if (requestId) {
    console.error(`request_id: ${requestId}`);
  }

  return status >= 400 ? 1 : 0;
}

async function collectFiles(rootDir) {
  const files = [];

  async function walk(dir) {
    const entries = await fs.readdir(dir, { withFileTypes: true });
    entries.sort((a, b) => a.name.localeCompare(b.name));

    for (const entry of entries) {
      const fullPath = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        await walk(fullPath);
      } else if (entry.isFile()) {
        files.push(fullPath);
      }
    }
  }

  await walk(rootDir);
  return files;
}

function toPosixRelative(rootDir, filePath) {
  return path.relative(rootDir, filePath).split(path.sep).join("/");
}

async function loadFilesForPublish(rootDir, preferText) {
  const decoder = new TextDecoder("utf-8", { fatal: true });
  const files = [];
  const absFiles = await collectFiles(rootDir);

  for (const absPath of absFiles) {
    const relPath = toPosixRelative(rootDir, absPath);
    const raw = await fs.readFile(absPath);

    if (preferText) {
      try {
        const content = decoder.decode(raw);
        files.push({ path: relPath, content });
        continue;
      } catch {
        // binary fallback below
      }
    }

    files.push({
      path: relPath,
      contentBase64: raw.toString("base64"),
    });
  }

  return files;
}

async function postJson(url, body, timeoutMs, extraHeaders) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "x-request-id": randomUUID(),
        ...extraHeaders,
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    const payload = await parseJsonResponse(response);
    return [response.status, payload, response.headers];
  } finally {
    clearTimeout(timeout);
  }
}

async function postForm(url, formData, timeoutMs, extraHeaders) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "x-request-id": randomUUID(),
        ...extraHeaders,
      },
      body: formData,
      signal: controller.signal,
    });
    const payload = await parseJsonResponse(response);
    return [response.status, payload, response.headers];
  } finally {
    clearTimeout(timeout);
  }
}

async function runUploadZip(options) {
  const baseUrl = requireOption(options, "base-url");
  const zipArg = requireOption(options, "zip");
  const title = requireOption(options, "title");
  const description = options.description || "";
  const timeoutSec = Number(options.timeout || 120);
  const timeoutMs = Math.max(1, timeoutSec) * 1000;

  const zipPath = path.resolve(zipArg);
  const stat = await fs.stat(zipPath).catch(() => null);
  if (!stat || !stat.isFile()) {
    throw new Error(`zip file not found: ${zipPath}`);
  }
  if (path.extname(zipPath).toLowerCase() !== ".zip") {
    throw new Error(`zip file must end with .zip: ${path.basename(zipPath)}`);
  }

  const zipBytes = await fs.readFile(zipPath);
  const form = new FormData();
  form.append("title", title);
  if (description) {
    form.append("description", description);
  }
  form.append("file", new Blob([zipBytes], { type: "application/zip" }), path.basename(zipPath));

  const url = endpoint(baseUrl, "/api/upload");
  const headers = parseHeaders(options.headers || []);
  const [status, payload, responseHeaders] = await postForm(url, form, timeoutMs, headers);
  return printResult(status, payload, responseHeaders);
}

async function runPublishFiles(options) {
  const baseUrl = requireOption(options, "base-url");
  const dirArg = requireOption(options, "dir");
  const title = requireOption(options, "title");
  const description = options.description || "";
  const timeoutSec = Number(options.timeout || 120);
  const timeoutMs = Math.max(1, timeoutSec) * 1000;

  const rootDir = path.resolve(dirArg);
  const stat = await fs.stat(rootDir).catch(() => null);
  if (!stat || !stat.isDirectory()) {
    throw new Error(`directory not found: ${rootDir}`);
  }

  const indexPath = path.join(rootDir, "index.html");
  const indexStat = await fs.stat(indexPath).catch(() => null);
  if (!indexStat || !indexStat.isFile()) {
    throw new Error(`index.html is required at directory root: ${indexPath}`);
  }

  const files = await loadFilesForPublish(rootDir, Boolean(options.preferText));
  const body = {
    title,
    files,
    ...(description ? { description } : {}),
  };

  const url = endpoint(baseUrl, "/api/publish");
  const headers = parseHeaders(options.headers || []);
  const [status, payload, responseHeaders] = await postJson(url, body, timeoutMs, headers);
  return printResult(status, payload, responseHeaders);
}

async function main() {
  try {
    const parsed = parseArgs(process.argv.slice(2));
    if (parsed.help) {
      printUsage();
      return 0;
    }

    const { command, options } = parsed;
    if (command === "upload-zip") {
      return await runUploadZip(options);
    }
    if (command === "publish-files") {
      return await runPublishFiles(options);
    }

    throw new Error(`Unsupported command: ${command}`);
  } catch (err) {
    if (err?.name === "AbortError") {
      console.error("request timeout");
      return 124;
    }
    console.error(err?.message || String(err));
    printUsage();
    return 2;
  }
}

const code = await main();
process.exit(code);
