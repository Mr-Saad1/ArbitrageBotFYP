#!/usr/bin/env python3
import os
import platform
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "Frontend"
BACKEND = ROOT / "main.py"


def run(cmd, cwd=None, env=None):
    print(f"\n>>> {cmd}\n")
    return subprocess.Popen(cmd, cwd=str(cwd) if cwd else None, env=env, shell=True)


def ensure_python_env():
    venv_dir = ROOT / ".venv"
    if not venv_dir.exists():
        print("Creating Python virtual environment...")
        subprocess.run([sys.executable, "-m", "venv", ".venv"], cwd=str(ROOT), check=True)

    if platform.system() == "Windows":
        py = venv_dir / "Scripts" / "python.exe"
    else:
        py = venv_dir / "bin" / "python"

    if not py.exists():
        raise FileNotFoundError(f"Virtual environment Python not found: {py}")

    subprocess.run([str(py), "-m", "pip", "install", "--upgrade", "pip"], cwd=str(ROOT), check=True)
    subprocess.run([str(py), "-m", "pip", "install", "-r", "requirements.txt"], cwd=str(ROOT), check=True)
    return py


def ensure_node_deps():
    if not (FRONTEND / "node_modules").exists():
        print("Installing frontend dependencies...")
        subprocess.run(["npm", "install"], cwd=str(FRONTEND), check=True)


def launch_backend(py_path):
    env = os.environ.copy()
    env["PORT"] = "8000"
    env["HOST"] = "0.0.0.0"
    return run(f'"{py_path}" main.py', cwd=str(ROOT), env=env)


def launch_frontend():
    env = os.environ.copy()
    return run("npm run dev -- --host 0.0.0.0 --port 5173", cwd=str(FRONTEND), env=env)


def main():
    print("=== ArbPro One-Click Launcher ===")
    print("This script will set up local dependencies and run the backend + frontend together.")
    print("It is intended for local development/demo use.")

    if not (ROOT / ".env").exists():
        if (ROOT / ".env.example").exists():
            print(".env not found. Copying .env.example to .env")
            shutil.copy2(ROOT / ".env.example", ROOT / ".env")
        else:
            print("No .env file found. You may need to add your API keys before live trading.")

    try:
        py_path = ensure_python_env()
        ensure_node_deps()
    except Exception as exc:
        print(f"Setup failed: {exc}")
        input("Press Enter to exit...")
        return

    backend_proc = launch_backend(py_path)
    time.sleep(3)
    frontend_proc = launch_frontend()

    print("\nBackend running at: http://localhost:8000")
    print("Frontend running at: http://localhost:5173")
    print("\nOpen the frontend in your browser. The project is starting in the background.")
    try:
        webbrowser.open("http://localhost:5173")
    except Exception:
        pass

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping services...")
        backend_proc.terminate()
        frontend_proc.terminate()
        print("Stopped.")


if __name__ == "__main__":
    main()
