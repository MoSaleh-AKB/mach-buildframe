"""R31 (#116): the gp brain's once-guards _REBOOKED, _CONS_CANCELLED and _SORRIED were sets
wiped with .clear() when they passed _SENT_NEEDS, so every guard dropped at once and a recent
citizen could get the returning-citizen apology (or a rebook / Ledger cancel) a second time.
They must drop only the oldest key. Fails on development 8f1eec75 (the recent key is lost),
passes with the bounded OrderedDict.
Run: <city python> -I tests/test_116_gp_dedupe_bounded.py asst/_gp/agent.py"""
import os, sys
os.environ.setdefault("REGRESS_ROLES", "gp")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _rh_world as _rh
from _rh_world import result


class _Msg:
    def __init__(self, need):
        self.need_id, self.sender, self.text = need, "citizen:r31", "ciao"


def main():
    b = _rh.load(sys.argv[1], "gp")
    if isinstance(b, _rh._Stub):
        result("R31", "gp", None, "gp not in REGRESS_ROLES")
        return
    b._returning = lambda chat, agent, message: True
    b._SORRIED.clear()
    cap = b._SENT_NEEDS
    agent = "gp-r31"
    # Fill the apology guard to the cap and one past it (the old code wiped all keys here).
    for i in range(cap + 2):
        b._sorry_line([], agent, _Msg(f"N-{i}"))
    recent = b._sorry_line([], agent, _Msg(f"N-{cap}"))
    result("R31", "gp", recent == "",
           "recent citizen apology stays once past the cap" if recent == "" else
           f"recent citizen N-{cap} got the apology again after the guard hit {cap} (whole set wiped)")
    oldest = b._sorry_line([], agent, _Msg("N-0"))
    result("R31b", "gp", len(b._SORRIED) <= cap and oldest != "",
           f"guard bounded at {len(b._SORRIED)} <= {cap}, oldest key dropped first")
    # The other two guards must not be wipe-on-full sets either.
    bad = [n for n in ("_REBOOKED", "_CONS_CANCELLED", "_SORRIED")
           if isinstance(getattr(b, n), (set, frozenset))]
    src = open(sys.argv[1], encoding="utf-8").read()
    bad += [n + ".clear()" for n in ("_REBOOKED", "_CONS_CANCELLED", "_SORRIED") if n + ".clear()" in src]
    result("R31c", "gp", not bad, "no wipe-all once-guards" if not bad else f"wipe-all guards: {bad}")


main()
_rh.done()
