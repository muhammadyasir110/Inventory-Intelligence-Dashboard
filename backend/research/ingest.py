"""
research/ingest.py — command-line ingestion of the research dataset.

  cd backend
  python -m research.ingest path/to/dataset.xlsx
  python -m research.ingest data.csv --mapping research/config/research_30sku_mapping.json --sheet Sheet1

Writes research/data/processed/<provenance>_<sha12>/ containing source copy,
processed.csv, validation_report.{json,md} and metadata.json.
Exit code 0 = valid, 1 = validation errors, 2 = usage/IO error.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from research.dataset import validate, write_artifact   # noqa: E402

DEFAULT_MAPPING = os.path.join(HERE, "config", "research_30sku_mapping.json")
DEFAULT_OUT = os.path.join(HERE, "data", "processed")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--mapping", default=DEFAULT_MAPPING)
    ap.add_argument("--sheet")
    ap.add_argument("--out", default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    try:
        with open(a.file, "rb") as fh:
            raw = fh.read()
        res = validate(raw, os.path.basename(a.file), a.mapping, a.sheet)
    except (OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    folder = write_artifact(res, raw, a.out)
    rep = res.report()
    print(f"{rep['rows']} rows | valid={rep['is_valid']} | errors={rep['n_errors']} "
          f"warnings={rep['n_warnings']} | units_confirmed={rep['units_confirmed']}")
    for i in rep["issues"]:
        print(f"  [{i['level']}] {i['rule']}: {i['message']}" + (f" (column {i['column']})" if i['column'] else ""))
    print(f"Artifacts: {folder}")
    return 0 if res.is_valid else 1


if __name__ == "__main__":
    sys.exit(main())
