---
name: handoff
description: Write a short baton so a fresh session can continue this session's work — goal, what is done, what is half-done, decisions and why, constraints word for word, links back to sources, what to verify first, and the next step. Use when a session's context is large and the work is at a natural seam (a commit, a pull request, a finished task), when Scry's context note mentions carrying work into a new session, or when the user asks for a handoff or baton.
---

# Handoff baton

A fresh session starts with nothing but what is written down. A summary kept
only in this session's memory does not carry over, and a summary that keeps
the story and drops the exceptions can read as settled when it is not. Write
the baton to a file in the project (or where the user keeps session notes), not
only into the reply.

Write these, in this order, each as plain lines:

1. **Goal.** What the work is for, in one or two sentences.
2. **Done.** What is finished and where it lives (branch, commit, file).
3. **Half-done.** What is started and not finished, and the state it was left in.
4. **Decisions and why.** What was decided, by whom, and the reason, so the next
   session does not reopen it.
5. **Constraints, word for word.** Every "leave X alone", exact value, name,
   threshold and exception, quoted as given. Summaries lose these first.
6. **Sources.** Links or paths back to the files, tickets or messages the facts
   came from, so the next session can read the source and not trust the baton.
7. **Verify first.** What the next session can check in a minute that would show
   the baton is out of date (git status, a test run, a file's date).
8. **Next step.** The one thing to do first.

A baton is a claim about a moment. It drifts as the work moves, so it carries
the time it was written and the commit it was written against.

At a natural seam the cost of starting fresh is lowest: the work is on disk, and
the cold prompt cache costs one re-read of a small context, not of a large one.
