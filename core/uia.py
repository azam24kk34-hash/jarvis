"""Натискання кнопок у будь-якій програмі Windows (UI Automation) — як у браузері, але для звичайних програм:
«Джарвіс, натисни в OBS «Почати трансляцію»». Знаходить кнопку за підписом, а не за координатами."""
import difflib
import re

CLICKABLE = ("ButtonControl", "MenuItemControl", "HyperlinkControl", "ListItemControl", "TabItemControl",
             "CheckBoxControl", "RadioButtonControl", "SplitButtonControl", "TreeItemControl", "ComboBoxControl")


def _root(hwnd):
    import uiautomation as auto
    return auto.ControlFromHandle(hwnd)


def _walk(ctrl, depth=0, max_depth=12, limit=400, out=None, budget=None):
    """Обхід дерева елементів з обмеженнями: не більше 2500 вузлів і 4 секунд —
    у великих програмах (браузери, IDE) повний обхід може вантажити процесор хвилинами."""
    import time
    out = [] if out is None else out
    budget = budget if budget is not None else {"nodes": 2500, "until": time.time() + 4}
    if depth > max_depth or len(out) >= limit or budget["nodes"] <= 0 or time.time() > budget["until"]:
        return out
    try:
        children = ctrl.GetChildren()
    except Exception:
        return out
    for ch in children:
        budget["nodes"] -= 1
        if budget["nodes"] <= 0:
            break
        try:
            if ch.ControlTypeName in CLICKABLE and (ch.Name or "").strip() and not ch.IsOffscreen:
                out.append(ch)
        except Exception:
            pass
        _walk(ch, depth + 1, max_depth, limit, out, budget)
    return out


def list_controls(hwnd, limit=80):
    items = _walk(_root(hwnd))
    seen, lines = set(), []
    for c in items:
        key = (c.ControlTypeName, c.Name)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"{c.ControlTypeName.replace('Control', '')}: «{c.Name.strip()[:60]}»")
        if len(lines) >= limit:
            break
    return lines


def click(hwnd, name):
    """Натиснути елемент з підписом name (точний збіг → входження → схожий). → назва натиснутого або None."""
    items = _walk(_root(hwnd))
    if not items:
        return None
    q = name.lower().strip()
    norm = lambda s: re.sub(r"\s+", " ", (s or "").lower().replace("&", "")).strip()
    hit = next((c for c in items if norm(c.Name) == q), None) or \
        next((c for c in items if q in norm(c.Name)), None)
    if not hit:
        names = [norm(c.Name) for c in items]
        m = difflib.get_close_matches(q, names, n=1, cutoff=0.6)
        hit = items[names.index(m[0])] if m else None
    if not hit:
        return None
    for pattern in ("GetInvokePattern", "GetTogglePattern", "GetSelectionItemPattern", "GetExpandCollapsePattern"):
        try:
            p = getattr(hit, pattern)()
            if p:
                {"GetInvokePattern": lambda: p.Invoke(), "GetTogglePattern": lambda: p.Toggle(),
                 "GetSelectionItemPattern": lambda: p.Select(), "GetExpandCollapsePattern": lambda: p.Expand()}[pattern]()
                return hit.Name
        except Exception:
            continue
    hit.Click(simulateMove=False)
    return hit.Name
