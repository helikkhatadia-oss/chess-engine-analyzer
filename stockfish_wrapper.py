#!/usr/bin/env python3
"""
stockfish_wrapper.py — thin wrapper around the Stockfish UCI binary for
verification analysis. Used as the "second opinion" engine alongside
own_engine.Engine.
"""

import subprocess
import shutil
import os
import glob
import platform

# Linux/macOS default when installed via package manager (apt, brew, etc.)
DEFAULT_STOCKFISH_PATH_UNIX = "/usr/games/stockfish"


def _find_stockfish(explicit_path: str = None) -> str:
    """Resolve the Stockfish executable path, checking (in order):
    1. An explicitly passed path
    2. The STOCKFISH_PATH environment variable
    3. The system PATH (works if you added Stockfish's folder to PATH,
       or installed it via apt/brew/choco)
    4. Common install locations (Linux package-manager path; typical
       Windows extraction folders under Downloads)
    Raises a clear, actionable error if none of these find it — this is
    especially relevant on Windows, where there's no package manager
    default and the .exe must be downloaded manually from
    https://stockfishchess.org/download/windows/
    """
    candidates = []
    if explicit_path:
        candidates.append(explicit_path)
    if os.environ.get("STOCKFISH_PATH"):
        candidates.append(os.environ["STOCKFISH_PATH"])
    which_result = shutil.which("stockfish") or shutil.which("stockfish.exe")
    if which_result:
        candidates.append(which_result)
    candidates.append(DEFAULT_STOCKFISH_PATH_UNIX)

    # Common Windows extraction spots — glob since the filename varies by
    # build (avx2 / bmi2 / popcnt / modern etc.)
    if platform.system() == "Windows":
        home = os.path.expanduser("~")
        search_globs = [
            os.path.join(home, "Downloads", "**", "stockfish*.exe"),
            os.path.join(home, "Downloads", "stockfish*.exe"),
            "C:\\stockfish\\stockfish*.exe",
        ]
        for pattern in search_globs:
            candidates.extend(glob.glob(pattern, recursive=True))

    for c in candidates:
        if c and os.path.isfile(c):
            return c

    raise FileNotFoundError(
        "Could not find a Stockfish executable.\n"
        "  - On Windows: download it from https://stockfishchess.org/download/windows/, "
        "extract the .zip, and either pass its path explicitly "
        "(--stockfish-path C:\\path\\to\\stockfish.exe on the CLI, or "
        "StockfishEngine(path=...) in code), or set an STOCKFISH_PATH "
        "environment variable to the .exe's full path.\n"
        "  - On Linux: sudo apt-get install stockfish\n"
        "  - On macOS: brew install stockfish\n"
        "  - Or run with --no-stockfish to skip Stockfish verification entirely "
        "(Brilliant-move detection will be less reliable, but everything else works)."
    )


class StockfishEngine:
    def __init__(self, path: str = None, threads: int = 2, hash_mb: int = 128, multipv: int = 1):
        self.path = _find_stockfish(path)
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
        # MultiPV > 1 lets analyze() report the 2nd-best root move's score
        # alongside the best one, in a single search pass -- needed so the
        # classifier can do "Great move" (large gap to 2nd best) detection
        # using Stockfish directly, the same way it already does with
        # own_engine's return_ranked option.
        self._send(f"setoption name MultiPV value {multipv}")
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
        from side-to-move perspective -- mate scores are folded into a large
        cp value, same convention own_engine uses, so callers can treat the
        two engines interchangeably), mate (int or None), ranked (list of
        (move_uci, score_cp) tuples, one per MultiPV line, best-first --
        only has more than one entry if the engine was constructed with
        multipv > 1)}."""
        self._send("ucinewgame")
        self._send("isready")
        self._read_until("readyok")
        self._send(f"position fen {fen}")
        if movetime_ms:
            self._send(f"go movetime {movetime_ms}")
        else:
            self._send(f"go depth {depth}")

        best_move = None
        # multipv rank (1 = best) -> {"move": uci str, "score_cp": int or None, "mate": int or None}
        pv_lines = {}
        while True:
            line = self.proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if line.startswith("info"):
                tokens = line.split()
                if "score" in tokens and "pv" in tokens:
                    rank = 1
                    if "multipv" in tokens:
                        rank = int(tokens[tokens.index("multipv") + 1])
                    score_idx = tokens.index("score")
                    kind = tokens[score_idx + 1]
                    val = int(tokens[score_idx + 2])
                    pv_idx = tokens.index("pv")
                    move = tokens[pv_idx + 1] if len(tokens) > pv_idx + 1 else None
                    entry = {"move": move, "score_cp": None, "mate": None}
                    if kind == "cp":
                        entry["score_cp"] = val
                    elif kind == "mate":
                        entry["mate"] = val
                    pv_lines[rank] = entry  # later (deeper) info lines overwrite earlier ones
            if line.startswith("bestmove"):
                parts = line.split()
                best_move = parts[1] if len(parts) > 1 and parts[1] != "(none)" else None
                break

        def _fold_mate(entry):
            if entry["mate"] is not None:
                cp = 100000 - abs(entry["mate"]) * 100
                return cp if entry["mate"] > 0 else -cp
            return entry["score_cp"]

        ranked = [(pv_lines[r]["move"], _fold_mate(pv_lines[r])) for r in sorted(pv_lines.keys())]
        top_score = ranked[0][1] if ranked else None
        top_mate = pv_lines.get(1, {}).get("mate")

        return {
            "best_move": best_move or (ranked[0][0] if ranked else None),
            "score_cp": top_score,
            "mate": top_mate,
            "ranked": ranked,
        }

    def close(self):
        try:
            self._send("quit")
            self.proc.terminate()
        except Exception:
            pass
