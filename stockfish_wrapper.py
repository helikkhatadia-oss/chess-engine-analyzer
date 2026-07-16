#!/usr/bin/env python3
"""
stockfish_wrapper.py — thin wrapper around the Stockfish UCI binary for
verification analysis. Used as the "second opinion" engine alongside
own_engine.Engine.
"""

import subprocess
import shutil

DEFAULT_STOCKFISH_PATH = "/usr/games/stockfish"


class StockfishEngine:
    def __init__(self, path: str = None, threads: int = 2, hash_mb: int = 128):
        self.path = path or shutil.which("stockfish") or DEFAULT_STOCKFISH_PATH
        self.proc = subprocess.Popen(
            [self.path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._send("uci")
        self._read_until("uciok")
        self._send(f"setoption name Threads value {threads}")
        self._send(f"setoption name Hash value {hash_mb}")
        self._send("isready")
        self._read_until("readyok")

    def _send(self, cmd: str):
        self.proc.stdin.write(cmd + "\n")
        self.proc.stdin.flush()

    def _read_until(self, token: str):
        lines = []
        while True:
            line = self.proc.stdout.readline()
            if not line:
                break
            lines.append(line.strip())
            if token in line:
                break
        return lines

    def analyze(self, fen: str, depth: int = 16, movetime_ms: int = None):
        """Returns dict: {best_move (uci str or None), score_cp (int or None,
        from side-to-move perspective), mate (int or None, plies to mate)}."""
        self._send("ucinewgame")
        self._send("isready")
        self._read_until("readyok")
        self._send(f"position fen {fen}")
        if movetime_ms:
            self._send(f"go movetime {movetime_ms}")
        else:
            self._send(f"go depth {depth}")

        best_move = None
        score_cp = None
        mate = None
        while True:
            line = self.proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if line.startswith("info"):
                tokens = line.split()
                if "score" in tokens:
                    idx = tokens.index("score")
                    kind = tokens[idx + 1]
                    val = int(tokens[idx + 2])
                    if kind == "cp":
                        score_cp = val
                        mate = None
                    elif kind == "mate":
                        mate = val
                        score_cp = None
            if line.startswith("bestmove"):
                parts = line.split()
                best_move = parts[1] if len(parts) > 1 and parts[1] != "(none)" else None
                break

        return {"best_move": best_move, "score_cp": score_cp, "mate": mate}

    def close(self):
        try:
            self._send("quit")
            self.proc.terminate()
        except Exception:
            pass
