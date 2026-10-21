#!/usr/bin/env python3
"""
analyze.py — CLI chess game analyzer.

Modes
-----
  pgn   Analyze a completed game from a PGN file, printing a classification
        (Brilliant/Great/Best/Excellent/Good/Book/Inaccuracy/Mistake/Blunder)
        for every move, plus a summary and an annotated PGN with NAG-style
        comments you can reopen in any PGN viewer.

  live  Play an interactive game against the engine in the terminal. After
         every move (yours and the engine's), you get instant feedback.

  fetch Pull a recent game straight from chess.com or lichess by username —
        no manual PGN export needed. List mode shows recent games; pick one
        with --index and it's analyzed exactly like `pgn` mode.

Examples
--------
  python3 analyze.py pgn --file game.pgn --depth 6
  python3 analyze.py pgn --file game.pgn --movetime 800 --out annotated.pgn
  python3 analyze.py live --depth 5 --side white
  python3 analyze.py fetch --site chesscom --username hikaru
  python3 analyze.py fetch --site lichess --username DrNykterstein --index 1
"""

import argparse
import io
import sys
import chess
import chess.pgn

from classifier import GameClassifier, MoveAnalysis
from own_engine import Engine as OwnEngine
from fetch_games import fetch_games, FetchError

LABEL_SYMBOLS = {
    "Brilliant": "!!",
    "Great": "!",
    "Best": "\u2605",   # star
    "Excellent": "",
    "Good": "",
    "Book": "",
    "Inaccuracy": "?!",
    "Mistake": "?",
    "Blunder": "??",
}

LABEL_ORDER = ["Brilliant", "Great", "Best", "Excellent", "Good", "Book",
               "Inaccuracy", "Mistake", "Blunder"]


def fmt_cp(cp):
    if cp is None:
        return "n/a"
    if abs(cp) >= 9000:  # mate-range score (own_engine encodes mate as ~+-100000)
        return "#+" if cp > 0 else "#-"
    return f"{cp/100:+.2f}"


def print_move_line(a: MoveAnalysis):
    move_str = f"{a.move_number}." if a.color == "White" else f"{a.move_number}..."
    symbol = LABEL_SYMBOLS.get(a.classification, "")
    line = f"{move_str:>6} {a.san:<8}{symbol:<3} [{a.classification:<10}] " \
           f"eval {fmt_cp(a.eval_after_actual_cp):>7}  (best: {a.engine_best_san}, cp lost: {a.centipawn_loss})"
    print(line)
    for note in a.notes:
        print(f"           \u2514\u2500 {note}")


def print_summary(analyses, white_name="White", black_name="Black"):
    print("\n" + "=" * 72)
    print("SUMMARY")
    print("=" * 72)
    for color, name in [("White", white_name), ("Black", black_name)]:
        counts = {label: 0 for label in LABEL_ORDER}
        total_cp_loss = 0
        n = 0
        for a in analyses:
            if a.color != color:
                continue
            counts[a.classification] += 1
            total_cp_loss += a.centipawn_loss
            n += 1
        avg_loss = total_cp_loss / n if n else 0
        print(f"\n{name} ({color}):")
        for label in LABEL_ORDER:
            if counts[label]:
                print(f"    {label:<12} {counts[label]}")
        print(f"    Average centipawn loss: {avg_loss:.1f}")
        # crude accuracy estimate (Lichess-style logistic curve, simplified)
        accuracy = max(0.0, 100 - avg_loss / 6)
        print(f"    Estimated accuracy: {accuracy:.1f}%")


def write_annotated_pgn(game: chess.pgn.Game, analyses, out_path):
    node = game
    for a in analyses:
        node = node.variations[0] if node.variations else None
        if node is None:
            break
        symbol = LABEL_SYMBOLS.get(a.classification, "")
        comment = f"{a.classification}{(' ' + symbol) if symbol else ''}. " \
                  f"Eval: {fmt_cp(a.eval_after_actual_cp)}. Best: {a.engine_best_san} " \
                  f"(cp lost: {a.centipawn_loss})."
        if a.notes:
            comment += " " + " ".join(a.notes)
        node.comment = comment
    with open(out_path, "w") as f:
        exporter = chess.pgn.FileExporter(f)
        game.accept(exporter)
    print(f"\nAnnotated PGN written to {out_path}")


def analyze_game(game: chess.pgn.Game, args, out_path=None):
    """Shared analysis pipeline used by both `pgn` (file on disk) and
    `fetch` (game pulled live from chess.com/lichess) subcommands."""
    white_name = game.headers.get("White", "White")
    black_name = game.headers.get("Black", "Black")
    print(f"Analyzing: {white_name} vs {black_name}  "
          f"({game.headers.get('Result', '?')}, {game.headers.get('Date', '?')})")
    print(f"Engine: own_engine (depth={args.depth}"
          f"{f', movetime={args.movetime}ms' if args.movetime else ''}) "
          f"+ {'Stockfish verification' if not args.no_stockfish else 'no Stockfish verification'}\n")

    clf = GameClassifier(depth=args.depth, movetime_ms=args.movetime,
                          use_stockfish_verify=not args.no_stockfish,
                          stockfish_path=args.stockfish_path)
    try:
        analyses = clf.classify_game(game)
    finally:
        clf.close()

    for a in analyses:
        print_move_line(a)

    print_summary(analyses, white_name, black_name)

    if out_path:
        write_annotated_pgn(game, analyses, out_path)


def cmd_pgn(args):
    with open(args.file) as f:
        game = chess.pgn.read_game(f)
    if game is None:
        print("Could not read a game from that PGN file.", file=sys.stderr)
        sys.exit(1)
    analyze_game(game, args, out_path=args.out)


def cmd_fetch(args):
    try:
        games = fetch_games(args.site, args.username, max_games=args.list_count)
    except FetchError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if not games:
        print(f"No games found for '{args.username}' on {args.site}.", file=sys.stderr)
        sys.exit(1)

    if args.index is None:
        # Browse mode: just list recent games so the user can pick one.
        print(f"Most recent games for {args.username} on {args.site} "
              f"(newest first) — rerun with --index N to analyze one:\n")
        for g in games:
            print(f"  {g.label()}")
        return

    if not (1 <= args.index <= len(games)):
        print(f"--index must be between 1 and {len(games)} "
              f"(only {len(games)} games were fetched; try --list-count to fetch more).",
              file=sys.stderr)
        sys.exit(1)

    chosen = games[args.index - 1]
    game = chess.pgn.read_game(io.StringIO(chosen.pgn))
    if game is None:
        print("Fetched PGN could not be parsed — the source game may be "
              "incomplete or in an unsupported variant.", file=sys.stderr)
        sys.exit(1)

    print(f"Fetched: {chosen.label()}\n")
    analyze_game(game, args, out_path=args.out)


def cmd_live(args):
    board = chess.Board()
    engine = OwnEngine()
    player_is_white = args.side == "white"

    print("Live mode. Enter moves in SAN (e.g. Nf3) or UCI (e.g. g1f3). "
          "Type 'quit' to stop.\n")
    print(board)
    print()

    clf = GameClassifier(depth=args.depth, movetime_ms=args.movetime,
                          use_stockfish_verify=not args.no_stockfish,
                          stockfish_path=args.stockfish_path)
    try:
        ply = 0
        analyses = []
        while not board.is_game_over():
            is_player_turn = (board.turn == chess.WHITE) == player_is_white
            if is_player_turn:
                move_str = input(f"Your move ({'White' if board.turn else 'Black'}): ").strip()
                if move_str.lower() == "quit":
                    break
                try:
                    move = board.parse_san(move_str)
                except ValueError:
                    try:
                        move = chess.Move.from_uci(move_str)
                        if move not in board.legal_moves:
                            raise ValueError
                    except Exception:
                        print("Could not parse that move, try again.")
                        continue
            else:
                print("Engine is thinking...")
                move, score, _info = engine.search(board, max_depth=args.depth, movetime=args.movetime)
                if move is None:
                    break
                print(f"Engine plays: {board.san(move)}")

            analysis = clf.classify_move(board, move, ply)
            print_move_line(analysis)
            analyses.append(analysis)
            board.push(move)
            print()
            print(board)
            print()
            ply += 1

        print("Game over:", board.result())
        if analyses:
            print_summary(analyses)
    finally:
        clf.close()


def main():
    parser = argparse.ArgumentParser(description="Chess game analyzer with move classification.")
    sub = parser.add_subparsers(dest="mode", required=True)

    pgn_p = sub.add_parser("pgn", help="Analyze a game from a PGN file")
    pgn_p.add_argument("--file", required=True, help="Path to PGN file")
    pgn_p.add_argument("--depth", type=int, default=6, help="Search depth ceiling (default 6)")
    pgn_p.add_argument("--movetime", type=int, default=1000,
                        help="Max milliseconds per position (default 1000; keeps analysis "
                             "practical since own_engine is pure Python)")
    pgn_p.add_argument("--no-stockfish", action="store_true",
                        help="Disable Stockfish verification (Brilliant detection will be less reliable)")
    pgn_p.add_argument("--stockfish-path", default=None,
                        help="Full path to the Stockfish executable, if it's not on your system PATH "
                             "(e.g. on Windows: C:\\stockfish\\stockfish-windows-x86-64-avx2.exe). "
                             "You can also set this via the STOCKFISH_PATH environment variable.")
    pgn_p.add_argument("--out", help="Write an annotated PGN to this path")
    pgn_p.set_defaults(func=cmd_pgn)

    live_p = sub.add_parser("live", help="Play an interactive game with live feedback")
    live_p.add_argument("--side", choices=["white", "black"], default="white",
                         help="Which side you play")
    live_p.add_argument("--depth", type=int, default=6)
    live_p.add_argument("--movetime", type=int, default=1000)
    live_p.add_argument("--no-stockfish", action="store_true")
    live_p.add_argument("--stockfish-path", default=None)
    live_p.set_defaults(func=cmd_live)

    fetch_p = sub.add_parser(
        "fetch",
        help="Pull a recent game straight from chess.com/lichess by username — no PGN export needed")
    fetch_p.add_argument("--site", choices=["chesscom", "lichess"], required=True,
                          help="Which site to fetch from")
    fetch_p.add_argument("--username", required=True, help="Player's username on that site")
    fetch_p.add_argument("--index", type=int, default=None,
                          help="1-based index (newest game = 1) of the game to analyze. "
                               "Omit to just list recent games first.")
    fetch_p.add_argument("--list-count", type=int, default=15,
                          help="How many recent games to fetch/list (default 15)")
    fetch_p.add_argument("--depth", type=int, default=6, help="Search depth ceiling (default 6)")
    fetch_p.add_argument("--movetime", type=int, default=1000,
                          help="Max milliseconds per position (default 1000)")
    fetch_p.add_argument("--no-stockfish", action="store_true",
                          help="Disable Stockfish verification")
    fetch_p.add_argument("--stockfish-path", default=None,
                          help="Full path to the Stockfish executable, if not on your PATH")
    fetch_p.add_argument("--out", help="Write an annotated PGN to this path")
    fetch_p.set_defaults(func=cmd_fetch)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
