"""Compile and validate spec v1 into domain objects."""
from __future__ import annotations

from typing import Any

from domain import (
    Automaton,
    CellType,
    Custom,
    Diagonal,
    Fixed,
    Moore,
    Periodic,
    VonNeumann,
)
from domain.rules import (
    Branch,
    CellIn,
    ConditionalRule,
    CountInRange,
    CountInSet,
    Identity,
    MoreThan,
    NeighbourIn,
    Not,
    SetTo,
)

from .errors import SpecError, SpecIssue
from .parser import COLOR_RE, IDENT_RE


def _add_issue(issues: list[SpecIssue], path: str, msg: str):
    issues.append(SpecIssue(path, msg))


def _is_int(x: Any) -> bool:
    if isinstance(x, bool):
        return False
    return isinstance(x, int)


def compile_automaton(spec: dict[str, Any]) -> tuple[Automaton, dict[str, Any]]:
    issues: list[SpecIssue] = []

    allowed_top = {'spec_version', 'name', 'description', 'world', 'states', 'groups', 'neighbourhood', 'update', 'rules'}
    for k in spec.keys():
        if k not in allowed_top:
            _add_issue(issues, str(k), "unknown key")

    if 'spec_version' not in spec:
        _add_issue(issues, 'spec_version', 'required')
    elif not _is_int(spec['spec_version']) or spec['spec_version'] != 1:
        _add_issue(issues, 'spec_version', 'must be 1')

    if 'name' not in spec or not isinstance(spec['name'], str) or spec['name'] == '':
        _add_issue(issues, 'name', 'must be a non-empty string')

    for req in ['world', 'states', 'neighbourhood', 'rules']:
        if req not in spec:
            _add_issue(issues, req, 'required')

    if issues:
        raise SpecError(issues)

    states_list = spec['states']
    if not isinstance(states_list, list) or len(states_list) < 2 or len(states_list) > 256:
        _add_issue(issues, 'states', 'must have 2 to 256 entries')
        raise SpecError(issues)

    state_names = []
    state_info = []
    for i, st in enumerate(states_list):
        if isinstance(st, str):
            name = st
            info = {'name': name}
        elif isinstance(st, dict):
            if 'name' not in st:
                _add_issue(issues, f'states[{i}].name', 'required')
                continue
            name = st['name']
            info = dict(st)
        else:
            _add_issue(issues, f'states[{i}]', 'invalid')
            continue
        if not isinstance(name, str):
            _add_issue(issues, f'states[{i}].name', 'must be a string')
            continue
        if not IDENT_RE.match(name):
            _add_issue(issues, f'states[{i}].name', 'invalid identifier')
            continue
        if name in state_names:
            _add_issue(issues, f'states[{i}].name', 'duplicate state name')
            continue
        state_names.append(name)
        state_info.append(info)

    if issues:
        raise SpecError(issues)

    symbols = []
    for i, info in enumerate(state_info):
        if 'symbol' in info:
            sym = info['symbol']
            if not isinstance(sym, str) or len(sym) != 1 or sym.isspace():
                _add_issue(issues, f'states[{i}].symbol', 'must be one non-whitespace character')
            elif sym in symbols:
                _add_issue(issues, f'states[{i}].symbol', 'duplicate symbol')
            else:
                symbols.append(sym)
        if 'color' in info:
            col = info['color']
            if not isinstance(col, str) or not COLOR_RE.match(col):
                _add_issue(issues, f'states[{i}].color', 'must match #rrggbb')

    if issues:
        raise SpecError(issues)

    state_to_idx = {n: i for i, n in enumerate(state_names)}

    groups = {}
    if 'groups' in spec:
        gmap = spec['groups']
        if not isinstance(gmap, dict):
            _add_issue(issues, 'groups', 'must be a mapping')
        else:
            for gname, members in gmap.items():
                if not isinstance(gname, str) or not IDENT_RE.match(gname):
                    _add_issue(issues, f'groups.{gname}', 'invalid identifier')
                    continue
                if gname in state_to_idx:
                    _add_issue(issues, f'groups.{gname}', 'must not equal a state name')
                    continue
                if not isinstance(members, list) or len(members) == 0:
                    _add_issue(issues, f'groups.{gname}', 'must be a non-empty list')
                    continue
                seen = set()
                resolved = []
                for m in members:
                    if not isinstance(m, str):
                        _add_issue(issues, f'groups.{gname}', 'members must be state names')
                        continue
                    if m in seen:
                        _add_issue(issues, f'groups.{gname}', 'duplicate member')
                        continue
                    if m not in state_to_idx:
                        _add_issue(issues, f'groups.{gname}', f'unknown state {m}')
                        continue
                    seen.add(m)
                    resolved.append(m)
                groups[gname] = resolved

    if issues:
        raise SpecError(issues)

    def is_state_or_group(name: str) -> bool:
        return name in state_to_idx or name in groups

    world = spec['world']
    if not isinstance(world, dict):
        _add_issue(issues, 'world', 'must be a mapping')
        raise SpecError(issues)
    allowed_world = {'width', 'height', 'edges', 'outside', 'infinite'}
    for k in world.keys():
        if k not in allowed_world:
            _add_issue(issues, f'world.{k}', 'unknown key')

    infinite = world.get('infinite', False) is True
    if infinite:
        if any(k in world for k in ['width', 'edges', 'outside']):
            _add_issue(issues, 'world', 'infinite excludes width, edges and outside')
        h = world.get('height')
        if h is not None and h != 1:
            _add_issue(issues, 'world.height', 'must be absent or 1 for infinite')
    else:
        if 'width' not in world or 'height' not in world:
            _add_issue(issues, 'world', 'width and height required')
        else:
            w = world['width']
            h = world['height']
            if not _is_int(w) or w < 1:
                _add_issue(issues, 'world.width', 'must be integer >= 1')
            if not _is_int(h) or h < 1:
                _add_issue(issues, 'world.height', 'must be integer >= 1')
        edges = world.get('edges', 'wrap')
        if edges not in ['wrap', 'fixed']:
            _add_issue(issues, 'world.edges', 'must be wrap or fixed')
        if edges == 'fixed':
            if 'outside' not in world:
                _add_issue(issues, 'world.outside', 'required when edges is fixed')
            else:
                outside = world['outside']
                if outside not in state_to_idx:
                    _add_issue(issues, 'world.outside', 'must name an existing state')
        else:
            if 'outside' in world:
                _add_issue(issues, 'world.outside', 'forbidden when edges is not fixed')

    if issues:
        raise SpecError(issues)

    neigh = spec['neighbourhood']
    if not isinstance(neigh, dict):
        _add_issue(issues, 'neighbourhood', 'must be a mapping')
        raise SpecError(issues)
    allowed_neigh = {'shape', 'radius', 'offsets'}
    for k in neigh.keys():
        if k not in allowed_neigh:
            _add_issue(issues, f'neighbourhood.{k}', 'unknown key')
    shape = neigh.get('shape')
    if shape not in ['moore', 'von_neumann', 'diagonal', 'custom']:
        _add_issue(issues, 'neighbourhood.shape', 'invalid')
        raise SpecError(issues)
    if shape == 'custom':
        if 'offsets' not in neigh:
            _add_issue(issues, 'neighbourhood.offsets', 'required for custom')
            raise SpecError(issues)
        offsets = neigh['offsets']
        if not isinstance(offsets, dict) or len(offsets) == 0:
            _add_issue(issues, 'neighbourhood.offsets', 'must be non-empty map')
            raise SpecError(issues)
        seen_names = set()
        seen_offs = set()
     
        for pname, poff in offsets.items():
            if not isinstance(pname, str) or not IDENT_RE.match(pname):
                _add_issue(issues, f'neighbourhood.offsets.{pname}', 'invalid identifier')
                continue
            if pname in seen_names:
                _add_issue(issues, f'neighbourhood.offsets.{pname}', 'duplicate')
                continue
            seen_names.add(pname)
            if not isinstance(poff, (list, tuple)) or len(poff) != 2:
                _add_issue(issues, f'neighbourhood.offsets.{pname}', 'must be [row, col]')
                continue
            r = poff[0]; c = poff[1]
            if not _is_int(r) or not _is_int(c):
                _add_issue(issues, f'neighbourhood.offsets.{pname}', 'must be integers')
                continue
            if r == 0 and c == 0:
                _add_issue(issues, f'neighbourhood.offsets.{pname}', 'cannot be [0,0]')
                continue
            key = (r, c)
            if key in seen_offs:
                _add_issue(issues, f'neighbourhood.offsets.{pname}', 'duplicate offset')
                continue
            seen_offs.add(key)
    else:
        if 'offsets' in neigh:
            _add_issue(issues, 'neighbourhood.offsets', 'forbidden for presets')
        radius = neigh.get('radius', 1)
        if not _is_int(radius) or radius < 1:
            _add_issue(issues, 'neighbourhood.radius', 'must be integer >= 1')

    if issues:
        raise SpecError(issues)

    if 'update' in spec and spec['update'] != 'synchronous':
        _add_issue(issues, 'update', 'must be synchronous')

    rules = spec['rules']
    if not isinstance(rules, dict) or len(rules) == 0:
        _add_issue(issues, 'rules', 'must be a non-empty map')
        raise SpecError(issues)

    covered_states = set()
    rule_keys_order = list(rules.keys())
    for rkey in rule_keys_order:
        if not isinstance(rkey, str):
            _add_issue(issues, f'rules.{rkey}', 'key must be string')
            continue
        if not is_state_or_group(rkey):
            _add_issue(issues, f'rules.{rkey}', 'must be existing state or group')
            continue
        if rkey in groups:
            for s in groups[rkey]:
                if s in covered_states:
                    _add_issue(issues, f'rules.{rkey}', 'state covered by more than one rules key')
                covered_states.add(s)
        else:
            if rkey in covered_states:
                _add_issue(issues, f'rules.{rkey}', 'state covered by more than one rules key')
            covered_states.add(rkey)
        rlist = rules[rkey]
        if not isinstance(rlist, list) or len(rlist) == 0:
            _add_issue(issues, f'rules.{rkey}', 'must be non-empty list')
            continue
        for i, r in enumerate(rlist):
            if not isinstance(r, dict):
                _add_issue(issues, f'rules.{rkey}[{i}]', 'must be mapping')
                continue
            allowed_r = {'becomes', 'when'}
            for k in r.keys():
                if k not in allowed_r:
                    _add_issue(issues, f'rules.{rkey}[{i}].{k}', 'unknown key')
            if 'becomes' not in r:
                _add_issue(issues, f'rules.{rkey}[{i}].becomes', 'required')
            else:
                b = r['becomes']
                if b not in state_to_idx:
                    _add_issue(issues, f'rules.{rkey}[{i}].becomes', 'must name an existing state')
            if 'when' in r:
                when = r['when']
                if not isinstance(when, dict) or len(when) == 0:
                    _add_issue(issues, f'rules.{rkey}[{i}].when', 'invalid')

    if issues:
        raise SpecError(issues)

    # Build neighbourhood
    if shape == 'moore':
        radius = neigh.get('radius', 1)
        neighborhood = Moore(radius=radius, dimensions=2)
    elif shape == 'von_neumann':
        radius = neigh.get('radius', 1)
        neighborhood = VonNeumann(radius=radius, dimensions=2)
    elif shape == 'diagonal':
        radius = neigh.get('radius', 1)
        neighborhood = Diagonal(radius=radius, dimensions=2)
    elif shape == 'custom':
        offsets_map = neigh['offsets']
        points = []
        labels = []
        for pname, poff in offsets_map.items():
            points.append((int(poff[0]), int(poff[1])))
            labels.append(pname)
        neighborhood = Custom(tuple(points), tuple(labels))
    else:
        raise SpecError([SpecIssue('neighbourhood.shape', 'invalid')])

    # Build boundary
    if infinite:
        boundary = Periodic()
        is_infinite = True
    else:
        edges = world.get('edges', 'wrap')
        if edges == 'fixed':
            outside = world['outside']
            boundary = Fixed(state_to_idx[outside])
        else:
            boundary = Periodic()
        is_infinite = False

    def resolve_state_or_group_to_set(sg: str) -> set[int]:
        if sg in groups:
            return set(state_to_idx[x] for x in groups[sg])
        return {state_to_idx[sg]}

    def resolve_to_indices(sg_or_list) -> set[int]:
        if isinstance(sg_or_list, str):
            return resolve_state_or_group_to_set(sg_or_list)
        elif isinstance(sg_or_list, (list, tuple)):
            res = set()
            for x in sg_or_list:
                res |= resolve_state_or_group_to_set(x)
            return res
        return set()

    def build_count_condition(count_key: str, count_val: Any, path: str):
        if not isinstance(count_key, str) or not is_state_or_group(count_key):
            _add_issue(issues, path, 'must be existing state or group')
            return None
        target_set = resolve_state_or_group_to_set(count_key)
        if isinstance(count_val, int) and not isinstance(count_val, bool):
            if count_val < 0:
                _add_issue(issues, path, 'count must be >= 0')
                return None
            return CountInRange(target_set, minimum=count_val, maximum=count_val)
        elif isinstance(count_val, (list, tuple)):
            if len(count_val) == 0:
                _add_issue(issues, path, 'invalid')
                return None
            allowed = set()
            for v in count_val:
                if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                    _add_issue(issues, path, 'invalid count')
                    return None
                allowed.add(v)
            return CountInSet(target_set, allowed=frozenset(allowed))
        elif isinstance(count_val, dict):
            allowed_keys = {'min', 'max', 'more_than'}
            for k in count_val.keys():
                if k not in allowed_keys:
                    _add_issue(issues, f'{path}.{k}', 'unknown key')
            if not any(k in count_val for k in allowed_keys):
                _add_issue(issues, path, 'needs at least one of min/max/more_than')
                return None
            if 'more_than' in count_val:
                mt = count_val['more_than']
                if not isinstance(mt, str) or not is_state_or_group(mt):
                    _add_issue(issues, f'{path}.more_than', 'must reference existing state or group')
                    return None
                mt_set = resolve_state_or_group_to_set(mt)
                cond = MoreThan(target_set, mt_set)
                mn = count_val.get('min')
                mx = count_val.get('max')
                if mn is not None:
                    if not isinstance(mn, int) or isinstance(mn, bool) or mn < 0:
                        _add_issue(issues, f'{path}.min', 'invalid')
                        return None
                    cond = cond & CountInRange(target_set, minimum=mn)
                if mx is not None:
                    if not isinstance(mx, int) or isinstance(mx, bool) or mx < 0:
                        _add_issue(issues, f'{path}.max', 'invalid')
                        return None
                    cond = cond & CountInRange(target_set, maximum=mx)
                return cond
            else:
                mn = count_val.get('min')
                mx = count_val.get('max')
                if mn is not None and mx is not None and mn > mx:
                    _add_issue(issues, path, 'min > max')
                    return None
                if mn is not None and (not isinstance(mn, int) or isinstance(mn, bool) or mn < 0):
                    _add_issue(issues, f'{path}.min', 'invalid')
                    return None
                if mx is not None and (not isinstance(mx, int) or isinstance(mx, bool) or mx < 0):
                    _add_issue(issues, f'{path}.max', 'invalid')
                    return None
                return CountInRange(target_set, minimum=mn, maximum=mx)
        else:
            _add_issue(issues, path, 'invalid count test')
            return None

    def build_condition(when: Any, path: str):
        if not isinstance(when, dict):
            _add_issue(issues, path, 'condition must be mapping')
            return None
        if len(when) == 0:
            _add_issue(issues, path, 'empty condition invalid')
            return None
        allowed = {'neighbours', 'cells', 'all', 'any', 'not'}
        for k in when.keys():
            if k not in allowed:
                _add_issue(issues, f'{path}.{k}', 'unknown key')

        conds = []
        if 'neighbours' in when:
            npath = f'{path}.neighbours'
            ncond = when['neighbours']
            if not isinstance(ncond, dict) or len(ncond) == 0:
                _add_issue(issues, npath, 'invalid')
            else:
                subconds = []
                for ck, cv in ncond.items():
                    if not isinstance(ck, str) or not is_state_or_group(ck):
                        _add_issue(issues, f'{npath}.{ck}', 'must be existing state or group')
                        continue
                    c = build_count_condition(ck, cv, f'{npath}.{ck}')
                    if c is not None:
                        subconds.append(c)
                if subconds:
                    result = subconds[0]
                    for sc in subconds[1:]:
                        result = result & sc
                    conds.append(result)
        if 'cells' in when:
            cpath = f'{path}.cells'
            ccond = when['cells']
            if not isinstance(ccond, dict) or len(ccond) == 0:
                _add_issue(issues, cpath, 'invalid')
            else:
                subconds = []
                for posname, allowed in ccond.items():
                    if not isinstance(posname, str):
                        _add_issue(issues, f'{cpath}.{posname}', 'invalid')
                        continue
                    try:
                        posidx = neighborhood.index_of(posname)
                    except Exception:
                        _add_issue(issues, f'{cpath}.{posname}', 'unknown position')
                        continue
                    aset = resolve_to_indices(allowed)
                    if len(aset) == 0:
                        _add_issue(issues, f'{cpath}.{posname}', 'invalid')
                        continue
                    subconds.append(NeighbourIn(posidx, aset))
                if subconds:
                    result = subconds[0]
                    for sc in subconds[1:]:
                        result = result & sc
                    conds.append(result)
        if 'all' in when:
            apath = f'{path}.all'
            alist = when['all']
            if not isinstance(alist, list) or len(alist) == 0:
                _add_issue(issues, apath, 'must be non-empty list')
            else:
                subconds = []
                for i, item in enumerate(alist):
                    sc = build_condition(item, f'{apath}[{i}]')
                    if sc is not None:
                        subconds.append(sc)
                if subconds:
                    result = subconds[0]
                    for sc in subconds[1:]:
                        result = result & sc
                    conds.append(result)
        if 'any' in when:
            apath = f'{path}.any'
            alist = when['any']
            if not isinstance(alist, list) or len(alist) == 0:
                _add_issue(issues, apath, 'must be non-empty list')
            else:
                subconds = []
                for i, item in enumerate(alist):
                    sc = build_condition(item, f'{apath}[{i}]')
                    if sc is not None:
                        subconds.append(sc)
                if subconds:
                    result = subconds[0]
                    for sc in subconds[1:]:
                        result = result | sc
                    conds.append(result)
        if 'not' in when:
            npath = f'{path}.not'
            nitem = when['not']
            sc = build_condition(nitem, npath)
            if sc is not None:
                conds.append(Not(sc))

        if len(conds) == 0:
            return None
        if len(conds) == 1:
            return conds[0]
        result = conds[0]
        for c in conds[1:]:
            result = result & c
        return result

    if issues:
        raise SpecError(issues)

    branches = []
    for rkey in rule_keys_order:
        rlist = rules[rkey]
        key_states = resolve_state_or_group_to_set(rkey)
        key_cond = CellIn(key_states)
        for i, r in enumerate(rlist):
            rpath = f'rules.{rkey}[{i}]'
            becomes = r['becomes']
            becomes_idx = state_to_idx[becomes]
            if 'when' in r:
                wcond = build_condition(r['when'], f'{rpath}.when')
                if wcond is not None:
                    branches.append(Branch(key_cond & wcond, SetTo(becomes_idx)))
                else:
                    # if condition is invalid we should have added issues
                    pass
            else:
                branches.append(Branch(key_cond, SetTo(becomes_idx)))

    if issues:
        raise SpecError(issues)

    rule = ConditionalRule(branches, otherwise=Identity())
    automaton = Automaton(
        name=spec['name'],
        cell_types=[CellType(n) for n in state_names],
        neighborhood=neighborhood,
        boundary=boundary,
        rule=rule,
    )

    if infinite:
        if not automaton.is_quiescent():
            _add_issue(issues, 'world', 'infinite world requires state 0 to be quiescent')
        if neighborhood.reach > 1:
            _add_issue(issues, 'neighbourhood', 'infinite worlds require neighbourhood reach 1')
        else:
            for off in neighborhood.offsets():
                if len(off) >= 2:
                    dr, dc = off[0], off[1]
                    if abs(dr) > 1 or abs(dc) > 1:
                        _add_issue(issues, 'neighbourhood', 'infinite worlds require all offsets within distance 1')
            h = world.get('height')
            if h == 1:
                for off in neighborhood.offsets():
                    if len(off) >= 1 and off[0] != 0:
                        _add_issue(issues, 'neighbourhood', 'with height:1 all row offsets must be 0')

    if issues:
        raise SpecError(issues)

    metadata = {
        'is_infinite': infinite,
        'world_width': world.get('width'),
        'world_height': world.get('height'),
        'states': state_info,
    }
    return automaton, metadata


# Flexible API
def compile(spec_or_dict=None, *args, **kwargs):
    if spec_or_dict is None and args:
        spec_or_dict = args[0]
    if hasattr(spec_or_dict, 'compile'):
        result = spec_or_dict.compile()
        return result
    # assume dict
    auto, meta = compile_automaton(spec_or_dict)
    return auto


def build_automaton(spec_or_dict=None, *args, **kwargs):
    auto, meta = compile_automaton(spec_or_dict) if hasattr(spec_or_dict, 'keys') or isinstance(spec_or_dict, dict) else compile(spec_or_dict)
    if isinstance(auto, Automaton):
        return auto
    return auto
