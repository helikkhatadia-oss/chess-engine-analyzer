#!/usr/bin/env python3
"""
fetch_games.py — pull recent games straight from chess.com or lichess by
username, so you can analyze any game without manually exporting a PGN
first (the "Chessigma-style" workflow).

Both sites expose free, no-login public APIs:

  Chess.com (Published-Data API)
    GET /pub/player/{username}/games/archives
        -> {"archives": ["https://api.chess.com/pub/player/{u}/games/{YYYY}/{MM}", ...]}
    GET /pub/player/{username}/games/{YYYY}/{MM}
        -> {"games": [{"pgn": "...", "white": {...}, "black": {...}, "end_time": ..., "url": "...", "time_class": "...", ...}, ...]}

  Lichess
    GET https://lichess.org/api/games/user/{username}?max=N
        -> raw PGN text (multiple games concatenated), newest first by default,
           no auth needed for public games (~20 req/s unauthenticated limit)

Chess.com asks that clients identify themselves with a contact-able
User-Agent (so they can reach out before blocking you rather than just
blocking you), so every request here sends one.
"""

import sys
import io
import json
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import List, Optional

import chess.pgn

USER_AGENT = (
    "chess-engine-analyzer/1.0 "
    "(personal project; contact: see https://github.com/helikkhatadia-oss/chess-engine-analyzer)"
)


class FetchError(Exception):
    pass


@dataclass
class GameSummary:
    index: int              # 1-based, newest first, for picking with --index
    site: str                # "chess.com" / "lichess"
    white: str
    black: str
    result: str
    time_control: str
    date: str
    url: str
    pgn: str

    def label(self) -> str:
        return (f"[{self.index}] {self.white} vs {self.black}  "
                f"({self.result}, {self.time_control}, {self.date})  {self.url}")


def _http_get(url: str, accept: str = None) -> bytes:
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise FetchError(f"Nothing found at {url} (404) — check the username.")
        if e.code == 429:
            raise FetchError("Rate-limited (429). Wait a bit and try again.")
        raise FetchError(f"HTTP {e.code} from {url}: {e.reason}")
    except urllib.error.URLError as e:
        raise FetchError(f"Could not reach {url}: {e.reason}")


# --------------------------------------------------------------------------
# chess.com
# --------------------------------------------------------------------------

def fetch_chesscom_games(username: str, months_back: int = 1, max_games: int = 20) -> List[GameSummary]:
    """Fetch the most recent games for a chess.com username, newest first.
    Walks back through monthly archives (most recent first) until max_games
    is reached or months_back archives have been checked."""
    username = username.strip().lower()
    archives_url = f"https://api.chess.com/pub/player/{username}/games/archives"
    raw = _http_get(archives_url)
    try:
        archives = json.loads(raw)["archives"]
    except (json.JSONDecodeError, KeyError) as e:
        raise FetchError(f"Unexpected response from chess.com archives endpoint: {e}")

    if not archives:
        raise FetchError(f"No chess.com games found for '{username}' (no archives).")

    games: List[GameSummary] = []
    # archives are oldest -> newest; walk from the end (most recent months)
    for month_url in reversed(archives[-months_back:] if months_back else archives):
        raw_month = _http_get(month_url)
        try:
            month_games = json.loads(raw_month).get("games", [])
        except json.JSONDecodeError as e:
            raise FetchError(f"Unexpected response from {month_url}: {e}")
        # within a month, newest-played-last in the API -> reverse for newest-first
        for g in reversed(month_games):
            white = g.get("white", {}).get("username", "?")
            black = g.get("black", {}).get("username", "?")
            white_res = g.get("white", {}).get("result", "")
            black_res = g.get("black", {}).get("result", "")
            result = f"{white_res}/{black_res}" if (white_res or black_res) else "?"
            end_ts = g.get("end_time")
            date = _fmt_ts(end_ts) if end_ts else "?"
            games.append(GameSummary(
                index=0, site="chess.com", white=white, black=black,
                result=result, time_control=g.get("time_class", g.get("time_control", "?")),
                date=date, url=g.get("url", ""), pgn=g.get("pgn", ""),
            ))
            if len(games) >= max_games:
                break
        if len(games) >= max_games:
            break

    for i, g in enumerate(games, start=1):
        g.index = i
    return games


def _fmt_ts(ts: int) -> str:
    import datetime
    return datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------
# lichess
# --------------------------------------------------------------------------

def fetch_lichess_games(username: str, max_games: int = 20) -> List[GameSummary]:
    """Fetch the most recent games for a lichess username (newest first,
    lichess's default ordering). Returns parsed summaries; PGN text is
    kept per-game for later analysis."""
    username = username.strip()
    url = (f"https://lichess.org/api/games/user/{username}"
           f"?max={max_games}&pgnInJson=false&clocks=false&evals=false&opening=false")
    raw = _http_get(url, accept="application/x-chess-pgn")
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        raise FetchError(f"No lichess games found for '{username}'.")

    games: List[GameSummary] = []
    pgn_io = io.StringIO(text)
    while True:
        game = chess.pgn.read_game(pgn_io)
        if game is None:
            break
        h = game.headers
        # Re-export just this game's PGN so each GameSummary is self-contained
        single_pgn = str(game)
        games.append(GameSummary(
            index=0, site="lichess",
            white=h.get("White", "?"), black=h.get("Black", "?"),
            result=h.get("Result", "?"),
            time_control=h.get("TimeControl", "?"),
            date=h.get("UTCDate", h.get("Date", "?")),
            url=h.get("Site", ""), pgn=single_pgn,
        ))

    for i, g in enumerate(games, start=1):
        g.index = i
    return games


# --------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------

def fetch_games(site: str, username: str, max_games: int = 20, months_back: int = 2) -> List[GameSummary]:
    site = site.lower()
    if site in ("chesscom", "chess.com"):
        return fetch_chesscom_games(username, months_back=months_back, max_games=max_games)
    if site == "lichess":
        return fetch_lichess_games(username, max_games=max_games)
    raise FetchError(f"Unknown site '{site}' (expected 'chesscom' or 'lichess').")


if __name__ == "__main__":
    # quick manual smoke test: python3 fetch_games.py chesscom hikaru
    if len(sys.argv) != 3:
        print("Usage: fetch_games.py <chesscom|lichess> <username>")
        sys.exit(1)
    try:
        for g in fetch_games(sys.argv[1], sys.argv[2], max_games=5):
            print(g.label())
    except FetchError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
