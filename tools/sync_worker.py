"""Check or copy shared sources from kinobot to a Worker checkout."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "tools/authorize_instant.py",
    "tools/episode_labels.py",
    "tools/provider_identity.py",
    "tools/provider_matching.py",
    "tools/yougen_agent.py",
    "tools/yougen_bridge.py",
    "tools/yougen_discovery.py",
    "tools/yougen_options.py",
    "tools/yougen_queue.py",
    "tools/yougen_worker.py",
    "tests/test_worker_shared.py",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("worker", type=Path)
    parser.add_argument("--copy", action="store_true")
    args = parser.parse_args()
    if not (args.worker / "tools/hosted_worker.py").is_file():
        parser.error("Expected a KinoTime-Worker checkout")
    differences = []
    for name in FILES:
        source = ROOT / name
        target = args.worker / name
        if target.is_file() and source.read_bytes() == target.read_bytes():
            continue
        if args.copy:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        else:
            differences.append(name)
    if differences:
        parser.exit(1, "Shared files differ: " + ", ".join(differences) + "\n")


if __name__ == "__main__":
    main()
