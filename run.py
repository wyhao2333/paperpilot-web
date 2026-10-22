"""One-command Windows / Ubuntu setup and launch. Use patched Python and Node."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / ".runtime"


def digest(paths):
    result = hashlib.sha256()
    for path in sorted(paths):
        result.update(str(path.relative_to(ROOT)).encode())
        result.update(path.read_bytes())
    return result.hexdigest()


def run(command, cwd=ROOT):
    subprocess.run([str(x) for x in command], cwd=cwd, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--port", type=int)
    parser.add_argument("--host")
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        raise SystemExit("Python 3.11 or newer is required.")
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        raise SystemExit("Install Node.js 24 LTS (includes npm), then run this command again.")
    node = shutil.which("node")
    version = subprocess.check_output([node, "--version"], text=True).strip().lstrip("v").split(".")
    installed = tuple(map(int, version[:3]))
    patched = {22: (22, 23, 4), 24: (24, 22, 1), 26: (26, 12, 1)}
    if installed[0] not in patched or installed < patched[installed[0]]:
        raise SystemExit("Install security-patched Node.js: 24.22.1+ recommended (or 22.23.4+/26.12.1+).")
    python = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        print("Creating an isolated Python environment...", flush=True)
        venv.create(ROOT / ".venv", with_pip=True)
    RUNTIME.mkdir(exist_ok=True)
    stamp_path = RUNTIME / "build-state.json"
    try:
        stamp = json.loads(stamp_path.read_text())
    except (OSError, ValueError):
        stamp = {}
    py_hash = digest([ROOT / "requirements.txt"])
    if stamp.get("python") != py_hash:
        run([python, "-m", "pip", "install", "-r", "requirements.txt"])
        stamp["python"] = py_hash
    frontend = ROOT / "frontend"
    node_hash = digest([frontend / "package.json", frontend / "package-lock.json"])
    if stamp.get("node") != node_hash or not (frontend / "node_modules").exists():
        run([npm, "ci"], frontend)
        stamp["node"] = node_hash
    sources = [p for p in (frontend / "src").rglob("*") if p.is_file()]
    sources += [frontend / name for name in ["package.json", "package-lock.json", "index.html", "tsconfig.json", "vite.config.ts"]]
    sources += [frontend / "public/favicon.svg", frontend / "scripts/copy-pdf-assets.mjs"]
    ui_hash = digest(sources)
    if stamp.get("frontend") != ui_hash or not (frontend / "dist/index.html").exists():
        run([npm, "run", "build"], frontend)
        stamp["frontend"] = ui_hash
    stamp_path.write_text(json.dumps(stamp, indent=2), encoding="utf-8")
    if args.prepare_only:
        print("Ready. Run python run.py to start PaperPilot Web.")
        return
    command = [python, "-m", "paperpilot"]
    if args.port:
        command.extend(["--port", str(args.port)])
    if args.host:
        command.extend(["--host", args.host])
    try:
        run(command)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode)
