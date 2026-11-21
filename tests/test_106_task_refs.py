"""#106 (R21): every background asyncio task in a brain keeps a strong reference until it is done.
The event loop holds tasks only weakly, so a task nobody holds can be garbage-collected mid-run.

A create_task / ensure_future call passes when it is wrapped in _keep / _keep_task, stored in a
container (X[k] = task, X[:] = [task]), or put in a set or dict by the very next statement. The resume task
must be kept too: _resume_offers pops it from _RESUMING before its last _advance step.
Then a live check: a kept task finishes after gc.collect() with no other reference.

Run: ~/.local/share/uv/tools/city-cli/bin/python -I tests/test_106_task_refs.py [brain path ...]
"""

import ast
import asyncio
import gc
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PATHS = sys.argv[1:] or [
    os.path.join(HERE, "..", "asst", "_shared", "agent.py"),
    os.path.join(HERE, "..", "asst", "_gp", "agent.py"),
]
SPAWN = {"create_task", "ensure_future"}
KEEP = {"_keep", "_keep_task"}


def _name(func: ast.AST) -> str:
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _held_next(stmt: ast.stmt, body: list, target: str) -> bool:
    i = body.index(stmt)
    if i + 1 >= len(body):
        return False
    nxt = body[i + 1]
    if (
        isinstance(nxt, ast.Assign)
        and isinstance(nxt.targets[0], ast.Subscript)
        and isinstance(nxt.value, ast.Name)
        and nxt.value.id == target
    ):
        return True  # X[k] = task: the container holds it
    call = nxt.value if isinstance(nxt, ast.Expr) else None
    return (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "add"
        and any(isinstance(a, ast.Name) and a.id == target for a in call.args)
    )


def check(path: str) -> list:
    tree = ast.parse(open(path, encoding="utf-8").read())
    parent = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node
    bad = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _name(node.func) in SPAWN):
            continue
        up = parent.get(node)
        if isinstance(up, ast.Call) and _name(up.func) in KEEP:
            continue
        if isinstance(up, ast.List):
            up = parent.get(up)
        if isinstance(up, ast.IfExp):
            up = parent.get(up)
        if isinstance(up, ast.Assign) and isinstance(up.targets[0], ast.Subscript):
            continue
        if isinstance(up, ast.Assign) and isinstance(up.targets[0], ast.Name):
            body = getattr(parent.get(up), "body", None)
            if body and up in body and _held_next(up, body, up.targets[0].id):
                continue
        bad.append(node.lineno)
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name == "_resume_offers":
            if not any(isinstance(n, ast.Call) and _name(n.func) in KEEP for n in ast.walk(fn)):
                bad.append(f"_resume_offers:{fn.lineno} (task popped from _RESUMING mid-run)")
    return bad


def live(path: str) -> None:
    os.environ.setdefault("CITY_ROLE", "booking_centre")
    spec = importlib.util.spec_from_file_location("brain106", path)
    brain = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(brain)
    keep = getattr(brain, "_keep", None) or getattr(brain, "_keep_task")
    done = []

    async def job() -> None:
        await asyncio.sleep(0.05)
        done.append(1)

    async def main() -> None:
        keep(asyncio.get_running_loop().create_task(job()))
        gc.collect()
        await asyncio.sleep(0.2)

    asyncio.run(main())
    assert done == [1], "kept task did not finish"
    assert not brain._TASK_REFS, "finished task still held"


fails = 0
for p in PATHS:
    bad = check(p)
    if bad:
        fails += 1
        print(f"FAIL {p}: unreferenced task at {bad}")
        continue
    try:
        live(p)
    except Exception as exc:
        fails += 1
        print(f"FAIL {p}: live check: {exc!r}")
        continue
    print(f"ok {p}")
print("FAILED" if fails else "ALL OK")
sys.exit(1 if fails else 0)
