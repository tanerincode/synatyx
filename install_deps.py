"""
Read dependencies from pyproject.toml and install them into the active venv.
Bypasses uv.lock — sentence-transformers/torch/CUDA never pulled in.
"""
import subprocess
import sys
import tomllib

with open("pyproject.toml", "rb") as f:
    deps = tomllib.load(f)["project"]["dependencies"]

subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-cache-dir"] + deps)

