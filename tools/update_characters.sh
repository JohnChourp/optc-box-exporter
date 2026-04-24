cd /Users/john/Downloads/projects/optc-box-exporter

python3 -m pip install -r requirements.txt

sh ./tools/download-units.sh --source=optc-db

python3 -m optcbx download-portraits \
  --units data/units.json \
  --output data/Portraits \
  --team-builder-root ../optc-team-builder \
  --source=optc-db
