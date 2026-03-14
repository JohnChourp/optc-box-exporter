#!/usr/bin/env node

import path from "node:path";
import vm from "node:vm";

const sourceRepoBase = "https://raw.githubusercontent.com/optc-db/optc-db.github.io/master";
const githubApiBase = "https://api.github.com/repos/optc-db/optc-db.github.io";
const githubHeaders = {
  "User-Agent": "optc-box-exporter",
  Accept: "application/vnd.github+json",
};

const packDefinitions = [
  {
    key: "thumbnailsGlo",
    entryName: "glo",
  },
  {
    key: "thumbnailsJapan",
    entryName: "jap",
  },
];

const packKeyToField = {
  thumbnailsGlo: "thumbnailGlobal",
  thumbnailsJapan: "thumbnailJapan",
};

const packEntryNameMap = {
  glo: "thumbnailsGlo",
  jap: "thumbnailsJapan",
};

const typeSuffixOrder = new Map(["STR", "DEX", "QCK", "PSY", "INT"].map((value, index) => [value, index]));
const noop = () => undefined;

function createSandbox() {
  const target = {
    window: {},
    console: {
      log: noop,
      warn: noop,
      error: noop,
    },
  };

  target.global = target;
  target.globalThis = target;
  target.self = target.window;

  return new Proxy(target, {
    get(currentTarget, property) {
      if (property in currentTarget) {
        return currentTarget[property];
      }

      if (property in globalThis) {
        return globalThis[property];
      }

      return noop;
    },
    has() {
      return true;
    },
    set(currentTarget, property, value) {
      currentTarget[property] = value;
      return true;
    },
  });
}

async function fetchText(relativePath) {
  const response = await fetch(`${sourceRepoBase}/${relativePath}`, { headers: githubHeaders });
  if (!response.ok) {
    throw new Error(`Failed to fetch ${relativePath}: ${response.status}`);
  }
  return response.text();
}

async function fetchJson(url) {
  const response = await fetch(url, { headers: githubHeaders });
  if (!response.ok) {
    throw new Error(`Failed to fetch ${url}: ${response.status}`);
  }
  return response.json();
}

function viableUnit(unit) {
  if (!Array.isArray(unit)) {
    return false;
  }

  if (unit.every((value) => value !== null && value !== undefined)) {
    return true;
  }

  if (unit.length >= 15) {
    for (let index = 9; index < 15; index += 1) {
      if (unit[index] === null || unit[index] === undefined) {
        return false;
      }
    }
    return true;
  }

  return false;
}

function parseVersion(source) {
  const match = source.match(/dbVersion\s*=\s*["']([^"']+)["']/);
  return match?.[1] ?? "unknown";
}

function normalizePackPaths(tree, packEntryName) {
  return tree.tree
    .filter((entry) => entry.type === "blob" && entry.path.endsWith(".png"))
    .map((entry) => ({
      localPath: entry.path,
      bytes: entry.size,
      packKey: packEntryNameMap[packEntryName],
    }));
}

function parseAssetReference(localPath) {
  const basename = path.basename(localPath);
  const match = basename.match(/^(\d{4})(?:-([A-Za-z0-9]+))?\.png$/);

  if (!match) {
    return null;
  }

  return {
    characterId: Number(match[1]),
    suffix: match[2] ?? null,
  };
}

function getAssetSuffixRank(suffix) {
  if (!suffix) {
    return 0;
  }

  if (/^\d+$/.test(suffix)) {
    return 10 + Number(suffix);
  }

  if (typeSuffixOrder.has(suffix)) {
    return 100 + (typeSuffixOrder.get(suffix) ?? 0);
  }

  return 1000;
}

function compareAssetPaths(leftPath, rightPath) {
  const leftReference = parseAssetReference(leftPath);
  const rightReference = parseAssetReference(rightPath);

  if (!leftReference || !rightReference) {
    return leftPath.localeCompare(rightPath);
  }

  const leftRank = getAssetSuffixRank(leftReference.suffix);
  const rightRank = getAssetSuffixRank(rightReference.suffix);

  if (leftRank !== rightRank) {
    return leftRank - rightRank;
  }

  if (
    leftReference.suffix &&
    rightReference.suffix &&
    /^\d+$/.test(leftReference.suffix) &&
    /^\d+$/.test(rightReference.suffix)
  ) {
    return Number(leftReference.suffix) - Number(rightReference.suffix);
  }

  return leftPath.localeCompare(rightPath);
}

function createEmptyAssets() {
  return {
    thumbnailGlobal: null,
    thumbnailJapan: null,
  };
}

function buildCharacterAssetsMap(packs) {
  const assetMap = new Map();

  for (const pack of packs) {
    const entriesByCharacterId = new Map();

    for (const file of pack.files) {
      const assetReference = parseAssetReference(file.localPath);

      if (!assetReference) {
        continue;
      }

      const currentEntries = entriesByCharacterId.get(assetReference.characterId) ?? [];
      currentEntries.push(file.localPath);
      entriesByCharacterId.set(assetReference.characterId, currentEntries);
    }

    for (const [characterId, filePaths] of entriesByCharacterId.entries()) {
      const preferredPath = [...filePaths].sort(compareAssetPaths)[0];
      const current = assetMap.get(characterId) ?? createEmptyAssets();
      const targetField = packKeyToField[pack.key];

      if (targetField) {
        current[targetField] = preferredPath;
      }

      assetMap.set(characterId, current);
    }
  }

  return assetMap;
}

function buildPackFileIndexes(packs) {
  return new Map(
    packs.map((pack) => [
      pack.key,
      new Map(pack.files.map((file) => [file.localPath, file])),
    ]),
  );
}

function parseThumbnailAssetUrl(url) {
  const match = String(url).match(/\/api\/images\/thumbnail\/(glo|jap)\/(.+\.png)$/);

  if (!match) {
    return null;
  }

  return {
    packKey: packEntryNameMap[match[1]] ?? null,
    relativePath: match[2],
  };
}

function buildDefaultThumbnailRelativePath(characterId) {
  const normalizedId = Number(characterId);
  return `${Math.trunc(normalizedId / 1000)}/${Math.trunc((normalizedId % 1000) / 100)}00/${String(normalizedId).padStart(4, "0")}.png`;
}

function buildDeterministicThumbnailOverrides(characterCount, utilsWindow, packFileIndexes) {
  const getter = utilsWindow?.Utils?.getThumbnailUrl;

  if (typeof getter !== "function") {
    throw new Error("Unable to evaluate upstream thumbnail mapping utility.");
  }

  const overrides = new Map();

  for (let characterId = 1; characterId <= characterCount; characterId += 1) {
    const assetReference = parseThumbnailAssetUrl(getter(characterId, ""));

    if (!assetReference?.packKey) {
      continue;
    }

    const packIndex = packFileIndexes.get(assetReference.packKey);

    if (!packIndex?.has(assetReference.relativePath)) {
      continue;
    }

    const isDefaultJapanPath =
      assetReference.packKey === "thumbnailsJapan" &&
      assetReference.relativePath === buildDefaultThumbnailRelativePath(characterId);

    if (isDefaultJapanPath) {
      continue;
    }

    overrides.set(characterId, assetReference);
  }

  return overrides;
}

function mergeThumbnailOverrides(assetsById, thumbnailOverrides) {
  for (const [characterId, assetReference] of thumbnailOverrides.entries()) {
    const current = assetsById.get(characterId) ?? createEmptyAssets();
    const targetField = packKeyToField[assetReference.packKey];

    if (!targetField) {
      continue;
    }

    current[targetField] = assetReference.relativePath;
    assetsById.set(characterId, current);
  }

  return assetsById;
}

async function buildPackTrees() {
  const listing = await fetchJson(`${githubApiBase}/contents/api/images/thumbnail?ref=master`);
  const packTrees = [];

  for (const pack of packDefinitions) {
    const directory = listing.find((entry) => entry.name === pack.entryName);
    if (!directory) {
      throw new Error(`Missing GitHub tree for ${pack.entryName}`);
    }

    const tree = await fetchJson(`${githubApiBase}/git/trees/${directory.sha}?recursive=1`);
    packTrees.push({
      key: pack.key,
      files: normalizePackPaths(tree, pack.entryName),
    });
  }

  return packTrees;
}

function buildSourceForAssets(assets) {
  if (assets.thumbnailGlobal) {
    return {
      region: "glo",
      relativePath: assets.thumbnailGlobal,
      sourceUrl: `/api/images/thumbnail/glo/${assets.thumbnailGlobal}`,
    };
  }

  if (assets.thumbnailJapan) {
    return {
      region: "jap",
      relativePath: assets.thumbnailJapan,
      sourceUrl: `/api/images/thumbnail/jap/${assets.thumbnailJapan}`,
    };
  }

  return {
    region: null,
    relativePath: null,
    sourceUrl: null,
  };
}

async function main() {
  const [unitsSource, utilsSource, versionSource, packTrees] = await Promise.all([
    fetchText("common/data/units.js"),
    fetchText("common/js/utils.js"),
    fetchText("common/data/version.js"),
    buildPackTrees(),
  ]);

  const sandbox = createSandbox();
  vm.runInNewContext(unitsSource, sandbox, { timeout: 20_000 });
  vm.runInNewContext(utilsSource, sandbox, { timeout: 20_000 });

  const units = sandbox.window?.units ?? [];
  const assetsById = buildCharacterAssetsMap(packTrees);
  const packFileIndexes = buildPackFileIndexes(packTrees);
  const thumbnailOverrides = buildDeterministicThumbnailOverrides(units.length, sandbox.window, packFileIndexes);
  mergeThumbnailOverrides(assetsById, thumbnailOverrides);

  const items = units.flatMap((unit, index) => {
    if (!viableUnit(unit)) {
      return [];
    }

    const id = index + 1;
    const assets = assetsById.get(id) ?? createEmptyAssets();
    const source = buildSourceForAssets(assets);

    return [{
      id,
      name: Array.isArray(unit) ? unit[0] : null,
      sourceUrl: source.sourceUrl,
      region: source.region,
      relativePath: source.relativePath,
    }];
  });

  process.stdout.write(
    JSON.stringify({
      generatedAt: new Date().toISOString(),
      sourceVersion: parseVersion(versionSource),
      unitsTotal: units.length,
      viableCount: items.length,
      items,
    }),
  );
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});
