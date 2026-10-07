#!/usr/bin/env python3
"""bfasm: compiles a small Forth-like stack language into a Befunge-98 grid.

Usage: python3 tools/bfasm.py json2dir.bfs > json2dir.b98

The generated program only uses core Funge-98 instructions. Layout:
  * every basic block is one row; it starts with '>' in column 0 and runs east;
  * a block ends with an absolute jump: push dx, push dy, 'x' (set delta), so the
    IP's next step lands exactly on column 0 of the target row, whose '>' resets
    the delta to east;
  * a conditional branch computes dy = flag ? A : B arithmetically;
  * `getc` uses '~' and its reflect-on-EOF behaviour plus one auxiliary row;
  * memory lives in funge space at negative rows: globals, a frame stack for
    locals (so functions can recurse), the return stack and one row per array.

Source language (whitespace separated words, `\\ comment` to end of line):
  var NAME                      global cell, starts at 0
  array NAME                    funge-space row, NAME@ ( i -- v )  NAME! ( v i -- )
  : NAME { a b | c d } ... ;    function; a b are popped from the stack, c d are locals
  NAME@ NAME!                   fetch / store a local or global
  123 -4 'c                     literals
  + - * / % dup drop swap not = <> < > <= >= and or emit getc exit
  if ... else ... then   begin ... while ... repeat   begin ... until
  return                        leave the current function
  ." text"                      print text (\\n is a newline)
  fail" text"                   print "json2dir: text" and quit with exit code 1
Any other word calls the function of that name. Execution starts at `main`.
"""
import re
import sys

VARROW, LOCROW, RSROW, ARRAY0 = -2, -3, -4, -10

PRIMS = {
    '+': '+', '-': '-', '*': '*', '/': '/', '%': '%',
    'dup': ':', 'drop': '$', 'swap': '\\', 'not': '!',
    '=': '-!', '<>': '-!!', '>': '`', '<': '\\`', '<=': '`!', '>=': '\\`!',
    'and': '!!\\!!*', 'or': '+!!', 'emit': ',', 'exit': 'q',
}

_small = {}


def push(n):
    """Befunge code that pushes the integer n."""
    if n < 0:
        return '0' + push(-n) + '-'
    if n < 10:
        return str(n)
    if n < 16:
        return 'abcdef'[n - 10]
    if 33 <= n <= 126:
        return "'" + chr(n)
    if n < 2048:
        if n in _small:
            return _small[n]
        best = None
        for a in range(2, int(n ** 0.5) + 1):
            if n % a == 0:
                c = push(a) + push(n // a) + '*'
                if best is None or len(c) < len(best):
                    best = c
        for r in range(1, 16):
            if n - r >= 16 and (best is None or len(best) > 4):
                c = push(n - r) + push(r) + '+'
                if best is None or len(c) < len(best):
                    best = c
        _small[n] = best
        return best
    q, r = divmod(n, 100)
    return push(q) + "'d*" + (push(r) + '+' if r else '')


def tokenize(text):
    toks, i, n = [], 0, len(text)
    while i < n:
        if text[i].isspace():
            i += 1
            continue
        j = i
        while j < n and not text[j].isspace():
            j += 1
        w = text[i:j]
        if w == '\\':
            while j < n and text[j] != '\n':
                j += 1
            i = j
            continue
        if w in ('."', 'fail"'):
            k, out = j + 1, []
            while text[k] != '"':
                if text[k] == '\\' and text[k + 1] in 'n"\\':
                    out.append({'n': '\n'}.get(text[k + 1], text[k + 1]))
                    k += 2
                else:
                    out.append(text[k])
                    k += 1
            toks.append((w, ''.join(out)))
            i = k + 1
            continue
        toks.append((w, None))
        i = j
    return toks


class Block:
    def __init__(self, label):
        self.label = label
        self.items = []      # strings or ('row', label)
        self.term = None     # ('jmp', L) ('br', Lt, Lf) ('ret',) ('halt',) ('getc', L)


class Compiler:
    def __init__(self):
        self.globals = {'FP': 0, 'RSP': 1}
        self.arrays = {}
        self.funcs = {}
        self.blocks = []
        self.nlab = 0
        self.cur = None

    def label(self, hint='L'):
        self.nlab += 1
        return '%s%d' % (hint, self.nlab)

    def start(self, lab):
        if self.cur is not None and self.cur.term is None:
            self.cur.term = ('jmp', lab)
        self.cur = Block(lab)
        self.blocks.append(self.cur)

    def emit(self, *items):
        self.cur.items.extend(items)

    def term(self, t):
        self.cur.term = t
        self.start(self.label('dead'))

    def gvar(self, k):
        return push(k) + push(VARROW) + 'g'

    def svar(self, k):
        return push(k) + push(VARROW) + 'p'

    def laddr(self, k):
        # FP points just past the current frame, which holds locals 0..fs-1
        k -= self.fs
        return self.gvar(0) + (push(k) + '+' if k else '') + push(LOCROW)

    def call(self, fname):
        cont = self.label('ret')
        self.emit(('row', cont), self.gvar(1) + '1+:' + self.svar(1) + push(RSROW) + 'p')
        self.cur.term = ('jmp', 'f_' + fname)
        self.start(cont)

    def compile(self, text):
        toks = tokenize(text)
        # pass 1: declarations
        i = 0
        while i < len(toks):
            w = toks[i][0]
            if w == 'var':
                self.globals[toks[i + 1][0]] = len(self.globals)
                i += 2
            elif w == 'array':
                self.arrays[toks[i + 1][0]] = ARRAY0 - len(self.arrays)
                i += 2
            elif w == ':':
                self.funcs[toks[i + 1][0]] = True
                i += 2
            else:
                i += 1
        # startup: zero the globals, call main, stop
        self.start('start')
        for k in self.globals.values():
            self.emit('0' + self.svar(k))
        self.call('main')
        self.emit('0q')
        self.cur.term = ('halt',)
        # pass 2: functions
        i = 0
        while i < len(toks):
            w = toks[i][0]
            if w in ('var', 'array'):
                i += 2
            elif w == ':':
                i = self.function(toks, i + 1)
            else:
                raise SystemExit('unexpected word at top level: ' + w)

    def function(self, toks, i):
        name = toks[i][0]
        i += 1
        args, locs = [], []
        if toks[i][0] == '{':
            i += 1
            target = args
            while toks[i][0] != '}':
                if toks[i][0] == '|':
                    target = locs
                else:
                    target.append(toks[i][0])
                i += 1
            i += 1
        local = {v: k for k, v in enumerate(args + locs)}
        fs = self.fs = len(local)
        exit_lab = self.label('exit_' + name)
        self.start('f_' + name)
        if fs:
            self.emit(self.gvar(0) + push(fs) + '+' + self.svar(0))
        for a in reversed(args):
            self.emit(self.laddr(local[a]) + 'p')
        ctrl = []
        while True:
            w, s = toks[i]
            i += 1
            if w == ';':
                break
            if re.fullmatch(r'-?\d+', w):
                self.emit(push(int(w)))
            elif len(w) == 2 and w[0] == "'":
                self.emit(push(ord(w[1])))
            elif w == '."':
                for ch in s:
                    self.emit(push(ord(ch)) + ',')
            elif w == 'fail"':
                for ch in 'json2dir: ' + s + '\n':
                    self.emit(push(ord(ch)) + ',')
                self.emit('1q')
            elif w in PRIMS:
                self.emit(PRIMS[w])
            elif w == 'getc':
                nxt = self.label('g')
                self.cur.term = ('getc', nxt)
                self.start(nxt)
            elif w == 'if':
                lt, lf = self.label('then'), self.label('else')
                self.cur.term = ('br', lt, lf)
                self.start(lt)
                ctrl.append(['if', lf])
            elif w == 'else':
                c = ctrl[-1]
                assert c[0] == 'if'
                lend = self.label('endif')
                self.cur.term = ('jmp', lend)
                self.start(c[1])
                ctrl[-1] = ['else', lend]
            elif w == 'then':
                c = ctrl.pop()
                assert c[0] in ('if', 'else')
                self.start(c[1])
            elif w == 'begin':
                lb = self.label('begin')
                self.start(lb)
                ctrl.append(['begin', lb])
            elif w == 'while':
                c = ctrl[-1]
                assert c[0] == 'begin'
                lbody, lexit = self.label('body'), self.label('wend')
                self.cur.term = ('br', lbody, lexit)
                self.start(lbody)
                ctrl[-1] = ['while', c[1], lexit]
            elif w == 'repeat':
                c = ctrl.pop()
                assert c[0] == 'while'
                self.cur.term = ('jmp', c[1])
                self.start(c[2])
            elif w == 'until':
                c = ctrl.pop()
                assert c[0] == 'begin'
                lexit = self.label('uend')
                self.cur.term = ('br', lexit, c[1])
                self.start(lexit)
            elif w == 'return':
                self.term(('jmp', exit_lab))
            elif w.endswith('@') or w.endswith('!'):
                v, op = w[:-1], w[-1]
                if v in local:
                    self.emit(self.laddr(local[v]) + ('g' if op == '@' else 'p'))
                elif v in self.globals:
                    k = self.globals[v]
                    self.emit(self.gvar(k) if op == '@' else self.svar(k))
                elif v in self.arrays:
                    self.emit(push(self.arrays[v]) + ('g' if op == '@' else 'p'))
                else:
                    raise SystemExit('unknown variable in %s: %s' % (name, w))
            elif w in self.funcs:
                self.call(w)
            else:
                raise SystemExit('unknown word in %s: %s' % (name, w))
        assert not ctrl, 'unbalanced control words in ' + name
        self.start(exit_lab)
        if fs:
            self.emit(self.gvar(0) + push(fs) + '-' + self.svar(0))
        self.cur.term = ('ret',)
        self.cur = None
        return i

    # ---- layout ----

    def layout(self):
        rows, r = {}, 0
        for b in self.blocks:
            rows[b.label] = r
            r += 2 if b.term[0] == 'getc' else 1
        grid = []

        def jump_tail(prefix_len, mid):
            after = '\\'
            # code: <mid: pushes dy> push(dx) swap 'x', with dx = -(column of 'x')
            for gl in range(1, 12):
                col = prefix_len + len(mid) + gl + len(after)
                c = push(-col)
                if len(c) <= gl:
                    return mid + c + 'z' * (gl - len(c)) + after + 'x'
            raise SystemExit('jump too far')

        for b in self.blocks:
            s = rows[b.label]
            body = '>' + ''.join(push(rows[it[1]]) if isinstance(it, tuple) else it
                                 for it in b.items)
            t = b.term
            if t[0] == 'jmp':
                grid.append(body + jump_tail(len(body), push(rows[t[1]] - s)))
            elif t[0] == 'br':
                a, f = rows[t[1]], rows[t[2]]
                mid = '!!' + push(a - f) + '*' + push(f - s) + '+'
                grid.append(body + jump_tail(len(body), mid))
            elif t[0] == 'ret':
                # pop the return row off the return stack
                body += self.gvar(1) + ':' + push(RSROW) + 'g\\1-' + self.svar(1)
                grid.append(body + jump_tail(len(body), push(s) + '-'))
            elif t[0] == 'halt':
                grid.append(body + '@')
            elif t[0] == 'getc':
                nxt = rows[t[1]]
                row = body + '#v~'
                vcol = len(row) - 2
                grid.append(row + jump_tail(len(row), push(nxt - s)))
                aux = ' ' * vcol + '>01-'
                grid.append(aux + jump_tail(len(aux), push(nxt - s - 1)))
        return '\n'.join(grid) + '\n'


def main():
    if len(sys.argv) != 2:
        raise SystemExit('usage: bfasm.py SOURCE.bfs > OUT.b98')
    with open(sys.argv[1], encoding='utf-8') as f:
        text = f.read()
    c = Compiler()
    c.compile(text)
    sys.stdout.buffer.write(c.layout().encode("ascii"))


if __name__ == '__main__':
    main()
