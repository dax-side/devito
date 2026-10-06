"""ship.py: the one gate between local work and the platform.

Nothing is uploaded unless `ship.py check` printed SHIP and wrote bundle/STAMP.
Every rule here comes from a real platform fail (lessons.md, platform_patterns.md).

  ship.py init TASKDIR [--lang go|python]     task.json, trace.tsv, ledger.csv
  ship.py trace TASKDIR                       add missing test names to trace.tsv
  ship.py ev TASKDIR NAME -- CMD...           run CMD, save evidence/NAME.txt stamped with input md5s
  ship.py ev TASKDIR NAME --file F --verdict ok|fail   record an agent check (mock TQ, pre-SQ, predictor)
  ship.py check TASKDIR [--allow-shrink]      every gate; on pass writes bundle/ + STAMP
  ship.py log TASKDIR CHECK RESULT [--local agree|miss|false|none] [--cls RULE] [--note TEXT]
  ship.py board ROOT                          scoreboard over every task dir under ROOT

Evidence names and what they depend on are set in task.json. An evidence file is stale as soon
as any file it depends on changes, so a check cannot be skipped by forgetting it.
"""
import csv, datetime, hashlib, json, os, re, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DEFAULT = {
    "name": "",
    "lang": "go",
    "files": {"description": "description.txt", "solution": "solution.patch", "tests": "test.patch"},
    "word_budget": 385,
    "loc_floor": 200,
    "hex_dir": True,
    "upload_budget": {"TQ": 2, "SQ": 2},
    "evidence": {
        "proof": ["description", "solution", "tests"],
        "sweep": ["description", "solution"],
        "pretq": ["description", "tests"],
        "presq": ["description", "solution"],
    },
    "before_rollouts": {"predict": ["description", "tests"]},
}
CHECKS = ("TQ", "SQ", "DQ", "VERIFY", "FLAKY", "ROLLOUT", "FP", "AR", "SCOPE")


def md5(p):
    return hashlib.md5(open(p, 'rb').read()).hexdigest()


def load(d):
    cfg = dict(DEFAULT)
    cfg.update(json.load(open(os.path.join(d, 'task.json'))))
    return cfg


def fpath(d, cfg, key):
    return os.path.join(d, cfg['files'][key])


def added(patch):
    out, cur, new = [], None, set()
    prev = None
    for line in open(patch, encoding='utf-8', errors='replace').read().splitlines():
        if line.startswith('--- '):
            prev = line
            continue
        if line.startswith('+++ '):
            cur = line[6:] if line.startswith('+++ b/') else line[4:]
            if prev and prev.strip() == '--- /dev/null':
                new.add(cur)
            continue
        if line.startswith('+') and not line.startswith('+++'):
            out.append((cur, line[1:]))
    return out, new


def is_test_file(f):
    return f.endswith('_test.go') or bool(re.search(r'(^|/)test_[^/]*\.py$|_test\.py$', f))


def test_names(patch):
    lines, _ = added(patch)
    names = []
    for f, l in lines:
        if not is_test_file(f or ''):
            continue
        m = re.match(r'func (Test\w+)\(', l) or re.match(r'\s*def (test_\w+)\(', l)
        if m:
            names.append(m.group(1))
    return names


def cases(patch):
    lines, _ = added(patch)
    pat = r't\.Run\(|pytest\.param\(|^\s*\{\s*(name|desc)\s*:|^\s*\{"[^"]+",'
    return sum(1 for f, l in lines if is_test_file(f or '') and re.search(pat, l))


def norm(s):
    return re.sub(r'\s+', ' ', s).strip().lower()


def sentences(text):
    body = text.split('\n', 1)[1] if '\n' in text else text
    return [s.strip() for s in re.split(r'(?<=[.!?])\s+', body.replace('\n', ' ')) if len(s.split()) > 3]


# ---------------- gates ----------------

def gate_description(d, cfg, fails, warns):
    raw = open(fpath(d, cfg, 'description'), 'rb').read()
    text = raw.decode('utf-8', errors='replace')
    words = len(re.findall(r'\w+', text))
    if words > 400:
        fails.append(f'D words {words} > 400 (DQ fails)')
    elif words > cfg['word_budget']:
        warns.append(f'D words {words} > budget {cfg["word_budget"]} (no room for a DQ fix)')
    bad = sorted({c for c in text if ord(c) > 127})
    if bad:
        fails.append(f'D non-ASCII characters: {bad}')
    if b'\r' in raw:
        fails.append('D CRLF in description')
    if re.search(r'\b(test|tests|tested|testing)\b', text, re.I):
        fails.append('D says "test" (leakage, P1)')
    lines = text.splitlines()
    paras = [p.strip() for p in re.split(r'\n\s*\n', '\n'.join(lines[1:])) if p.strip()]
    for i, p in enumerate(paras, 1):
        if len(p.split()) > 150:
            fails.append(f'D paragraph {i} has {len(p.split())} words (> 150)')
        if len(p.splitlines()) > 1:
            fails.append(f'D paragraph {i} is hard-wrapped')
    for l in lines[1:]:
        if re.match(r'\s*([-*]|\d+[.)])\s', l) or l.lstrip().startswith('#'):
            fails.append(f'D list or heading line: {l[:60]}')
    for s in sentences(text):
        if re.search(r'\b(any|every|all|whichever|always|never)\b', s, re.I) and not re.search(r'\d', s):
            warns.append(f'D unbounded word, no number (SQ will push it): "{s[:90]}"')
        if re.search(r'\b(as before|unchanged|correctly|properly|any other use)\b', s, re.I):
            warns.append(f'D broad wording no assertion pins (T1): "{s[:90]}"')
        if len(s.split()) > 45:
            warns.append(f'D sentence over 45 words (overloaded, A2): "{s[:60]}..."')
    return words


def gate_tests(d, cfg, fails, warns):
    tp = fpath(d, cfg, 'tests')
    lines, new = added(tp)
    files = sorted({f for f, _ in lines if f})
    for f in files:
        if not is_test_file(f):
            continue
        if f not in new:
            fails.append(f'T {f} edits an existing test file (platform merges into agent copies, R1)')
        base = os.path.basename(f)
        if not re.search(r'[0-9a-f]{6}', base):
            fails.append(f'T {f}: file name needs a random hex (openssl rand -hex 3, P3)')
        if cfg.get('hex_dir') and not re.search(r'[0-9a-f]{6}', os.path.dirname(f)):
            fails.append(f'T {f}: directory needs a random hex')
    pats = {
        'comment in test file': r'^\s*(#(?!!)|//)',
        'asserts error text (G1)': r'\bmatch\s*=|\.Error\(\)\s*(==|!=)|Contains\(\s*err\.Error\(\)|str\(\s*exc',
        'private attribute': r"\.\_(?!_)[A-Za-z]\w*\b(?!\()|getattr\([^)]*,\s*f?['\"]_(?!_)",
        'tuple parametrize': r"parametrize\([^)]*,\s*\[\s*\(",
        'fixed row index (out[-2])': r'\[\s*-\d+\s*\]',
        'sleep / timing': r'time\.sleep\(|time\.Sleep\(',
    }
    for name, p in pats.items():
        hits = [(f, l.strip()) for f, l in lines if is_test_file(f or '') and re.search(p, l)]
        if hits:
            soft = ('comment in test file', 'fixed row index (out[-2])', 'sleep / timing')
            (warns if name in soft else fails).append(
                f'T {name}: {len(hits)} line(s), first: {hits[0][0]}: {hits[0][1][:80]}')
    if 'test.sh' not in {os.path.basename(f) for f in files}:
        warns.append('T test.sh not in the test patch')


def gate_trace(d, cfg, fails, warns):
    names = test_names(fpath(d, cfg, 'tests'))
    desc = norm(open(fpath(d, cfg, 'description'), encoding='utf-8', errors='replace').read())
    tr = os.path.join(d, 'trace.tsv')
    quotes = {}
    if os.path.exists(tr):
        for row in csv.reader(open(tr), delimiter='\t'):
            if row and not row[0].startswith('#'):
                quotes[row[0]] = row[1] if len(row) > 1 else ''
    for n in names:
        q = quotes.get(n, '')
        if not q.strip():
            fails.append(f'F {n}: no quote in trace.tsv (Rule A: every assertion quotes the sentence that states it)')
        elif norm(q) not in desc:
            fails.append(f'F {n}: quote not word for word in description: "{q[:70]}"')
    covered = [norm(q) for q in quotes.values() if q.strip()]
    text = open(fpath(d, cfg, 'description'), encoding='utf-8', errors='replace').read()
    for s in sentences(text):
        if not any(c and (c in norm(s) or norm(s) in c) for c in covered):
            warns.append(f'F sentence with no test quoting it (FP risk, or cut it): "{s[:80]}"')
    for n in quotes:
        if n not in names:
            warns.append(f'F trace.tsv names {n}, not in the test patch (stale row)')
    return names


def gate_solution(d, cfg, fails, warns):
    sp = fpath(d, cfg, 'solution')
    text = open(sp, encoding='utf-8', errors='replace').read()
    if cfg['lang'] == 'python':
        from effloc import effective
    else:
        from effloc_c import effective
    total = effective(text)[0]
    if total < cfg['loc_floor']:
        fails.append(f'S effective LOC {total} < floor {cfg["loc_floor"]}')
    lines, _ = added(sp)
    if any(is_test_file(f or '') for f, _ in lines):
        warns.append('S solution patch adds test files: they must be skipped in test.sh base mode (after_extras_not_skipped)')
    if re.search(r'\bfloat\(np\.real', text):
        warns.append('S float(np.real(...)) narrows values (A1)')
    return total


def deps_md5(d, cfg, keys):
    return {k: md5(fpath(d, cfg, k)) for k in keys}


def gate_evidence(d, cfg, fails, warns, table):
    for name, keys in table.items():
        p = os.path.join(d, 'evidence', name + '.txt')
        want = deps_md5(d, cfg, keys)
        if not os.path.exists(p):
            fails.append(f'E {name}: never run. ship.py ev {d} {name} -- <cmd>  (or --file for an agent check)')
            continue
        head = open(p, encoding='utf-8', errors='replace').read(2000)
        m = re.search(r'^INPUTS (.*)$', head, re.M)
        got = json.loads(m.group(1)) if m else {}
        stale = [k for k in keys if got.get(k) != want[k]]
        if stale:
            fails.append(f'E {name}: stale, {"/".join(stale)} changed since it ran. Rerun it.')
        elif not re.search(r'^VERDICT ok$', head, re.M):
            fails.append(f'E {name}: ran, verdict not ok. Fix, rerun.')


def ledger(d):
    p = os.path.join(d, 'ledger.csv')
    return list(csv.DictReader(open(p))) if os.path.exists(p) else []


def gate_budget(d, cfg, fails, warns):
    rows = ledger(d)
    for chk, cap in cfg.get('upload_budget', {}).items():
        n = sum(1 for r in rows if r['check'] == chk)
        if n >= cap:
            warns.append(f'B {chk}: {n} uploads already (budget {cap}). Before another: why did local gates miss it? Add that case to the sweep.')
        fl = [r for r in rows if r['check'] == chk and r['result'] == 'fail']
        if len(fl) >= 3:
            warns.append(f'B {chk} failed {len(fl)} times: G13 tripwire. Decide continue / cut / drop with the user.')
    seen = {}
    for r in rows:
        if r.get('cls'):
            seen[r['cls']] = seen.get(r['cls'], 0) + 1
    for c, n in seen.items():
        if n > 1:
            warns.append(f'B rule "{c}" found {n} times: fix it at the lowest layer (per hop / per route), then sweep every route.')


# ---------------- commands ----------------

def cmd_init(d, lang):
    os.makedirs(os.path.join(d, 'evidence'), exist_ok=True)
    cfg = dict(DEFAULT, name=os.path.basename(os.path.abspath(d)), lang=lang)
    tj = os.path.join(d, 'task.json')
    if not os.path.exists(tj):
        json.dump(cfg, open(tj, 'w'), indent=1)
    lp = os.path.join(d, 'ledger.csv')
    if not os.path.exists(lp):
        open(lp, 'w').write('date,task,check,result,local,cls,note\n')
    tr = os.path.join(d, 'trace.tsv')
    if not os.path.exists(tr):
        open(tr, 'w').write('# test name<TAB>exact words from the description that state what it asserts\n')
    print('init', d)


def cmd_trace(d):
    cfg = load(d)
    tr = os.path.join(d, 'trace.tsv')
    have = {r[0] for r in csv.reader(open(tr), delimiter='\t') if r} if os.path.exists(tr) else set()
    miss = [n for n in test_names(fpath(d, cfg, 'tests')) if n not in have]
    with open(tr, 'a') as f:
        for n in miss:
            f.write(f'{n}\t\n')
    print(f'added {len(miss)} names to trace.tsv; fill each quote')


def cmd_ev(d, name, argv):
    cfg = load(d)
    table = dict(cfg['evidence'])
    table.update(cfg.get('before_rollouts', {}))
    keys = table.get(name, ['description', 'solution', 'tests'])
    inputs = deps_md5(d, cfg, keys)
    if '--file' in argv:
        src = argv[argv.index('--file') + 1]
        verdict = argv[argv.index('--verdict') + 1] if '--verdict' in argv else 'fail'
        body, code = open(src, encoding='utf-8', errors='replace').read(), (0 if verdict == 'ok' else 1)
        what = f'file {src}'
    else:
        cmd = argv[argv.index('--') + 1:]
        r = subprocess.run(cmd, capture_output=True, text=True)
        body, code, what = r.stdout + r.stderr, r.returncode, ' '.join(cmd)
    os.makedirs(os.path.join(d, 'evidence'), exist_ok=True)
    with open(os.path.join(d, 'evidence', name + '.txt'), 'w') as f:
        f.write(f'INPUTS {json.dumps(inputs)}\nRAN {datetime.datetime.now().isoformat(timespec="seconds")} {what}\n')
        f.write(f'VERDICT {"ok" if code == 0 else "fail"}\n\n{body}')
    print(f'evidence {name}: {"ok" if code == 0 else "FAIL"} (exit {code})')
    sys.exit(code)


def cmd_check(d, allow_shrink, rollouts=False):
    cfg = load(d)
    fails, warns = [], []
    words = gate_description(d, cfg, fails, warns)
    gate_tests(d, cfg, fails, warns)
    names = gate_trace(d, cfg, fails, warns)
    loc = gate_solution(d, cfg, fails, warns)
    table = dict(cfg['evidence'])
    if rollouts:
        table.update(cfg.get('before_rollouts', {}))
    gate_evidence(d, cfg, fails, warns, table)
    gate_budget(d, cfg, fails, warns)
    n_cases = cases(fpath(d, cfg, 'tests'))
    stamp_p = os.path.join(d, 'bundle', 'STAMP')
    if os.path.exists(stamp_p):
        last = json.load(open(stamp_p))
        lost = sorted(set(last.get('names', [])) - set(names))
        if (lost or n_cases < last.get('cases', 0)) and not allow_shrink:
            fails.append(f'C tests shrank since last upload: lost {lost or "-"}, cases {last.get("cases")} -> {n_cases}. '
                         'Pass --allow-shrink only if this was on purpose.')
    for w in warns:
        print('warn ', w)
    for f in fails:
        print('FAIL ', f)
    print(f'-- words {words}/{cfg["word_budget"]}  loc {loc}  tests {len(names)}  cases {n_cases}')
    if fails:
        print(f'NO SHIP: {len(fails)} fail(s). Fix all of them in one edit, then rerun.')
        sys.exit(1)
    b = os.path.join(d, 'bundle')
    os.makedirs(b, exist_ok=True)
    sums = {}
    for k in ('description', 'solution', 'tests'):
        shutil.copy2(fpath(d, cfg, k), b)
        sums[cfg['files'][k]] = md5(fpath(d, cfg, k))
    open(os.path.join(b, 'MANIFEST.txt'), 'w').write(''.join(f'{v}  {k}\n' for k, v in sums.items()))
    json.dump({'time': datetime.datetime.now().isoformat(timespec='seconds'), 'md5': sums,
               'names': names, 'cases': n_cases, 'words': words, 'loc': loc,
               'warnings': len(warns)}, open(stamp_p, 'w'), indent=1)
    print('SHIP. Upload exactly these bytes:')
    for k, v in sums.items():
        print(f'  {v}  {k}')
    print('After the platform answers: ship.py log', d, '<CHECK> pass|fail --local agree|miss|false|none [--cls RULE]')


def cmd_log(d, argv):
    chk, res = argv[0].upper(), argv[1].lower()
    opt = lambda k: argv[argv.index(k) + 1] if k in argv else ''
    if chk not in CHECKS:
        sys.exit(f'check must be one of {CHECKS}')
    row = {'date': datetime.date.today().isoformat(), 'task': load(d).get('name', ''), 'check': chk, 'result': res,
           'local': opt('--local') or 'none', 'cls': opt('--cls'), 'note': opt('--note')}
    prior = [r for r in ledger(d) if row['cls'] and r['cls'] == row['cls']]
    with open(os.path.join(d, 'ledger.csv'), 'a', newline='') as f:
        csv.DictWriter(f, fieldnames=list(row)).writerow(row)
    print('logged', row)
    if row['local'] in ('none', 'miss') and res == 'fail':
        print('-> local gates missed this. Add one sweep row or checklist line that catches the whole class, '
              'then append it to calibration.md.')
    if prior:
        print(f'-> REPEAT of rule "{row["cls"]}" ({len(prior)} before). Narrow fixes are reward hacking: '
              'enforce it at the lowest layer and sweep every route before the next upload.')


def score(rows):
    s = 0
    for r in rows:
        if r['result'] == 'pass' and r['local'] == 'agree':
            s += 1
        if r['result'] == 'fail':
            s -= {'none': 3, 'miss': 2, 'false': 1}.get(r['local'], 1)
    seen = set()
    for r in rows:
        if r['cls'] and r['cls'] in seen:
            s -= 3
        seen.add(r['cls'])
    return s


def cmd_board(root):
    tasks = []
    for dp, _, fs in os.walk(root):
        if 'ledger.csv' in fs and 'task.json' in fs:
            tasks.append((dp, ledger(dp)))
    print(f'{"task":24} {"TQ":>3} {"SQ":>3} {"fails":>5} {"no-local":>8} {"repeats":>7} {"score":>5}  first..last')
    spans = []
    for dp, rows in sorted(tasks, key=lambda t: (t[1][0]['date'] if t[1] else '')):
        if not rows:
            continue
        up = lambda c: sum(1 for r in rows if r['check'] == c)
        fails = sum(1 for r in rows if r['result'] == 'fail')
        nol = sum(1 for r in rows if r['local'] == 'none')
        cl = [r['cls'] for r in rows if r['cls']]
        rep = len(cl) - len(set(cl))
        spans.append((rows[0]['date'], rows[-1]['date']))
        print(f'{os.path.basename(dp):24} {up("TQ"):>3} {up("SQ"):>3} {fails:>5} {nol:>8} {rep:>7} {score(rows):>5}  '
              f'{rows[0]["date"]}..{rows[-1]["date"]}')
    for (a0, a1), (b0, _) in zip(spans, spans[1:]):
        gap = (datetime.date.fromisoformat(b0) - datetime.date.fromisoformat(a1)).days
        if gap > 0:
            print(f'idle {gap} day(s) between tasks ending {a1} and starting {b0}: start the next scout while waiting')
    print('targets: TQ<=2 SQ<=2 uploads, no-local 0, repeats 0. score: +1 pass predicted, '
          '-3 fail with no local check, -2 missed, -3 repeated rule')


if __name__ == '__main__':
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    c = a[0]
    if c == 'init':
        cmd_init(a[1], a[a.index('--lang') + 1] if '--lang' in a else 'go')
    elif c == 'trace':
        cmd_trace(a[1])
    elif c == 'ev':
        cmd_ev(a[1], a[2], a[3:])
    elif c == 'check':
        cmd_check(a[1], '--allow-shrink' in a, '--rollouts' in a)
    elif c == 'log':
        cmd_log(a[1], a[2:])
    elif c == 'board':
        cmd_board(a[1])
    else:
        sys.exit(__doc__)
