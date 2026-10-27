The loop printed the same message for every turn that ended without a
final answer: 'I wasn't able to finish reasoning within the allowed N
tool iterations'. Two very different situations produce it, and the
message interpolates max_iters, so it always reads like a cap hit:

  CASE A - a genuine cap hit: the loop ran out of iterations while it
           was still making tool calls. The message is right, but it
           never said how many iterations the turn actually used.

  CASE B - the model ended the turn with no text and no tool call. The
           minimax_m31_flash adapter drops thinking blocks from
           response.text, so a reasoning-only response arrives with
           text=None and the loop claimed a cap hit that never
           happened. On 2026-10-27 at 17:26 a pull-request turn whose
           work was DONE reported 'hit the 25-iteration cap' with 477
           iterations still unused, and the operator went looking for
           the wrong bug.

This change:

  * tracks WHY the turn ended (iteration cap / wall-clock cap / final
    answer / no answer) and switches the diagnostic on it;
  * counts the model calls the turn actually made, so the diagnostic
    states the iterations used, not only the cap;
  * does not discard the model's last output when the response carried
    thinking but no text and no tool_use: the reasoning is used as the
    answer (bounded to 4000 chars), or the turn says plainly that the
    model ended the turn without an answer;
  * follows the new wording in app/routes/web.py, which watches for the
    model fabricating the harness's own failure message (it still
    matches the old wording too -- the model can produce that one from
    its own context).

tests/test_loop_end_of_turn.py drives the real agent loop over a fake
adapter: no database, no model, and no tool is ever executed. The six
new behavioural assertions were seen FAILING on the old code before
this change and pass on it now. Full suite: 181 passed, 9 skipped.
