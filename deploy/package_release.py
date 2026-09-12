"""Build reproducible Lambda/static artifacts using only Python's stdlib.

No AWS calls, credentials, pip installation, source mutation, or directory walks
outside the allowlist. The output directory must not already exist.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parent.parent
CITIES = ("新北市", "臺北市", "桃園市", "臺中市", "臺南市", "高雄市")
REQUIRED_PY = ("deploy/runtime.py", "deploy/lambda_handler.py", "llm/backend.py",
               "llm/rate_limit.py", "engine/reference.py")
PUBLIC_FIXTURES = ("tests/fixtures/table32_sheet.png",)
API_SCRIPT = '<script data-youthscope-release>window.YOUTHLENS_API="/api";</script>'


def digest(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def read_safe(path: Path) -> bytes:
    if path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError(f"Refusing symlink/out-of-tree source: {path}")
    return path.read_bytes()


def snapshot() -> tuple[dict[str, bytes], dict[str, bytes]]:
    runtime: dict[str, bytes] = {}
    static: dict[str, bytes] = {}
    # Flat package modules only. No cache, dependencies, hidden files, or docs.
    for folder in ("engine", "llm", "data"):
        for path in sorted((ROOT / folder).glob("*.py")):
            runtime[path.relative_to(ROOT).as_posix()] = read_safe(path)
    for name in REQUIRED_PY:
        runtime[name] = read_safe(ROOT / name)
    for name in ("deploy/__init__.py",):
        if (ROOT / name).exists():
            runtime[name] = read_safe(ROOT / name)
    for pattern in ("unified*.json", "reference*.json"):
        for path in sorted((ROOT / "data").glob(pattern)):
            runtime[path.relative_to(ROOT).as_posix()] = read_safe(path)
    # These are public government-data outputs read by the API from S3.
    # Nothing else under data/ is copied into the public static tree.
    for city in CITIES:
        suffix = "" if city == "新北市" else "_" + city
        data_name = f"data/unified{suffix}.json"
        if data_name not in runtime:
            raise ValueError(f"Missing required six-city dataset: {data_name}")
        static[f"unified{suffix}.json"] = runtime[data_name]
        html_name = f"preview{suffix}.html"
        html = read_safe(ROOT / html_name).decode("utf-8")
        if "window.YOUTHLENS_API" not in html:
            raise ValueError(f"{html_name}: missing supported frontend API override")
        if "data-youthscope-release" in html:
            raise ValueError(f"{html_name}: source must not be a previous release copy")
        # HTML permits an implicit head. Preserve the charset declaration near
        # the start, and inject before the first executable application script.
        marker = re.search(r"<meta\b[^>]*\bcharset\s*=[^>]*>", html, re.I)
        if marker is None:
            marker = re.search(r"<head(?:\s[^>]*)?>", html, re.I)
        if marker is None:
            raise ValueError(f"{html_name}: missing charset/head insertion point")
        html = html[:marker.end()] + "\n" + API_SCRIPT + html[marker.end():]
        static[html_name] = html.encode("utf-8")
    for name in PUBLIC_FIXTURES:
        static[name] = read_safe(ROOT / name)
    if "data/reference_ntpc.json" not in runtime:
        raise ValueError("Missing official reference_ntpc.json for scan alignment")
    for name, blob in runtime.items():
        if name.endswith(".py"):
            ast.parse(blob, filename=name)
        else:
            json.loads(blob)
    return runtime, static


def write_zip(path: Path, entries: dict[str, bytes]) -> None:
    with ZipFile(path, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for name, blob in sorted(entries.items()):
            info = ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, blob, compresslevel=9)
    with ZipFile(path) as archive:
        if archive.testzip() is not None or set(archive.namelist()) != set(entries):
            raise ValueError(f"ZIP integrity failure: {path}")
        for name, expected in entries.items():
            if archive.read(name) != expected:
                raise ValueError(f"ZIP read-back mismatch: {name}")


def build(output: Path) -> dict:
    output = output.resolve()
    if output.exists():
        raise FileExistsError(f"Output already exists; choose a fresh path: {output}")
    runtime, static = snapshot()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".youthscope-release-", dir=output.parent) as tmp:
        stage = Path(tmp)
        write_zip(stage / "lambda.zip", runtime)
        write_zip(stage / "static.zip", static)
        for name, blob in static.items():
            target = stage / "static" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
        manifest = {"schema_version": 1, "handler": "deploy.runtime.handler",
                    "runtime": "python3.12", "api_endpoint": "/api",
                    "dependency_source": "AWS Python 3.12 runtime boto3; no bundled pip packages",
                    "files": {kind: [{"path": name, "bytes": len(blob), "sha256": digest(blob)}
                                     for name, blob in sorted(entries.items())]
                              for kind, entries in (("lambda", runtime), ("static", static))},
                    "artifacts": {name: {"bytes": (stage / name).stat().st_size,
                                          "sha256": digest((stage / name).read_bytes())}
                                  for name in ("lambda.zip", "static.zip")}}
        (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        # Refuse to replace a path created while staging.
        output.mkdir()
        for item in stage.iterdir():
            shutil.move(str(item), str(output / item.name))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New release directory (must not exist)")
    args = parser.parse_args()
    manifest = build(args.output)
    print(json.dumps({"output": str(args.output.resolve()), "artifacts": manifest["artifacts"]}, indent=2))


if __name__ == "__main__":
    main()
