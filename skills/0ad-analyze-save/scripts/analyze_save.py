"""Run the bundled offline analyzer, or its canonical source in this checkout."""

from pathlib import Path
import sys


def main():
    script_directory = Path(__file__).resolve().parent
    source_directory = script_directory.parents[2] / "src"
    roots = (script_directory, source_directory)
    root = next((candidate for candidate in roots
                 if (candidate / "pyrogenesis_analysis/cli.py").is_file()), None)
    if root is None:
        raise SystemExit("Missing offline analyzer. Copy the complete 0ad-analyze-save directory from player-kit or skill-kit.")
    sys.path.insert(0, str(root))
    from pyrogenesis_analysis.cli import main as analyze
    return analyze()


if __name__ == "__main__":
    raise SystemExit(main())
