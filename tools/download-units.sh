set -euo pipefail

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

curl -fsSL \
    https://raw.githubusercontent.com/optc-db/optc-db.github.io/master/common/data/units.js \
    -o "$TMP_DIR/units.js"

{
    printf 'const window = {};\n'
    cat "$TMP_DIR/units.js"
    printf '\nconst unitsJSON = JSON.stringify(window.units);\n'
    printf 'const fs = require("fs");\n'
    printf 'fs.writeFileSync("../data/units.json", unitsJSON + "\\n");\n'
} > "$TMP_DIR/modifiedUnits.js"

node "$TMP_DIR/modifiedUnits.js"
