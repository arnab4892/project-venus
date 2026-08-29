"""Eval harness (HLD-C-10 / LLD §11, LLD-EVAL-01/02/03).

:mod:`agentkit.eval.runner` runs ``clients/<client>/golden/questions.yaml`` at its fact and
retrieval layers against the live active release; the end-to-end (agent-answer) layer is
reported ``pending`` until the agents land (milestone 5). A failing fact/retrieval check
fails the run, which gates ``release activate`` (LLD-EVAL-03).
"""
