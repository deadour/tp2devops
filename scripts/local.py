"""Launch the four real HTTP services locally; Ctrl+C stops only these children."""
import argparse
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def launch(base_port=8000, data_dir=None, log_dir=None):
    processes = []
    logs = []
    roles = {"reservations": base_port + 1, "payments": base_port + 2, "confirmations": base_port + 3, "coordinator": base_port}
    try:
        # Never accidentally run tests against an existing demo/server.
        for port in roles.values():
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", port))
        for role, port in roles.items():
            env = {**os.environ, "SERVICE_ROLE": role, "DATA_DIR": str(data_dir or ROOT / ".local" / "data"), "SELF_URL": f"http://127.0.0.1:{base_port}"}
            for name, number in roles.items():
                env[f"{name.upper()}_URL"] = f"http://127.0.0.1:{number}"
            output = None
            if log_dir:
                Path(log_dir).mkdir(parents=True, exist_ok=True)
                output = open(Path(log_dir) / f"{role}.log", "w", encoding="utf-8")
                logs.append(output)
            process = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", str(port), "--no-access-log"], cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT if output else None)
            processes.append(process)
        deadline = time.monotonic() + 30
        for role, port in roles.items():
            while True:
                if any(p.poll() is not None for p in processes):
                    raise RuntimeError("Un servicio no pudo iniciarse. Revisar puertos y logs.")
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    if time.monotonic() > deadline:
                        raise RuntimeError(f"Timeout iniciando {role}")
                    time.sleep(.15)
        return processes, logs
    except BaseException:
        stop(processes, logs)
        raise


def stop(processes, logs):
    for process in processes:
        if process.poll() is None:
            if os.name == "nt":
                # A Windows venv executable is a launcher with a child interpreter.
                # Terminating only the launcher leaves the server and its files open.
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            else:
                process.terminate()
    for process in processes:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    for handle in logs:
        handle.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    processes, logs = launch(args.port)
    print(f"API lista: http://127.0.0.1:{args.port}/docs", flush=True)
    print("Frontend: ejecutar npm run dev desde web/. Ctrl+C para detener servicios.", flush=True)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        while all(p.poll() is None for p in processes):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop(processes, logs)
