#!/usr/bin/env node

import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import vm from "node:vm";

import { buildRawSourceUrl, parseSourceArgs, resolveSourceConfig } from "./optc_sources.mjs";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const rootDir = path.resolve(__dirname, "..");
const githubHeaders = {
  "User-Agent": "optc-box-exporter",
  Accept: "application/vnd.github+json",
};

export function parseArgs(args = process.argv.slice(2)) {
  const options = parseSourceArgs(args, {
    output: path.join(rootDir, "data", "units.json"),
  });

  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];

    if (arg.startsWith("--output=")) {
      options.output = path.resolve(rootDir, arg.split("=")[1]);
      continue;
    }

    if (arg === "--output") {
      const nextValue = args[index + 1];
      if (!nextValue) {
        throw new Error("--output requires a value.");
      }
      options.output = path.resolve(rootDir, nextValue);
      index += 1;
      continue;
    }
  }

  return options;
}

async function fetchText(source, relativePath) {
  const response = await fetch(buildRawSourceUrl(source, relativePath), { headers: githubHeaders });
  if (!response.ok) {
    throw new Error(`Failed to fetch ${relativePath} from ${source.label}: ${response.status}`);
  }
  return response.text();
}

export function extractUnitsJson(unitsSource) {
  const sandbox = {
    window: {},
    console: {
      log: () => undefined,
      warn: () => undefined,
      error: () => undefined,
    },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.self = sandbox.window;

  vm.runInNewContext(unitsSource, sandbox, { timeout: 20_000 });
  const units = sandbox.window?.units;

  if (!Array.isArray(units)) {
    throw new Error("Unable to evaluate upstream units array.");
  }

  return `${JSON.stringify(units)}\n`;
}

export async function downloadUnits(args = process.argv.slice(2)) {
  const options = parseArgs(args);
  const source = resolveSourceConfig(options.source);
  const unitsSource = await fetchText(source, "common/data/units.js");
  const unitsJson = extractUnitsJson(unitsSource);

  await mkdir(path.dirname(options.output), { recursive: true });
  await writeFile(options.output, unitsJson);

  return {
    sourceKey: source.key,
    output: options.output,
    unitCount: JSON.parse(unitsJson).length,
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  downloadUnits().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exit(1);
  });
}
