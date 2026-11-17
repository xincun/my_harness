#!/usr/bin/env python3
"""Symbol anchors for knowledge files: `path/to/file.ext#Symbol` instead of `file.ext:123`.

Line numbers drift with every edit; a symbol name survives until the symbol
itself is renamed or removed, and then kb_check reports the anchor as stale.
Anchors are resolved against the code graph (.codegraph/codegraph.db), with
the source text as a fallback: the graph misses some declarations (for
example Objective-C NS_ENUM types and methods declared in an @interface).

  kb_anchors.py --check            # list stale anchors and remaining line references
  kb_anchors.py --fix [--dry-run]  # rewrite line references in docs/knowledge/ to anchors

--fix handles `path:N`, `path:N-M`, `path:N/M`, a bare basename that is unique
in the repo (`orders.py:42`), and bare `:N` after a file named earlier on the
same line (a table row or a sentence). It prints what it could not resolve;
fix those by hand. Without a code graph only references that sit on a
declaration line (def / class / func / @interface …) can be resolved.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))
from _common import KNOWLEDGE, ROOT, iter_md  # noqa: E402

DB_PATH = ROOT / ".codegraph" / "codegraph.db"
SOURCE_EXT = r"(?:m|h|mm|swift|py|c|cc|cpp|hpp|inc|js|ts|tsx|go|rs|java|kt)"
# `Sources/Tools/X.m#executeToolNamed:arguments:completion:` (inside backticks)
ANCHOR_RX = re.compile(r"`([A-Za-z0-9_.\-+/]+\." + SOURCE_EXT + r")#([^`\s]+)`")
FILE_LINE_RX = re.compile(
    r"(?<![\w/+.-])((?:[\w.+-]+/)*[\w+-][\w.+-]*\." + SOURCE_EXT + r"):(\d+)((?:\s*[-–]\s*:?\d+|/:?\d+)*)")
BARE_LINE_RX = re.compile(r"(?<![\w:])(?<=[\s（(、，,；;])(:\d+)((?:\s*[-–]\s*:?\d+|/:?\d+)*)")
COARSE_SPAN = 150  # a class body this long says nothing about where the reference points


class Graph:
    def __init__(self) -> None:
        self.db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) if DB_PATH.exists() else None
        self._files: Optional[List[str]] = None

    def close(self) -> None:
        if self.db is not None:
            self.db.close()
            self.db = None

    def __enter__(self) -> "Graph":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def available(self) -> bool:
        return self.db is not None

    def file_edges(self) -> List[Tuple[str, str, str, int]]:
        if not self.db:
            return []
        rows = self.db.execute(
            "SELECT DISTINCT s.file_path, t.file_path, e.kind, COALESCE(e.line, s.start_line, 1) "
            "FROM edges e JOIN nodes s ON e.source = s.id JOIN nodes t ON e.target = t.id "
            "WHERE s.file_path != t.file_path"
        ).fetchall()
        return [(str(r[0]), str(r[1]), str(r[2]), int(r[3])) for r in rows]

    def files(self) -> List[str]:
        if self._files is None:
            self._files = [r[0] for r in self.db.execute("SELECT path FROM files")] if self.db else []
        return self._files

    def resolve_file(self, token: str) -> Optional[str]:
        if (ROOT / token).is_file():
            return token
        if not self.db:
            return None
        # "+Schema.m" is how a category file is often abbreviated.
        hits = [f for f in self.files()
                if f.endswith("/" + token) or (token.startswith("+") and f.endswith(token))]
        return hits[0] if len(hits) == 1 else None

    def symbol_at(self, path: str, line: int) -> Optional[str]:
        """The symbol a `path:line` reference points at, or None when unsure."""
        # 1. The line (or its neighbour, for off-by-one references) declares something.
        declared = declaration_near(path, line)
        if declared:
            return declared
        if not self.db:
            return None
        # 2. The innermost function / method / type that contains the line.
        rows = self.db.execute(
            "SELECT name, kind, start_line, end_line FROM nodes WHERE file_path = ? "
            "AND kind IN ('function', 'method', 'class', 'struct', 'protocol', 'interface', 'enum') "
            "AND start_line <= ? AND end_line >= ?", (path, line, line)).fetchall()
        if not rows:
            return None
        name, kind, start, end = min(rows, key=lambda r: r[3] - r[2])
        if kind in ("class", "struct", "protocol", "interface") and end - start > COARSE_SPAN:
            return None  # "somewhere in a 2,000-line @implementation" is not an anchor
        return name

    def has_symbol(self, path: str, symbol: str) -> bool:
        stem, dot, ext = path.rpartition(".")
        paths = [path] + ([f"{stem}.h", f"{stem}.m", f"{stem}.mm"] if ext in ("h", "m", "mm") else [])
        if not self.db:
            return any(declared_in_text(p, symbol) for p in paths)
        marks = ",".join("?" * len(paths))
        row = self.db.execute(
            f"SELECT 1 FROM nodes WHERE file_path IN ({marks}) AND (name = ? OR qualified_name = ? "
            "OR qualified_name LIKE ? OR qualified_name LIKE ?) LIMIT 1",
            (*paths, symbol, symbol, "%::" + symbol, "%." + symbol)).fetchone()
        return row is not None or any(declared_in_text(p, symbol) for p in paths)


# ------------------------------------------------- source-text fallback
# Declaration forms only. A statement inside a body never names the anchor.
DECL_RXS = [
    re.compile(r"^\s*@(?:interface|protocol|implementation)\s+(\w+)"),
    re.compile(r"NS_(?:ENUM|OPTIONS|ERROR_ENUM|CLOSED_ENUM)\s*\(\s*\w+\s*,\s*(\w+)\s*\)"),
    re.compile(r"^\s*#define\s+(\w+)"),
    re.compile(r"^\s*(?:FOUNDATION_EXPORT|extern)\b[^;(]*?\b(\w+)\s*;"),
    re.compile(r"^\s*typedef\b.*?\(\^\s*(\w+)\)"),  # typedef void (^Name)(...)
    re.compile(r"^\s*(?:public\s+|private\s+|static\s+|final\s+)*(?:class|struct|enum|protocol|func|def)\s+(\w+)"),
]
OBJC_METHOD_RX = re.compile(r"^\s*[-+]\s*\([^)]*\)\s*\w+")


def _source_lines(path: str) -> List[str]:
    try:
        return (ROOT / path).read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:
        return []


def _objc_selector(lines: List[str], index: int) -> Optional[str]:
    text = ""
    for line in lines[index:index + 12]:
        text += " " + line
        if ";" in line or "{" in line:
            break
    text = re.sub(r"^\s*[-+]\s*\([^)]*\)\s*", "", text.split(";")[0].split("{")[0])
    parts = re.findall(r"(\w+)\s*:\s*\(", text)
    if parts:
        return "".join(p + ":" for p in parts)
    m = re.match(r"\s*(\w+)", text)
    return m.group(1) if m else None


def declaration_near(path: str, line: int) -> Optional[str]:
    """The name declared on (or right around) a 1-based source line."""
    lines = _source_lines(path)
    for index in (line - 1, line):  # the line itself, or the next one (off-by-one)
        if not 0 <= index < len(lines):
            continue
        text = lines[index]
        if OBJC_METHOD_RX.match(text):
            return _objc_selector(lines, index)
        for rx in DECL_RXS:
            m = rx.search(text)
            if m:
                return m.group(1)
    return None


def declared_in_text(path: str, symbol: str) -> bool:
    text = "\n".join(_source_lines(path))
    if not text:
        return False
    if ":" in symbol:  # Objective-C selector: every keyword present
        return all(re.search(r"\b" + re.escape(part) + r"\s*:", text) for part in symbol.split(":") if part)
    return re.search(r"\b" + re.escape(symbol) + r"\b", text) is not None


def knowledge_files() -> Iterable[Path]:
    for md in iter_md([KNOWLEDGE]):
        if "generated" not in md.parts and md.name not in ("README.md", "INDEX.md"):
            yield md


def without_fences(text: str) -> str:
    return re.sub(r"```.*?```", "", re.sub(r"<!--.*?-->", "", text, flags=re.S), flags=re.S)


def stale_anchors(graph: Graph, text: str) -> List[str]:
    out = []
    for path, symbol in ANCHOR_RX.findall(without_fences(text)):
        if (ROOT / path).exists() and not graph.has_symbol(path, symbol):
            out.append(f"{path}#{symbol}")
    return out


def line_references(text: str) -> List[str]:
    return [m.group(0) for m in FILE_LINE_RX.finditer(without_fences(text))]


def fix_line(graph: Graph, line: str, unresolved: List[str]) -> str:
    current: Dict[str, Optional[str]] = {"file": None}
    out, pos = [], 0
    # Earlier file mentions on the line (a table's first column, "in `X.m`") give bare `:N` its file.
    mentions = [(m.start(), graph.resolve_file(m.group(1)))
                for m in re.finditer(r"([\w.+/-]+\." + SOURCE_EXT + r")\b", line)]

    def file_before(index: int) -> Optional[str]:
        # Headers are excluded: "`Foo.h` … （:266）" usually means a range end or the .m.
        known = [f for start, f in mentions if start < index and f]
        return known[-1] if known and not known[-1].endswith((".h", ".hpp")) else None

    tokens = sorted([(m.start(), m.end(), "file", m) for m in FILE_LINE_RX.finditer(line)] +
                    [(m.start(), m.end(), "bare", m) for m in BARE_LINE_RX.finditer(line)])
    taken_until = -1
    for start, end, kind, m in tokens:
        if start < taken_until:
            continue
        if kind == "file":
            path = graph.resolve_file(m.group(1))
            symbol = graph.symbol_at(path, int(m.group(2))) if path else None
            if not symbol:
                unresolved.append(m.group(0))
                continue
            inside = line[:start].count("`") % 2 == 1
            replacement = f"{path}#{symbol}" if inside else f"`{path}#{symbol}`"
        else:
            path = file_before(start)
            symbol = graph.symbol_at(path, int(m.group(1)[1:])) if path else None
            if not symbol:
                unresolved.append(f"{m.group(0).strip()}（{path or '未知文件'}）")
                continue
            replacement = f"`{symbol}`" if line[:start].count("`") % 2 == 0 else symbol
        out.append(line[pos:start])
        out.append(replacement)
        pos = taken_until = end
    out.append(line[pos:])
    fixed = "".join(out)
    # `X.m#Sym` `Sym` → `X.m#Sym`
    return re.sub(r"(`[^`\s]+#([^`\s]+)`)\s*`\2`", r"\1", fixed)


def fix_file(graph: Graph, md: Path, dry_run: bool) -> Tuple[int, List[str]]:
    text = md.read_text(encoding="utf-8")
    unresolved: List[str] = []
    lines, in_fence, changed = [], False, 0
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        new = line if in_fence or line.lstrip().startswith("<!--") else fix_line(graph, line, unresolved)
        changed += new != line
        lines.append(new)
    if changed and not dry_run:
        md.write_text("\n".join(lines), encoding="utf-8")
    return changed, unresolved


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="列出失效锚点和残留的行号引用")
    ap.add_argument("--fix", action="store_true", help="把 docs/knowledge/ 中的行号引用改写成符号锚点")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    with Graph() as graph:
        if not graph.available:
            print("[kb_anchors] 没有代码图谱（.codegraph/codegraph.db）：只按源码文本校验和改写声明行；"
                  "完整功能先运行 codegraph.py init")
        status = 0
        for md in knowledge_files():
            rel = md.relative_to(ROOT)
            if args.fix:
                changed, unresolved = fix_file(graph, md, args.dry_run)
                if changed or unresolved:
                    print(f"{rel}: 改写 {changed} 行" + ("（试运行）" if args.dry_run else ""))
                for u in unresolved:
                    print(f"    无法解析：{u}")
            else:
                text = md.read_text(encoding="utf-8")
                for a in stale_anchors(graph, text):
                    print(f"{rel}: 符号锚点失效 → {a}")
                    status = 1
                refs = line_references(text)
                if refs:
                    print(f"{rel}: {len(refs)} 处行号引用，例如 {refs[0]}")
    return status


if __name__ == "__main__":
    sys.exit(main())
