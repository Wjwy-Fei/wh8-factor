#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麦语言(WH8)信号公式瘦身工具。

把形如 AJ_00018:=A && B && C && ...; 的批量信号定义中反复出现的
条件片段提取成独立的公共定义(AJ_<PREFIX>1, AJ_<PREFIX>2, ...),
并对结果做逐项按原顺序的展开还原校验,确保逻辑与原文件完全等价。

用法:
    python3 factor_wh8.py <输入.txt> [-o 输出.txt] [-p 前缀字母] \
        [--min-count N] [--min-save N] [--reuse-from 已精简文件.txt ...] [--or-format] [--merge-dup]

默认输出: 输入文件名 + "_精简.txt",与输入同目录。
默认前缀: A (输出别名形如 AJ_A1, AJ_A2, ...)。

--or-format: 信号行不再输出成 "NAME:=body;" 定义式,而是 "||    body    //NAME",
用于直接粘贴进 WH8 里拼成一条 OR 合并的大条件(公共定义部分仍然是普通的
"AJ_A1:=...;" 定义式,不受此选项影响)。

--reuse-from: 传入一个或多个已经瘦身过的文件(如 A文件_精简.txt),可重复传参
复用多个文件。本次提取出的公共定义,只要展开到最底层条件项后与这些文件里
某个已有定义完全相同,就不再重新定义,直接复用那个文件里的别名。用于多个
文件分开瘦身、但存在相同重复定义的场景 —— 避免精简文件里出现内容相同、
名字却不同的定义。使用该文件时,WH8 里必须把 --reuse-from 指向的文件都
粘贴在本文件之前。

--merge-dup: 输入文件里如果出现同名但内容不同的信号行(通常是设计时同一个背景
写了几个不同条件分支,却忘了把编号改掉),默认会直接报错退出并列出这些重名,
避免静默用最后一条覆盖前面几条、导致条件丢失。加 --merge-dup 后会自动处理:
取这些同名行共同的前缀条件,把各自不同的尾部条件用 (X || Y || ...) 合并,
输出成一条信号。同名但内容完全相同的行始终会静默去重,不受此选项影响。
"""
import argparse
import collections
import re
import sys

# 已知语义片段 -> 固定别名(跨文件保持同一个名字,便于识别)
KNOWN_ALIASES = {
    '412DP=0 && 412S': 'AJ_412',
}

# 只有名字形如 <字母前缀>_<纯数字> 的行才当作"待瘦身的信号行"参与分析
# (不同信号族前缀可以是 AJ_/BJ_ 等,不局限于 AJ)。
# 其余行(如 AJ_T1 这种被引用但不参与瘦身的基础模板行,后缀非纯数字)原样保留在文件最前面。
SIGNAL_NAME_RE = re.compile(r'^[A-Za-z]+_\d+$')
DEF_LINE_RE = re.compile(r'^(\w+):=(.*);$')
REF_INNER_RE = re.compile(r'^REF\((.+ && .+),([A-Za-z0-9+\-]+)\)(=0)?$')


def split_terms(expr):
    """按括号深度为 0 的 && 拆分成条件项列表。"""
    terms, depth, cur, i = [], 0, '', 0
    while i < len(expr):
        c = expr[i]
        if c == '(':
            depth += 1
        if c == ')':
            depth -= 1
        if depth == 0 and expr[i:i + 2] == '&&':
            terms.append(cur.strip())
            cur = ''
            i += 2
            continue
        cur += c
        i += 1
    terms.append(cur.strip())
    return terms


def load(path, merge_dup=False):
    preamble, lines, order = [], {}, []
    bodies = {}   # 信号名 -> [body, ...],用于发现同名重复
    text = open(path, encoding='utf-8-sig').read()
    for raw in text.splitlines():
        l = raw.strip()
        if not l:
            continue
        m = DEF_LINE_RE.match(l)
        if not m:
            print('警告: 无法解析该行,原样忽略: %s' % l[:80], file=sys.stderr)
            continue
        name, body = m.group(1), m.group(2)
        if SIGNAL_NAME_RE.match(name):
            if name not in bodies:
                bodies[name] = []
                order.append(name)
            bodies[name].append(body)
        else:
            preamble.append(l)

    # 同名但内容不同的重复信号行(常见于"同一背景下写了几个不同条件分支"却忘了改编号):
    # 内容完全相同的重复直接去重;内容不同的必须显式处理,不能静默用最后一条覆盖前面的,
    # 否则前面几条设计的条件会无声丢失。
    conflicts = {n: bs for n, bs in bodies.items() if len(set(bs)) > 1}
    if conflicts:
        if not merge_dup:
            print('错误: 发现 %d 个信号名重复且内容不同(可能是不同背景分支忘了改编号):' % len(conflicts),
                  file=sys.stderr)
            for n, bs in conflicts.items():
                print('  %s 出现 %d 次,内容不同' % (n, len(bs)), file=sys.stderr)
            print('如果这些是同一信号下应该合并的不同条件分支,加 --merge-dup 自动用 || 合并为一条;'
                  '否则请先修正源文件里的信号名。', file=sys.stderr)
            sys.exit(2)
        for n, bs in conflicts.items():
            term_lists = [split_terms(b) for b in bs]
            minlen = min(len(t) for t in term_lists)
            lcp = 0
            while lcp < minlen and all(t[lcp] == term_lists[0][lcp] for t in term_lists):
                lcp += 1
            common = term_lists[0][:lcp]
            alts = []
            for t in term_lists:
                rest = t[lcp:]
                if not rest:
                    print('错误: %s 的某个分支是另一分支的前缀子集,无法安全合并为 ||,请手动检查该信号'
                          % n, file=sys.stderr)
                    sys.exit(2)
                alts.append(rest[0] if len(rest) == 1 else '(' + ' && '.join(rest) + ')')
            merged = ' && '.join(common + ['(' + ' || '.join(alts) + ')'])
            bodies[n] = [merged]
            print('合并重复信号名 %s: %d 个分支 -> 公共前缀 %d 项 + OR 分支' % (n, len(bs), lcp),
                  file=sys.stderr)

    for name in order:
        lines[name] = split_terms(bodies[name][0])
    return preamble, lines, order


def factor(lines, order, prefix, min_count, min_save):
    orig = {n: list(ts) for n, ts in lines.items()}
    defs = []            # [(别名, 定义体字符串)]
    expand_map = {}       # 别名 -> 该别名"下一层"的条件项列表(递归展开时用)
    counter = [0]

    def next_alias():
        counter[0] += 1
        return 'AJ_%s%d' % (prefix, counter[0])

    # 第一步: REF(复合条件, X) 中的复合条件单独提取
    inner_cnt = collections.Counter()
    for ts in lines.values():
        for t in ts:
            m = REF_INNER_RE.match(t)
            if m:
                inner_cnt[m.group(1)] += 1
    inner_alias = {}
    for inner, c in inner_cnt.items():
        if c >= 5:
            a = KNOWN_ALIASES.get(inner) or next_alias()
            inner_alias[inner] = a
            defs.append((a, inner))
            expand_map[a] = split_terms(inner)

    def rewrite_ref(t):
        m = REF_INNER_RE.match(t)
        if m and m.group(1) in inner_alias:
            return 'REF(%s,%s)%s' % (inner_alias[m.group(1)], m.group(2), m.group(3) or '')
        return t

    for n in order:
        lines[n] = [rewrite_ref(t) for t in lines[n]]

    # 第二步: 贪心提取高频连续条件段(块可以嵌套引用之前提取的块)
    while True:
        grams = collections.Counter()
        for ts in lines.values():
            length = len(ts)
            for i in range(length):
                for k in range(2, min(9, length - i) + 1):
                    grams[tuple(ts[i:i + k])] += 1
        best, best_save = None, 0
        for g, c in grams.items():
            if c < min_count:
                continue
            body = ' && '.join(g)
            save = c * (len(body) - 6) - len(body)
            if save > best_save:
                best, best_save = g, save
        if best is None or best_save < min_save:
            break
        a = next_alias()
        defs.append((a, ' && '.join(best)))
        expand_map[a] = list(best)
        k = len(best)
        for n in order:
            ts, out, i = lines[n], [], 0
            while i < len(ts):
                if tuple(ts[i:i + k]) == best:
                    out.append(a)
                    i += k
                else:
                    out.append(ts[i])
                    i += 1
            lines[n] = out

    # 第三步: 高频出现的长单项(含正负极性配对)也提取
    single = collections.Counter()
    for ts in lines.values():
        for t in ts:
            base = t[:-2] if t.endswith('=0') else t
            single[base] += 1
    single_alias = {}
    for base, c in single.most_common():
        if len(base) >= 14 and c >= 30 and base not in expand_map and not base.startswith('AJ_'):
            a = next_alias()
            single_alias[base] = a
            defs.append((a, base))
            expand_map[a] = [base]
    for n in order:
        out = []
        for t in lines[n]:
            base, neg = (t[:-2], '=0') if t.endswith('=0') else (t, '')
            out.append((single_alias[base] + neg) if base in single_alias else t)
        lines[n] = out

    return defs, expand_map, orig


def load_reuse_map(path):
    """解析一个已精简文件,取出其中的公共定义(非信号行),构建 别名->下一层条件项 的表。"""
    preamble, _, _ = load(path)
    reuse_map = {}
    for l in preamble:
        m = DEF_LINE_RE.match(l)
        if m:
            reuse_map[m.group(1)] = split_terms(m.group(2))
    return reuse_map


def apply_reuse(defs, expand_map, lines, order, reuse_map):
    """把 defs 中与 reuse_map 里已有定义(展开到叶子后)完全相同的,改为直接引用 reuse_map 的别名。"""
    renamed = {}
    for a, _ in defs:
        leaf = tuple(expand_term(a, expand_map))
        hit = None
        for other, other_terms in reuse_map.items():
            if tuple(expand_term(other, reuse_map)) == leaf:
                hit = other
                break
        if hit and hit != a:
            renamed[a] = hit

    if not renamed:
        return defs, expand_map, 0

    pattern = re.compile(r'\b(%s)\b' % '|'.join(re.escape(k) for k in renamed))

    def sub(s):
        return pattern.sub(lambda m: renamed[m.group(1)], s)

    new_defs = [(a, sub(b)) for a, b in defs if a not in renamed]
    for n in order:
        lines[n] = [sub(t) for t in lines[n]]
    new_expand_map = {a: [sub(t) for t in ts] for a, ts in expand_map.items() if a not in renamed}
    return new_defs, new_expand_map, renamed


def expand_term(t, expand_map):
    m = re.match(r'^REF\((AJ_\w+),([A-Za-z0-9+\-]+)\)(=0)?$', t)
    if m and m.group(1) in expand_map:
        inner = ' && '.join(sum((expand_term(x, expand_map) for x in expand_map[m.group(1)]), []))
        return ['REF(%s,%s)%s' % (inner, m.group(2), m.group(3) or '')]
    base, neg = (t[:-2], '=0') if t.endswith('=0') else (t, '')
    if base in expand_map:
        exp = sum((expand_term(x, expand_map) for x in expand_map[base]), [])
        if neg:
            assert len(exp) == 1, '极性取反的别名必须只展开成单个条件项: %s' % base
            return [exp[0] + neg]
        return exp
    return [t]


def verify(order, lines, orig, expand_map):
    bad = []
    for n in order:
        restored = sum((expand_term(t, expand_map) for t in lines[n]), [])
        if restored != orig[n]:
            bad.append(n)
    return bad


# ---- --or-group: 用分配律把共享公共前缀的多条信号折叠成一条嵌套 OR 表达式 ----
# (A && B && C) || (A && B && D) == A && B && (C || D),对纯 && / || 布尔组合恒成立。
# 只在"组内全部成员在当前位置的条件项完全相同"时才把该项当作公共前缀合并,
# 因此每个叶子的条件项顺序和原始展开结果完全一致,不引入重排风险。

def build_group(entries, depth=0):
    """entries: [(terms_tuple, name), ...],要求都共享 terms[:depth] 相同前缀。"""
    if len(entries) == 1:
        terms, name = entries[0]
        return ('leaf', tuple(terms[depth:]), name)

    ext = 0
    while True:
        pos = depth + ext
        val, ok = None, True
        for terms, _ in entries:
            if pos >= len(terms):
                ok = False
                break
            if val is None:
                val = terms[pos]
            elif terms[pos] != val:
                ok = False
                break
        if not ok:
            break
        ext += 1

    if ext > 0:
        prefix = tuple(entries[0][0][depth:depth + ext])
        return ('prefix', prefix, build_group(entries, depth + ext))

    groups, order_keys = {}, []
    for terms, name in entries:
        key = terms[depth] if depth < len(terms) else None
        if key not in groups:
            groups[key] = []
            order_keys.append(key)
        groups[key].append((terms, name))
    children = []
    for k in order_keys:
        grp = groups[k]
        if k is None:
            # 该分组内条件项已在此深度耗尽(极罕见:多个信号条件项完全相同),
            # 没有更多内容可比较,各自作为独立叶子,不再递归。
            children.extend(('leaf', tuple(terms[depth:]), name) for terms, name in grp)
        else:
            children.append(build_group(grp, depth))
    return ('branch', children)


def render_group(node):
    """返回 [[code_str, name], ...] 列表,每个元素是一条物理输出行的(代码部分, 信号名)。"""
    kind = node[0]
    if kind == 'leaf':
        terms, name = node[1], node[2]
        return [[' && '.join(terms), name]]
    if kind == 'prefix':
        prefix_terms, inner = node[1], node[2]
        lns = render_group(inner)
        prefix_str = ' && '.join(prefix_terms)
        lns[0][0] = '%s && %s' % (prefix_str, lns[0][0]) if lns[0][0] else prefix_str
        return lns
    # branch
    all_lines = []
    for child in node[1]:
        all_lines.extend(render_group(child))
    all_lines[0][0] = '(' + all_lines[0][0]
    all_lines[-1][0] = all_lines[-1][0] + ')'
    return all_lines


def render_group_top(node):
    """顶层渲染:不给最外层加多余的包裹括号。"""
    if node[0] == 'branch':
        all_lines = []
        for child in node[1]:
            all_lines.extend(render_group(child))
        return all_lines
    if node[0] == 'prefix':
        prefix_terms, inner = node[1], node[2]
        lns = render_group_top(inner)
        prefix_str = ' && '.join(prefix_terms)
        lns[0][0] = '%s && %s' % (prefix_str, lns[0][0]) if lns[0][0] else prefix_str
        return lns
    return render_group(node)


def verify_group(node, prefix_acc, lines_map, seen):
    kind = node[0]
    if kind == 'leaf':
        terms, name = node[1], node[2]
        seen.add(name)
        if list(prefix_acc + terms) != lines_map[name]:
            return [name]
        return []
    if kind == 'prefix':
        return verify_group(node[2], prefix_acc + node[1], lines_map, seen)
    bad = []
    for child in node[1]:
        bad += verify_group(child, prefix_acc, lines_map, seen)
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('input')
    ap.add_argument('-o', '--out', default=None)
    ap.add_argument('-p', '--prefix', default='A', help='别名前缀字母,默认 A (生成 AJ_A1, AJ_A2 ...)')
    ap.add_argument('--min-count', type=int, default=4, help='片段至少重复几次才提取,默认 4')
    ap.add_argument('--min-save', type=int, default=80, help='提取净节省字符数门槛,默认 80')
    ap.add_argument('--reuse-from', action='append', default=[],
                     help='复用另一个已精简文件里的公共定义,内容相同则不重复定义;可重复传参复用多个文件')
    ap.add_argument('--or-format', action='store_true',
                     help='信号行改用 "||    body    //NAME" 输出,用于直接拼进 WH8 的 OR 合并条件里,'
                          '而不是 "NAME:=body;" 定义式')
    ap.add_argument('--or-group', action='store_true',
                     help='在 --or-format 基础上,用分配律把共享公共前缀的多条信号折叠成嵌套 OR '
                          '(A&&B&&C || A&&B&&D 合并成 A&&B&&(C||D)),隐含开启 --or-format')
    ap.add_argument('--merge-dup', action='store_true',
                     help='输入文件里同名但内容不同的信号行(如同一背景写了几个条件分支忘了改编号),'
                          '自动提取公共前缀、把各分支用 || 合并成一条信号,而不是报错退出')
    args = ap.parse_args()
    if args.or_group:
        args.or_format = True

    out = args.out or re.sub(r'\.txt$', '_精简.txt', args.input)
    if out == args.input:
        out = args.input + '.精简.txt'

    preamble, lines, order = load(args.input, merge_dup=args.merge_dup)
    if not order:
        print('未找到任何 <前缀>_<数字> 形式的信号行,请检查输入文件格式。', file=sys.stderr)
        sys.exit(1)

    defs, expand_map, orig = factor(lines, order, args.prefix, args.min_count, args.min_save)

    reuse_map, renamed = {}, {}
    if args.reuse_from:
        for path in args.reuse_from:
            reuse_map.update(load_reuse_map(path))
        defs, expand_map, renamed = apply_reuse(defs, expand_map, lines, order, reuse_map)

    verify_map = dict(reuse_map)
    verify_map.update(expand_map)
    bad = verify(order, lines, orig, verify_map)

    group_bad = []
    if args.or_group:
        root = build_group([(tuple(lines[n]), n) for n in order])
        seen = set()
        group_bad = verify_group(root, (), lines, seen)
        if seen != set(order):
            group_bad.append('__覆盖不全__:期望 %d 条,实际覆盖 %d 条' % (len(order), len(seen)))
        signal_lines = render_group_top(root)
    else:
        signal_lines = None

    out_lines = []
    if preamble:
        out_lines += preamble + ['']
    for a, b in defs:
        out_lines.append('%s:=%s;' % (a, b))
    out_lines.append('')
    if args.or_group:
        for code, name in signal_lines:
            out_lines.append('||    %s    //%s' % (code, name))
    else:
        for n in order:
            body = ' && '.join(lines[n])
            if args.or_format:
                out_lines.append('||    %s    //%s' % (body, n))
            else:
                out_lines.append('%s:=%s;' % (n, body))

    orig_chars = sum(len(preamble_l) for preamble_l in preamble)
    orig_chars += sum(len(n) + 2 + len(' && '.join(orig[n])) + 1 for n in order)
    new_chars = sum(len(l) for l in out_lines)

    print('信号行数: %d' % len(order))
    print('提取公共定义: %d 个 (前缀 AJ_%s)' % (len(defs), args.prefix))
    if renamed:
        print('复用 %s 中已有定义: %d 个 -> %s' % (', '.join(args.reuse_from), len(renamed), sorted(set(renamed.values()))))
    if bad:
        print('!! 校验失败: 以下 %d 条展开还原后与原文不一致,已终止写出,请检查脚本或原文件格式: %s'
              % (len(bad), bad[:10]), file=sys.stderr)
        sys.exit(2)
    if group_bad:
        print('!! --or-group 校验失败: 以下条目还原后与原文不一致或未被覆盖,已终止写出: %s'
              % group_bad[:10], file=sys.stderr)
        sys.exit(2)
    print('校验通过: %d/%d 条逐项按原顺序展开还原后与原文完全一致' % (len(order), len(order)))
    if args.or_group:
        print('--or-group 校验通过: %d 条信号全部覆盖,嵌套 OR 展开后与原条件完全一致' % len(order))
    print('字符量: %d -> %d (压缩 %.0f%%)' % (orig_chars, new_chars, 100 - 100 * new_chars / orig_chars))

    open(out, 'w', encoding='utf-8').write('\n'.join(out_lines) + '\n')
    print('已写出: %s' % out)


if __name__ == '__main__':
    main()
