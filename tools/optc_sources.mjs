export const dataImportSources = Object.freeze({
  "optc-db": Object.freeze({
    key: "optc-db",
    label: "optc-db/optc-db.github.io",
    rawBaseUrl: "https://raw.githubusercontent.com/optc-db/optc-db.github.io/master",
    githubApiBase: "https://api.github.com/repos/optc-db/optc-db.github.io",
    ref: "master",
  }),
  "2shankz": Object.freeze({
    key: "2shankz",
    label: "2Shankz/optc-db.github.io",
    rawBaseUrl: "https://raw.githubusercontent.com/2Shankz/optc-db.github.io/master",
    githubApiBase: "https://api.github.com/repos/2Shankz/optc-db.github.io",
    ref: "master",
  }),
});

export function resolveSourceConfig(sourceKey = "optc-db") {
  const source = dataImportSources[sourceKey];

  if (!source) {
    const supportedSources = Object.keys(dataImportSources).join(", ");
    throw new Error(`Invalid --source value "${sourceKey}". Expected one of: ${supportedSources}.`);
  }

  return source;
}

export function parseSourceArgs(args = [], defaults = {}) {
  const options = {
    source: "optc-db",
    ...defaults,
  };

  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];

    if (arg.startsWith("--source=")) {
      options.source = arg.split("=")[1];
      continue;
    }

    if (arg === "--source") {
      const nextValue = args[index + 1];
      if (!nextValue) {
        throw new Error("--source requires a value.");
      }
      options.source = nextValue;
      index += 1;
    }
  }

  resolveSourceConfig(options.source);
  return options;
}

export function buildRawSourceUrl(source, relativePath) {
  return `${source.rawBaseUrl}/${relativePath}`;
}
