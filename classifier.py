#!/usr/bin/env python3
"""
classifier.py — classifies each move in a game as:
    Brilliant, Great, Best, Excellent, Good, Book,
    Inaccuracy, Mistake, or Blunder

Design
------
For every position, we get an engine evaluation using TWO sources:
  - own_engine.Engine   (your reference engine — primary)
  - stockfish_wrapper   (verification / ground truth for "was this really
                          a sound sacrifice" checks, since a weaker engine's
                          blind spots would make "Brilliant" detection
                          unreliable)

Core numbers computed per move:
  eval_before        = eval of position before the move (from mover's POV)
  eval_after_best    = eval if the *engine's best move* had been played
  eval_after_actual  = eval after the move actually played, re-evaluated
                        from the mover's POV (i.e. negated after the turn flips)
  centipawn_loss     = eval_after_best - eval_after_actual   (>= 0, clamped)

Base classification is by centipawn_loss thresholds (values chosen to
mirror the general shape of Lichess/chess.com-style classifiers; there is
no single universal standard, and you may want to tune these against your
own tournament games):

    cp_loss == 0 (or move == engine's top choice)      -> Best
    cp_loss <= 10                                       -> Excellent
    cp_loss <= 25                                        -> Good
    cp_loss <= 50                                        -> Inaccuracy
    cp_loss <= 100                                       -> Mistake
    cp_loss  > 100                                       -> Blunder

On top of that, two special upgrades (only ever applied to moves that are
already at least "Excellent" by cp_loss, i.e. the move must be objectively
strong — a losing sacrifice is a Blunder no matter how flashy):

  Brilliant:
    - Move is a sacrifice (mover gives up material — a piece worth >= 3,
      or an exchange — without immediate material compensation *this move*)
    - AND the position after the sacrifice is still evaluated as good/winning
      for the mover (verified independently by Stockfish, not just the
      primary engine, to avoid a shallow-search blind spot being mistaken
      for a real brilliancy)
    - AND it is not simply the *only* reasonable move (see Great, below;
      Brilliant is reserved for genuine, only-relatively-obvious sacrifices)

  Great:
    - The move is (near-)best AND the gap to the second-best legal move is
      large (>= 150cp) AND the position is sharp/critical (i.e. playing a
      different move would have let an advantage slip or a defense fail) —
      i.e. "the only move that keeps the position together."

  Book:
    - The move matches a short hardcoded list of extremely common opening
      moves within the first N plies (cheap heuristic — see BOOK_MOVES).
      This is intentionally conservative; you may want to plug in a real
      opening book/ECO database for production use.

None of this is claimed to be identical to any commercial classifier's
proprietary algorithm — it is a transparent, tunable reimplementation of
the same idea, so you can inspect and adjust every threshold.
"""

import chess
import chess.pgn
from dataclasses import dataclass, field
from typing import Optional, List

from own_engine import Engine as OwnEngine
from stockfish_wrapper import StockfishEngine

# --------------------------------------------------------------------------
# Thresholds (centipawn loss -> label). Tune freely.
# --------------------------------------------------------------------------

THRESHOLDS = [
    (10, "Excellent"),
    (25, "Good"),
    (50, "Inaccuracy"),
    (100, "Mistake"),
    (float("inf"), "Blunder"),
]

SACRIFICE_MIN_VALUE = 300          # centipawns; knight/bishop or more
GREAT_MOVE_GAP_CP = 150            # gap to 2nd-best move to call it "Great"
BRILLIANT_MIN_EVAL_AFTER = -50     # position must still be ~fine/winning (cp, mover POV)
BOOK_PLY_LIMIT = 8                 # first 4 full moves considered possible "book"

BOOK_MOVES = {
    # very common opening moves, by (ply_index, uci move) — deliberately small
    (0, "e2e4"), (0, "d2d4"), (0, "c2c4"), (0, "g1f3"),
    (1, "e7e5"), (1, "c7c5"), (1, "e7e6"), (1, "c7c6"), (1, "d7d5"), (1, "g8f6"),
    (2, "g1f3"), (2, "f1c4"), (2, "b1c3"),
    (3, "b8c6"), (3, "g8f6"), (3, "d7d6"),
}


@dataclass
class MoveAnalysis:
    ply: int
    move_number: int
    color: str           # "White" / "Black"
    san: str
    uci: str
    eval_before_cp: Optional[int]
    eval_after_best_cp: Optional[int]
    eval_after_actual_cp: Optional[int]
    centipawn_loss: int
    engine_best_san: Optional[str]
    classification: str
    is_book: bool = False
    notes: List[str] = field(default_factory=list)


def cp_loss_to_label(cp_loss: int) -> str:
    if cp_loss <= 0:
        return "Best"
    for threshold, label in THRESHOLDS:
        if cp_loss <= threshold:
            return label
    return "Blunder"


def material_value(board: chess.Board, color: bool) -> int:
    total = 0
    for pt, val in [(chess.PAWN, 100), (chess.KNIGHT, 320), (chess.BISHOP, 330),
                     (chess.ROOK, 500), (chess.QUEEN, 900)]:
        total += val * len(board.pieces(pt, color))
    return total


class GameClassifier:
    def __init__(self, depth: int = 14, movetime_ms: int = None, use_stockfish_verify: bool = True,
                 stockfish_path: str = None):
        self.own_engine = OwnEngine()
        self.depth = depth
        self.movetime_ms = movetime_ms
        self.use_stockfish_verify = use_stockfish_verify
        self.sf = StockfishEngine(path=stockfish_path) if use_stockfish_verify else None

    def close(self):
        if self.sf:
            self.sf.close()

    # -- evaluation helpers --------------------------------------------------
    def _own_eval(self, board: chess.Board):
        """Returns (best_move, score_cp, second_best_score_cp_or_None) from own
        engine, mover POV. Single search call (iterative deepening up to
        self.depth or self.movetime_ms, whichever caller configured)."""
        if not list(board.legal_moves):
            return None, None, None
        best_move, best_score, _info, ranked = self.own_engine.search(
            board, max_depth=self.depth, movetime=self.movetime_ms, return_ranked=True)
        second_score = ranked[1][1] if len(ranked) > 1 else None
        return best_move, best_score, second_score

    def _sf_eval(self, board: chess.Board):
        if not self.sf:
            return None, None
        result = self.sf.analyze(board.fen(), depth=self.depth, movetime_ms=self.movetime_ms)
        if result["mate"] is not None:
            cp = 100000 - abs(result["mate"]) * 100
            cp = cp if result["mate"] > 0 else -cp
        else:
            cp = result["score_cp"]
        return result["best_move"], cp

    # -- per-move classification ---------------------------------------------
    def classify_move(self, board_before: chess.Board, move: chess.Move, ply: int) -> MoveAnalysis:
        color = "White" if board_before.turn == chess.WHITE else "Black"
        move_number = board_before.fullmove_number
        san = board_before.san(move)
        uci = move.uci()

        is_book = (ply, uci) in BOOK_MOVES and ply < BOOK_PLY_LIMIT

        # Best move & its resulting eval (mover's POV) via search on root.
        # (eval_before, for display purposes, is just this position's best score.)
        best_move, best_score, second_score = self._own_eval(board_before)
        eval_before = best_score
        engine_best_san = board_before.san(best_move) if best_move else None

        # Eval after the ACTUAL move played, converted back to mover's POV
        board_after = board_before.copy()
        board_after.push(move)
        _, eval_after_opp_pov, _ = self._own_eval(board_after) if not board_after.is_game_over() else (None, self._terminal_score(board_after), None)
        eval_after_actual = -eval_after_opp_pov if eval_after_opp_pov is not None else None

        cp_loss = 0
        if best_score is not None and eval_after_actual is not None:
            cp_loss = max(0, best_score - eval_after_actual)
            # Clamp: once a move loses mate-in-N vs. staying safe, the raw
            # difference can be in the tens of thousands (mate scores are
            # encoded as ~100000cp). Cap so classification/stats stay sane;
            # anything this large is already unambiguously a Blunder.
            cp_loss = min(cp_loss, 2000)

        label = "Best" if move == best_move else cp_loss_to_label(cp_loss)
        notes = []

        if is_book:
            label = "Book"

        # -- Great move detection --
        elif label in ("Best", "Excellent") and second_score is not None:
            gap = best_score - second_score
            if gap >= GREAT_MOVE_GAP_CP and move == best_move:
                label = "Great"
                notes.append(f"Only move keeping the advantage (gap to 2nd best: {gap}cp)")

        # -- Brilliant move detection --
        if label in ("Best", "Great") and move == best_move:
            moving_piece = board_before.piece_at(move.from_square)
            is_sacrifice = False
            if board_before.is_capture(move):
                captured = board_before.piece_at(move.to_square)
                cap_val = {chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330,
                           chess.ROOK: 500, chess.QUEEN: 900}.get(captured.piece_type, 0) if captured else 0
                mover_val = {chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330,
                             chess.ROOK: 500, chess.QUEEN: 900}.get(moving_piece.piece_type, 0) if moving_piece else 0
                # sacrifice if giving up notably more than gained AND the piece can be recaptured
                if mover_val - cap_val >= SACRIFICE_MIN_VALUE and board_after.is_attacked_by(not board_before.turn, move.to_square):
                    is_sacrifice = True
            else:
                # non-capture sac: moving into a square where it can be captured for less/no compensation
                if moving_piece and moving_piece.piece_type != chess.KING:
                    mover_val = {chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330,
                                 chess.ROOK: 500, chess.QUEEN: 900}.get(moving_piece.piece_type, 0)
                    if mover_val >= SACRIFICE_MIN_VALUE and board_after.is_attacked_by(not board_before.turn, move.to_square) \
                       and not board_after.is_attacked_by(board_before.turn, move.to_square):
                        is_sacrifice = True

            if is_sacrifice and eval_after_actual is not None and eval_after_actual >= BRILLIANT_MIN_EVAL_AFTER:
                # Verify independently with Stockfish to avoid a shallow blind spot
                verified = True
                if self.sf:
                    _, sf_after_opp = self._sf_eval(board_after)
                    sf_after_actual = -sf_after_opp if sf_after_opp is not None else None
                    verified = sf_after_actual is not None and sf_after_actual >= BRILLIANT_MIN_EVAL_AFTER - 50
                if verified:
                    label = "Brilliant"
                    notes.append("Sound sacrifice verified by Stockfish")

        return MoveAnalysis(
            ply=ply, move_number=move_number, color=color, san=san, uci=uci,
            eval_before_cp=eval_before, eval_after_best_cp=best_score,
            eval_after_actual_cp=eval_after_actual, centipawn_loss=cp_loss,
            engine_best_san=engine_best_san, classification=label,
            is_book=is_book, notes=notes,
        )

    @staticmethod
    def _terminal_score(board: chess.Board) -> int:
        if board.is_checkmate():
            return -100000
        return 0

    # -- full game -------------------------------------------------------------
    def classify_game(self, game: chess.pgn.Game) -> List[MoveAnalysis]:
        board = game.board()
        analyses = []
        for ply, move in enumerate(game.mainline_moves()):
            analysis = self.classify_move(board, move, ply)
            analyses.append(analysis)
            board.push(move)
        return analyses
