import json
import hashlib
import subprocess
import urllib.request
from pathlib import Path
import argparse
import re

SOURCE_ROOT = "source/"

parser = argparse.ArgumentParser()
parser.add_argument("--gameid", required=True)
parser.add_argument("--config", required=False)
parser.add_argument("--objects", required=False)
args = parser.parse_args()
gameid = args.gameid

DELINK_VERSION = "v0.1.0"
DELINK_EXE = Path("build/tools/delink-windows-x86_64.exe")
DELINK_URL = f"https://github.com/HaydnTrigg/delink/releases/download/{DELINK_VERSION}/delink-windows-x86_64.exe"
DELINK_SHA1 = "26e58fe113ebdafc8e682e69523e07d9eb1c6191"

OBJDIFF_VERSION = "v3.7.2-Monkey"
OBJDIFF_CLI_EXE = Path("build/tools/objdiff-cli-windows-x86_64.exe")
OBJDIFF_CLI_URL = f"https://github.com/HaydnTrigg/objdiff/releases/download/{OBJDIFF_VERSION}/objdiff-cli-windows-x86_64.exe"
OBJDIFF_CLI_SHA1 = "52509e6b7b1b93e516bc6e1635fd2ee7fb6b82c5"

OBJDIFF_EXE = Path("build/tools/objdiff-windows-x86_64.exe")
OBJDIFF_URL = f"https://github.com/HaydnTrigg/objdiff/releases/download/{OBJDIFF_VERSION}/objdiff-windows-x86_64.exe"
OBJDIFF_SHA1 = "8de8e7b364580f3a0752c4c0ed09db546acb4703"

ORIG_BIN = Path(f"orig/{gameid}.exe")
ORIG_PDB = Path(f"orig/{gameid}.pdb")

DELINK_OUTPUT_DIR = Path(f"build/{gameid}/delink")
OUTPUT_DIR = Path(f"build/{gameid}/obj")

config_path = args.config or f"config/{gameid}/config.json"
objects_path = args.objects or f"config/{gameid}/objects.json"

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

        # Strip block comments
        text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)

        # Strip line comments
        text = re.sub(r'//[^\n]*', '', text)
        return json.loads(text)

# Generate build files
config = load_json(config_path)
objects = load_json(objects_path)

COMPILER_ROOT = config.get("compiler_root")

def sha1_file(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd, cwd=None):
    print(f"Running: {' '.join(map(str, cmd))}")
    subprocess.run(cmd, cwd=cwd, check=True)


def download_file(url: str, dest: Path, expected_sha1: str):
    if dest.exists() and sha1_file(dest).lower() == expected_sha1.lower():
        print(f"{dest} already present and verified.")
        return
    print(f"Downloading {url} -> {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, dest)
    actual = sha1_file(dest)
    if actual.lower() != expected_sha1.lower():
        raise RuntimeError(f"SHA1 mismatch for {dest}: expected {expected_sha1}, got {actual}")
    print(f"{dest} downloaded and verified.")


def verify_hash(path: Path, expected: list[str]):
    if not path.exists():
        raise RuntimeError(f"Missing required file: {path}")
    actual = sha1_file(path)
    if actual.lower() not in expected:
        print(f"{path} SHA1 invalid")
    else:
        print(f"{path} SHA1 verified")

def substitute_flag(flag):
    substitutions = {
        "$(SOURCE_ROOT)": SOURCE_ROOT,
        "$(COMPILER_ROOT)": COMPILER_ROOT,
    }
    for token, value in substitutions.items():
        flag = flag.replace(token, value)
    return flag

def flatten_cflags(name, cflags_dict):
    result = []

    def recurse(n):
        entry = cflags_dict[n]
        if "base" in entry:
            recurse(entry["base"])
        result.extend(substitute_flag(f) for f in entry.get("flags", []))

    recurse(name)
    return result


def to_forward_path(p):
    return p.replace("\\", "/")


def get_delink_path(src):
    return f"build/{gameid}/delink/" + to_forward_path(src).rsplit(".", 1)[0] + ".obj"


def get_target_path(src):
    return f"build/{gameid}/obj/" + to_forward_path(src).rsplit(".", 1)[0] + ".obj"


def write_objdiff(config, objects):
    units = []
    for _, lib in objects.items():
        category = lib.get("progress_category", "default")
        for src, target in lib["objects"].items():
            units.append({
                "name": f"{gameid.lower()}/" + to_forward_path(src).rsplit(".", 1)[0],
                "target_path": get_delink_path(target or src),
                "base_path": get_target_path(src),
                "metadata": {
                    "complete": False,
                    "reverse_fn_order": False,
                    "source_path": SOURCE_ROOT + "/" + to_forward_path(src),
                    "progress_categories": [category],
                    "auto_generated": False,
                },
            })

    return {
        "min_version": "2.0.0-beta.5",
        "custom_make": "ninja",
        "build_target": False,
        "watch_patterns": [
            "*.c", "*.cp", "*.cpp", "*.h", "*.hpp",
            "*.inc", "*.py", "*.yml", "*.txt", "*.json"
        ],
        "units": units,
        "progress_categories": [
            {"id": k, "name": v}
            for k, v in config.get("progress_categories", {}).items()
        ],
    }


def write_ninja(config, objects):
    lines = []
    cxx = config.get("compiler", "clang++")
    lines.append(f"cxx = {cxx}\n\n")

    asflags = " ".join(substitute_flag(f) for f in config.get("asflags", []))
    ldflags = " ".join(substitute_flag(f) for f in config.get("ldflags", []))
    if asflags:
        lines.append(f"asflags = {asflags}\n\n")
    if ldflags:
        lines.append(f"ldflags = {ldflags}\n\n")

    lines.append("rule compile\n")
    lines.append("  command = $cxx /c $cflags -c $in \"/Fo$out\"\n")
    lines.append("  description = Compiling $in\n\n")

    all_objs = []
    for _, lib in objects.items():
        flags_str = " ".join(flatten_cflags(lib["cflags"], config["cflags"]))
        for src in lib["objects"]:
            obj = get_target_path(src)
            all_objs.append(obj)
            lines.append(f"build {obj}: compile {to_forward_path(SOURCE_ROOT + '/' + src)}\n")
            lines.append(f"  cflags = {flags_str}\n\n")

    lines.append("build all: phony $\n")
    for obj in all_objs:
        lines.append(f"  {obj} $\n")
    lines.append("\ndefault all\n")

    return "".join(lines)

# Ensure delink binary
download_file(DELINK_URL, DELINK_EXE, DELINK_SHA1)

download_file(OBJDIFF_CLI_URL, OBJDIFF_CLI_EXE, OBJDIFF_CLI_SHA1)
download_file(OBJDIFF_URL, OBJDIFF_EXE, OBJDIFF_SHA1)

DELINK_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
print(" ".join([
    str(DELINK_EXE), "pe-split", str(ORIG_BIN),
    "--pdb", str(ORIG_PDB),
    "-o", str(DELINK_OUTPUT_DIR),
]))
run([
    str(DELINK_EXE), "pe-split", str(ORIG_BIN),
    "--pdb", str(ORIG_PDB),
    "-o", str(DELINK_OUTPUT_DIR),
])

with open("build.ninja", "w", encoding="utf-8") as f:
    f.write(write_ninja(config, objects))

with open("objdiff.json", "w", encoding="utf-8") as f:
    json.dump(write_objdiff(config, objects), f, indent=2)

print("Generated build.ninja and objdiff.json")
