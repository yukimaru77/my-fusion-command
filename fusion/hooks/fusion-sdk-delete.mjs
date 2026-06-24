#!/usr/bin/env node
import { createRequire } from "node:module";

const [sessionId, configDir = "", dir = ""] = process.argv.slice(2);

if (!sessionId) {
  console.error("usage: fusion-sdk-delete.mjs <session-id> [claude-config-dir] [project-dir]");
  process.exit(2);
}

if (configDir) {
  process.env.CLAUDE_CONFIG_DIR = configDir;
}

const require = createRequire(new URL("../fusion-sdk/package.json", import.meta.url));
const sdkPath = require.resolve("@anthropic-ai/claude-agent-sdk");
const { deleteSession } = await import(sdkPath);

const options = {};
if (dir) options.dir = dir;

await deleteSession(sessionId, options);
process.stdout.write(JSON.stringify({ sessionId, deleted: true }) + "\n");
