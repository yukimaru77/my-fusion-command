#!/usr/bin/env node
import { createRequire } from "node:module";

const require = createRequire(new URL("../fusion-sdk/package.json", import.meta.url));
const sdkPath = require.resolve("@anthropic-ai/claude-agent-sdk");
const { forkSession } = await import(sdkPath);

const [sessionId, dir, upToMessageId = "", title = ""] = process.argv.slice(2);

if (!sessionId || !dir) {
  console.error("usage: fusion-sdk-fork.mjs <session-id> <dir> [up-to-message-uuid] [title]");
  process.exit(2);
}

const options = { dir };
if (upToMessageId) options.upToMessageId = upToMessageId;
if (title) options.title = title;

const result = await forkSession(sessionId, options);
process.stdout.write(JSON.stringify(result) + "\n");
