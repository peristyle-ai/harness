A read timeout means the upstream RECEIVED the request and did not answer it. Repeating it re-uploads the whole body and waits the same window again, for the same silence -- so on a multi-megabyte body it is minutes per attempt.

On the peristyle install (2026-10-29) evie's turn 559 grew a 2.4 MB request body over 147 iterations. The upstream went quiet, and the harness spent 12 minutes of that 2h27m turn re-sending the same 2.4 MB request four times, each attempt waiting out the full 180s read timeout -- and the turn died anyway. The turn ended with a bare 'ReadTimeout: The read operation timed out', which says nothing about the request that was actually being sent.

Two changes, one concern:

  - a read timeout is no longer retried once the body is at least a megabyte, and the streaming path does not fall back to the buffered POST for one either (the fallback sends the SAME body, and the buffered path must wait for the whole answer, so it is strictly slower than the stream that just timed out). The failure surfaces at once instead of after minutes;

  - the error names the request that was not answered: its size, the window it was given, and the upstream. 'The read operation timed out' is true and useless -- it does not say the request was 2.4 MB, which is the thing the operator can act on.

A dropped connection still retries, on a big body as well: it fails in seconds, and the retry is often what recovers the turn (it was the recovery on 2026-10-27).

This is stacked on the buffered-fallback fix (the streaming path and its fallback send the same body, so the two changes belong together).
