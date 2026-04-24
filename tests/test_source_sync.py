import json
import subprocess
import textwrap
import unittest


class SourceSyncScriptTests(unittest.TestCase):
    def test_source_config_defaults_to_optc_db_and_extracts_units_json(self) -> None:
        script = textwrap.dedent("""
            import { resolveSourceConfig } from './tools/optc_sources.mjs';
            import { extractUnitsJson, parseArgs } from './tools/download-units.mjs';

            const source = resolveSourceConfig(parseArgs([]).source);
            const units = JSON.parse(extractUnitsJson(`
              window.units = [
                ["A", "STR", ["Fighter"], 5],
                null,
                ["C", "DEX", ["Slasher"], 4]
              ];
            `));

            console.log(JSON.stringify({
              sourceKey: source.key,
              rawUnitCount: units.length,
              maxUnitId: units.length
            }));
        """)

        process = subprocess.run(
            ['node', '--input-type=module', '-e', script],
            check=True,
            capture_output=True,
            text=True,
        )

        payload = json.loads(process.stdout)
        self.assertEqual(payload, {
            'sourceKey': 'optc-db',
            'rawUnitCount': 3,
            'maxUnitId': 3,
        })

    def test_source_config_accepts_2shankz(self) -> None:
        script = textwrap.dedent("""
            import { resolveSourceConfig } from './tools/optc_sources.mjs';
            import { parseArgs } from './tools/download-units.mjs';
            console.log(JSON.stringify(resolveSourceConfig(parseArgs(['--source=2shankz']).source)));
        """)

        process = subprocess.run(
            ['node', '--input-type=module', '-e', script],
            check=True,
            capture_output=True,
            text=True,
        )

        payload = json.loads(process.stdout)
        self.assertEqual(payload['key'], '2shankz')
        self.assertIn('2Shankz', payload['label'])


if __name__ == '__main__':
    unittest.main()
