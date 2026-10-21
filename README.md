# Chess Analyzer

A move-classification chess analysis tool: for every move in a game (from a
PGN file, or played live in the terminal), it tells you whether the move was

**Brilliant · Great · Best · Excellent · Good · Book · Inaccuracy · Mistake · Blunder**

## Files

| File                    | Purpose |
|-------------------------|---------|
| `own_engine.py`         | Your reference engine: negamax + alpha-beta pruning + quiescence search + transposition table, UCI-compatible. Used as the **fallback evaluator** when Stockfish isn't installed (see below). **Swap this out for your real engine** — as long as it exposes the same `Engine.search(board, max_depth, movetime, return_ranked)` interface, nothing else needs to change. |
| `stockfish_wrapper.py`  | Thin subprocess wrapper around the system Stockfish binary. When Stockfish is available, it's the **primary evaluator for every move** (not just a sacrifice spot-check — see "Engine architecture" below). |
| `classifier.py`         | The actual classification logic — thresholds, sacrifice detection, "Great move" (only-move) detection, book-move heuristic. |
| `analyze.py`            | CLI entry point: `pgn` mode (analyze a PGN file), `live` mode (play interactively), and `fetch` mode (pull a game straight from chess.com/lichess by username — no PGN export needed). |
| `fetch_games.py`        | Pulls recent games from the chess.com and lichess public APIs by username. |
| `examples/sample_game.pgn` | The famous Morphy "Opera Game" (1858), included as a worked example — see below. |
| `examples/annotated_opera_game.pgn` | The Opera Game annotated by this tool's classifier — see "Worked example" below. |
| `examples/sample_analysis_output.txt` | Full console output from running the analyzer on the Opera Game. |

## Setup

```bash
pip install -r requirements.txt
# (or: pip install chess --break-system-packages, on systems that need the flag)
```

Stockfish is used only for Brilliant-move verification, not primary analysis, so it's
optional — the tool runs fine without it via `--no-stockfish`, just with less
reliable Brilliant detection. To install it:

- **Linux:** `sudo apt-get install stockfish`
- **macOS:** `brew install stockfish`
- **Windows:** there's no package-manager default. Download the Windows build from
  [stockfishchess.org/download/windows](https://stockfishchess.org/download/windows/),
  extract the `.zip`, and either:
  - add the folder containing `stockfish*.exe` to your system PATH, or
  - pass the `.exe`'s full path directly: `--stockfish-path C:\path\to\stockfish-windows-x86-64-avx2.exe`, or
  - set an environment variable: `set STOCKFISH_PATH=C:\path\to\stockfish.exe`

If Stockfish isn't found, the tool raises a clear error telling you exactly which of
these to do — it won't fail with an opaque traceback.

## Usage

### Analyze a PGN file

```bash
python3 analyze.py pgn --file examples/sample_game.pgn --depth 6 --movetime 1000 --out annotated.pgn
```

- `--depth` — search depth ceiling (default 6)
- `--movetime` — max milliseconds per position (default 1000ms) — this is
  the real practical limiter, since `own_engine.py` is pure Python and much
  slower than a compiled engine; see "Performance" below.
- `--no-stockfish` — skip Stockfish verification (Brilliant detection
  becomes less reliable, since it then trusts only the primary engine's
  own search)
- `--stockfish-path` — full path to the Stockfish executable, if it's
  not discoverable on your system PATH (mainly needed on Windows — see
  Setup above)
- `--out` — write an annotated PGN with a classification comment on every
  move, viewable in any PGN reader

### Play live with feedback

```bash
python3 analyze.py live --side white --depth 5 --movetime 800
```

Enter moves in SAN (`Nf3`) or UCI (`g1f3`); type `quit` to stop. You get a
classification after every move — yours and the engine's.

### Analyze a game straight from chess.com or lichess (no export needed)

No need to manually export a PGN first — just point the tool at a username,
the same way Chessigma-style analyzers work:

```bash
# Browse mode: list someone's recent games first
python3 analyze.py fetch --site chesscom --username hikaru
python3 analyze.py fetch --site lichess --username DrNykterstein

# Then pick one (1 = most recent) and analyze it
python3 analyze.py fetch --site chesscom --username hikaru --index 1
python3 analyze.py fetch --site lichess --username DrNykterstein --index 3 --out annotated.pgn
```

- `--site` — `chesscom` or `lichess`
- `--username` — the player's username on that site (any public account works,
  including your own)
- Omit `--index` to just list recent games (newest first) with opponent,
  result, time control and date, so you can see what's there before
  committing to a full analysis
- `--index N` — analyze the Nth game from that list (1 = most recent)
- `--list-count` — how many recent games to pull (default 15)
- All the usual `--depth`, `--movetime`, `--no-stockfish`, `--stockfish-path`
  and `--out` flags from `pgn` mode work the same way here, since under the
  hood it's the exact same classifier — only the source of the PGN differs

Both sites' APIs are free and need no login or API key for public games.
chess.com's API does ask that requests identify themselves, so this tool
sends a descriptive `User-Agent`; be considerate with `--list-count` if
you're pulling someone's whole history.

## Engine architecture

**When Stockfish is available, it is the primary evaluator for every move** —
not just a secondary check on flashy sacrifices. `own_engine.py` (the
pure-Python reference engine) is used only as a fallback when Stockfish
isn't installed (or `--no-stockfish` is passed).

This wasn't the original design, and it's worth being honest about why it
changed: `own_engine` at a practical depth (4-6) handles sharp, forced
sequences reasonably well, but was found — by comparing its output against
a real Stockfish-backed analyzer on an actual played game — to badly
misjudge quiet, positional moments. It flagged standard, well-known
opening theory (the Ruy Lopez, Open Variation) as a string of outright
blunders, simply because its evaluation function is material + piece-square
tables only, with no king-safety or long-term positional understanding.
Using real Stockfish for every move (not just sacrifice spot-checks) fixed
this, and is also *faster* than the old approach, since Stockfish is many
orders of magnitude faster per node than a pure-Python search.

**Practical implication:** install Stockfish if you want classifications
you can actually trust (see Setup above) — `--no-stockfish` still works,
but treat its output as a rough approximation, not a real analysis. The
CLI's "Engine:" line at the top of every run tells you honestly which mode
you're in.

## Worked example: the Opera Game

`examples/sample_game.pgn` is Paul Morphy's famous 1858 game against the Duke of
Brunswick and Count Isouard. Running the analyzer on it correctly:

- Flags **15...Nxd7?? as a Blunder** (the recapture that allows the mating
  combination — the historically-known losing move)
- Flags **16.Qb8+!! as Brilliant** (a genuine queen sacrifice, verified by
  Stockfish to still be winning) — this is the move the game is famous for

That's a reasonable sanity check that the classification logic is doing
something real, not just echoing engine best-moves.

## How classification works

For each move: the engine evaluates the position before the move, the
resulting position if its own top choice had been played, and the resulting
position after the move actually played. The gap between those last two
(**centipawn loss**) drives the base label:

| Centipawn loss | Label |
|---|---|
| 0 (matches engine's top choice) | Best |
| ≤ 10 | Excellent |
| ≤ 25 | Good |
| ≤ 50 | Inaccuracy |
| ≤ 100 | Mistake |
| > 100 | Blunder |

Two upgrades sit on top of that (only ever applied to moves that are already
objectively strong — a losing sacrifice is still a Blunder, however flashy):

- **Brilliant** — the move gives up material (piece/exchange or more)
  without immediate compensation, the position is still good/winning
  afterward, and this is confirmed independently by Stockfish (guards
  against the primary engine's shallow search mistaking a losing sac for a
  sound one).
- **Great** — the move is the *only* move keeping the position together:
  it's best, and the gap to the second-best legal move is large (≥150cp).

**Book** is a small hardcoded table of common opening moves in the first
few plies — deliberately minimal; swap in a real opening-book/ECO database
if you want broader, more accurate book detection.

All thresholds live at the top of `classifier.py` and are meant to be
tuned — there's no single agreed-upon industry standard for these
categories (chess.com and Lichess each use their own undisclosed/
semi-disclosed logic), so treat the numbers here as a reasonable, fully
transparent starting point, not gospel.

## Performance

`own_engine.py` is a plain-Python negamax engine — readable and matches the
spec (alpha-beta, quiescence, TT), but it's roughly 2-3 orders of magnitude
slower than a compiled engine like Stockfish at the same nominal depth. In
practice that means:

- Depth 4-5 with a 300-1000ms movetime cap is fast enough for casual
  analysis of a full game (tens of seconds to a few minutes).
- Depth 6+ gets noticeably slow position-by-position (multiple seconds
  each) since there's no null-move pruning, killer-move heuristics beyond
  basic MVV-LVA ordering, or late-move reductions.
- **This is exactly where swapping in your real engine matters most** —
  if yours is faster or has more pruning, drop it in via the same
  interface and you can raise the depth ceiling substantially.

## Known limitations / honesty check

This is a transparent reimplementation of the *idea* behind chess.com/
Lichess-style move classification, not a claim to replicate their exact
(partly proprietary) algorithms. Specific things to be aware of:

- The static evaluation function is a standard material + piece-square-table
  model — no king safety, pawn structure, or endgame tablebase knowledge
  beyond a basic midgame/endgame king PST switch. Its evaluations at low
  depth can be noisy in sharp tactical positions.
- "Book" detection is a short hardcoded list, not a real opening database.
- "Great move" detection uses a fixed 150cp gap threshold, which is a
  reasonable heuristic but will occasionally mis-fire in unusual positions.
- Mate-range scores are clamped for display and centipawn-loss statistics
  (real "distance to mate" isn't linear with centipawns) — the CLI shows
  `#+`/`#-` instead of a raw number once a position is essentially resolved.
