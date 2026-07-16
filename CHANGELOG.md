# Changelog

Tracks design decisions and experimental conditions as the project
evolved, not just file changes — the "why," not only the "what."

## Engine core (negamax / alpha-beta / quiescence / transposition table)
Established the base search engine. Chose negamax over separate
minimax branches for code simplicity (chess is zero-sum, so the
symmetry is free to exploit). Added quiescence search after noticing
naive fixed-depth search gave wildly wrong evaluations mid-capture-
sequence (the horizon effect) — this was the first real correctness
issue found in testing. Transposition table added once repeated
positions from transpositions were confirmed to be re-searched
unnecessarily in test games.

## Stockfish verification wrapper
Added as a dependency of the move classifier, specifically for
sacrifice verification — realised during early classifier testing
that the primary engine's own search depth wasn't reliable enough on
its own to distinguish a genuine brilliancy from a losing sacrifice
that only looks good a few plies deep.
