#!/usr/bin/env python3
"""
own_engine.py — A UCI-compatible chess engine.

Reference implementation matching the spec: negamax search with alpha-beta
pruning, quiescence search at leaf nodes, and a transposition table (Zobrist
hashing). Communicates over stdin/stdout using the UCI protocol, so it is a
drop-in replacement target — swap this file out for your own engine's UCI
binary/script and everything else (classifier, CLI) keeps working unchanged.

Usage as a standalone UCI engine:
    python3 own_engine.py
    (then type UCI commands: uci / isready / position ... / go movetime N)

It is also imported directly by classifier.py for in-process use (faster,
no subprocess overhead), via the Engine class.
"""

import sys
import time
import chess

# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------

PIECE_VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0,
}

# Piece-square tables (white's perspective; mirrored for black).
PAWN_PST = [
      0,   0,   0,   0,   0,   0,   0,   0,
     50,  50,  50,  50,  50,  50,  50,  50,
     10,  10,  20,  30,  30,  20,  10,  10,
      5,   5,  10,  25,  25,  10,   5,   5,
      0,   0,   0,  20,  20,   0,   0,   0,
      5,  -5, -10,   0,   0, -10,  -5,   5,
      5,  10,  10, -20, -20,  10,  10,   5,
      0,   0,   0,   0,   0,   0,   0,   0,
]
KNIGHT_PST = [
    -50, -40, -30, -30, -30, -30, -40, -50,
    -40, -20,   0,   0,   0,   0, -20, -40,
    -30,   0,  10,  15,  15,  10,   0, -30,
    -30,   5,  15,  20,  20,  15,   5, -30,
    -30,   0,  15,  20,  20,  15,   0, -30,
    -30,   5,  10,  15,  15,  10,   5, -30,
    -40, -20,   0,   5,   5,   0, -20, -40,
    -50, -40, -30, -30, -30, -30, -40, -50,
]
BISHOP_PST = [
    -20, -10, -10, -10, -10, -10, -10, -20,
    -10,   0,   0,   0,   0,   0,   0, -10,
    -10,   0,   5,  10,  10,   5,   0, -10,
    -10,   5,   5,  10,  10,   5,   5, -10,
    -10,   0,  10,  10,  10,  10,   0, -10,
    -10,  10,  10,  10,  10,  10,  10, -10,
    -10,   5,   0,   0,   0,   0,   5, -10,
    -20, -10, -10, -10, -10, -10, -10, -20,
]
ROOK_PST = [
      0,   0,   0,   0,   0,   0,   0,   0,
      5,  10,  10,  10,  10,  10,  10,   5,
     -5,   0,   0,   0,   0,   0,   0,  -5,
     -5,   0,   0,   0,   0,   0,   0,  -5,
     -5,   0,   0,   0,   0,   0,   0,  -5,
     -5,   0,   0,   0,   0,   0,   0,  -5,
     -5,   0,   0,   0,   0,   0,   0,  -5,
      0,   0,   0,   5,   5,   0,   0,   0,
]
QUEEN_PST = [
    -20, -10, -10,  -5,  -5, -10, -10, -20,
    -10,   0,   0,   0,   0,   0,   0, -10,
    -10,   0,   5,   5,   5,   5,   0, -10,
     -5,   0,   5,   5,   5,   5,   0,  -5,
      0,   0,   5,   5,   5,   5,   0,  -5,
    -10,   5,   5,   5,   5,   5,   0, -10,
    -10,   0,   5,   0,   0,   0,   0, -10,
    -20, -10, -10,  -5,  -5, -10, -10, -20,
]
KING_PST_MID = [
    -30, -40, -40, -50, -50, -40, -40, -30,
    -30, -40, -40, -50, -50, -40, -40, -30,
    -30, -40, -40, -50, -50, -40, -40, -30,
    -30, -40, -40, -50, -50, -40, -40, -30,
    -20, -30, -30, -40, -40, -30, -30, -20,
    -10, -20, -20, -20, -20, -20, -20, -10,
     20,  20,   0,   0,   0,   0,  20,  20,
     20,  30,  10,   0,   0,  10,  30,  20,
]
KING_PST_END = [
    -50, -40, -30, -20, -20, -30, -40, -50,
    -30, -20, -10,   0,   0, -10, -20, -30,
    -30, -10,  20,  30,  30,  20, -10, -30,
    -30, -10,  30,  40,  40,  30, -10, -30,
    -30, -10,  30,  40,  40,  30, -10, -30,
    -30, -10,  20,  30,  30,  20, -10, -30,
    -30, -30,   0,   0,   0,   0, -30, -30,
    -50, -30, -30, -30, -30, -30, -30, -50,
]

PST = {
    chess.PAWN: PAWN_PST,
    chess.KNIGHT: KNIGHT_PST,
    chess.BISHOP: BISHOP_PST,
    chess.ROOK: ROOK_PST,
    chess.QUEEN: QUEEN_PST,
}


def is_endgame(board: chess.Board) -> bool:
    queens = len(board.pieces(chess.QUEEN, chess.WHITE)) + len(board.pieces(chess.QUEEN, chess.BLACK))
    minors_majors = 0
    for color in (chess.WHITE, chess.BLACK):
        for pt in (chess.KNIGHT, chess.BISHOP, chess.ROOK):
            minors_majors += len(board.pieces(pt, color))
    return queens == 0 or (queens <= 2 and minors_majors <= 4)


def evaluate(board: chess.Board) -> int:
    """Static evaluation in centipawns, from the side-to-move's perspective."""
    if board.is_checkmate():
        return -100000
    # NOTE: deliberately NOT calling board.can_claim_draw() here — on a
    # position with a long real-game move stack behind it (e.g. analyzing
    # move 40 of an actual played game), that call triggers python-chess's
    # threefold-repetition scan, which walks the *entire* move history.
    # This function runs at every quiescence leaf, so that cost multiplies
    # into minutes of hanging on anything but a short game. is_fifty_moves()
    # is an O(1) halfmove-clock check and covers the common draw case
    # cheaply; true in-search repetition is handled separately in negamax()
    # via Engine.search_path, scoped to the current search line only.
    if board.is_stalemate() or board.is_insufficient_material() or board.is_fifty_moves():
        return 0

    endgame = is_endgame(board)
    score = 0
    for square in chess.SQUARES:
        piece = board.piece_at(square)
        if piece is None:
            continue
        value = PIECE_VALUES[piece.piece_type]
        if piece.piece_type == chess.KING:
            table = KING_PST_END if endgame else KING_PST_MID
        else:
            table = PST[piece.piece_type]
        idx = square if piece.color == chess.WHITE else chess.square_mirror(square)
        pst_bonus = table[idx]
        if piece.color == chess.WHITE:
            score += value + pst_bonus
        else:
            score -= value + pst_bonus

    return score if board.turn == chess.WHITE else -score


# --------------------------------------------------------------------------
# Transposition table
# --------------------------------------------------------------------------

EXACT, LOWERBOUND, UPPERBOUND = 0, 1, 2


class TTEntry:
    __slots__ = ("depth", "value", "flag", "best_move")

    def __init__(self, depth, value, flag, best_move):
        self.depth = depth
        self.value = value
        self.flag = flag
        self.best_move = best_move


class Engine:
    """In-process negamax engine with alpha-beta, quiescence, and a TT."""

    def __init__(self, tt_size: int = 1_000_000):
        self.tt = {}
        self.tt_size = tt_size
        self.nodes = 0
        self.start_time = 0.0
        self.time_limit = None
        self.stop_search = False
        # Positions visited along the CURRENT search line (root -> current
        # node), reset at the start of each search() call. Used to detect an
        # in-search repetition cheaply (O(1) set lookup) instead of calling
        # board.can_claim_draw(), which rescans the whole real-game move
        # stack and is far too slow to call at every search node on a long
        # real game (see evaluate()'s docstring note above).
        self.search_path = set()

    # -- move ordering -----------------------------------------------------
    def _order_moves(self, board: chess.Board, moves, tt_move=None):
        def score(move):
            if tt_move is not None and move == tt_move:
                return 1_000_000
            if board.is_capture(move):
                victim = board.piece_at(move.to_square)
                attacker = board.piece_at(move.from_square)
                v = PIECE_VALUES[victim.piece_type] if victim else 100
                a = PIECE_VALUES[attacker.piece_type] if attacker else 0
                return 100_000 + v * 10 - a
            if move.promotion:
                return 90_000
            return 0
        return sorted(moves, key=score, reverse=True)

    # -- quiescence search ---------------------------------------------------
    def quiescence(self, board: chess.Board, alpha: int, beta: int, depth: int = 0) -> int:
        self.nodes += 1
        stand_pat = evaluate(board)
        if depth > 6:
            return stand_pat
        if stand_pat >= beta:
            return beta
        if alpha < stand_pat:
            alpha = stand_pat

        captures = [m for m in board.legal_moves if board.is_capture(m) or m.promotion]
        captures = self._order_moves(board, captures)
        for move in captures:
            board.push(move)
            score = -self.quiescence(board, -beta, -alpha, depth + 1)
            board.pop()
            if score >= beta:
                return beta
            if score > alpha:
                alpha = score
        return alpha

    # -- negamax with alpha-beta and TT --------------------------------------
    def negamax(self, board: chess.Board, depth: int, alpha: int, beta: int) -> int:
        self.nodes += 1

        if self.time_limit is not None and (self.nodes & 1023) == 0:
            if time.time() - self.start_time > self.time_limit:
                self.stop_search = True
        if self.stop_search:
            return 0

        alpha_orig = alpha
        key = hash(board._transposition_key())

        # In-search repetition check: O(1) against positions visited along
        # THIS recursive line, instead of board.can_claim_draw() (which
        # scans the entire real-game move stack and is the thing that was
        # making this hang for minutes on a real, long game — see
        # evaluate()'s docstring note). Treating any repeat within the
        # search line as a draw is a standard, cheap engine approximation.
        if key in self.search_path:
            return 0

        entry = self.tt.get(key)
        tt_move = None
        if entry is not None and entry.depth >= depth:
            tt_move = entry.best_move
            if entry.flag == EXACT:
                return entry.value
            elif entry.flag == LOWERBOUND:
                alpha = max(alpha, entry.value)
            elif entry.flag == UPPERBOUND:
                beta = min(beta, entry.value)
            if alpha >= beta:
                return entry.value
        elif entry is not None:
            tt_move = entry.best_move

        if board.is_checkmate():
            return -100000 + (1000 - depth)  # prefer faster mates
        if board.is_stalemate() or board.is_insufficient_material() or board.is_fifty_moves():
            return 0
        if depth == 0:
            return self.quiescence(board, alpha, beta)

        best_value = -10**9
        best_move = None
        moves = self._order_moves(board, list(board.legal_moves), tt_move)
        self.search_path.add(key)
        for move in moves:
            board.push(move)
            value = -self.negamax(board, depth - 1, -beta, -alpha)
            board.pop()
            if self.stop_search:
                self.search_path.discard(key)
                return 0
            if value > best_value:
                best_value = value
                best_move = move
            if best_value > alpha:
                alpha = best_value
            if alpha >= beta:
                break
        self.search_path.discard(key)

        flag = EXACT
        if best_value <= alpha_orig:
            flag = UPPERBOUND
        elif best_value >= beta:
            flag = LOWERBOUND
        if len(self.tt) < self.tt_size:
            self.tt[key] = TTEntry(depth, best_value, flag, best_move)

        return best_value

    # -- iterative deepening driver ------------------------------------------
    def search(self, board: chess.Board, max_depth: int = 5, movetime: float = None,
               return_ranked: bool = False):
        """Returns (best_move, score_cp, info_list) by default. score_cp is from
        side-to-move perspective. If return_ranked=True, returns
        (best_move, score_cp, info_list, ranked) where ranked is a list of
        (move, score_cp) for every legal root move, sorted best-first, from
        the final completed depth — this avoids a second search pass when
        the caller also needs the 2nd-best move's score (e.g. for "Great
        move" detection)."""
        self.nodes = 0
        self.stop_search = False
        self.start_time = time.time()
        self.time_limit = movetime
        self.search_path = set()  # fresh per top-level search() call

        best_move = None
        best_score = 0
        info = []
        ranked = []

        root_key = hash(board._transposition_key())
        for depth in range(1, max_depth + 1):
            alpha, beta = -10**9, 10**9
            moves = self._order_moves(board, list(board.legal_moves), best_move)
            iter_scores = []
            iter_best_move = None
            iter_best_value = -10**9
            self.search_path.add(root_key)
            for move in moves:
                board.push(move)
                value = -self.negamax(board, depth - 1, -beta, -alpha)
                board.pop()
                if self.stop_search:
                    break
                iter_scores.append((move, value))
                if value > iter_best_value:
                    iter_best_value = value
                    iter_best_move = move
                if iter_best_value > alpha:
                    alpha = iter_best_value

            if self.stop_search or iter_best_move is None:
                break

            best_move = iter_best_move
            best_score = iter_best_value
            ranked = sorted(iter_scores, key=lambda x: -x[1])
            elapsed = time.time() - self.start_time
            info.append({"depth": depth, "score_cp": best_score, "nodes": self.nodes,
                          "time": elapsed, "pv": best_move.uci() if best_move else None})

            if self.time_limit is not None and elapsed > self.time_limit:
                break

        if return_ranked:
            return best_move, best_score, info, ranked
        return best_move, best_score, info


# --------------------------------------------------------------------------
# UCI protocol loop (for standalone use / interoperability with GUIs)
# --------------------------------------------------------------------------

def uci_main():
    board = chess.Board()
    engine = Engine()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        tokens = line.split()
        cmd = tokens[0]

        if cmd == "uci":
            print("id name OwnEngine")
            print("id author Helik")
            print("uciok")
            sys.stdout.flush()
        elif cmd == "isready":
            print("readyok")
            sys.stdout.flush()
        elif cmd == "ucinewgame":
            board = chess.Board()
            engine.tt.clear()
        elif cmd == "position":
            idx = 1
            if tokens[idx] == "startpos":
                board = chess.Board()
                idx += 1
            elif tokens[idx] == "fen":
                fen = " ".join(tokens[idx + 1: idx + 7])
                board = chess.Board(fen)
                idx += 7
            if idx < len(tokens) and tokens[idx] == "moves":
                for mv in tokens[idx + 1:]:
                    board.push_uci(mv)
        elif cmd == "go":
            movetime = None
            depth = 5
            if "movetime" in tokens:
                movetime = int(tokens[tokens.index("movetime") + 1]) / 1000.0
            if "depth" in tokens:
                depth = int(tokens[tokens.index("depth") + 1])
            best_move, score, info = engine.search(board, max_depth=depth, movetime=movetime)
            for entry in info:
                print(f"info depth {entry['depth']} score cp {entry['score_cp']} "
                      f"nodes {entry['nodes']} time {int(entry['time']*1000)} pv {entry['pv']}")
            print(f"bestmove {best_move.uci() if best_move else '0000'}")
            sys.stdout.flush()
        elif cmd == "quit":
            break


if __name__ == "__main__":
    uci_main()
