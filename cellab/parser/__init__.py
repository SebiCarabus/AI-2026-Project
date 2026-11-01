"""Parser for CelLab automaton specs (v1 format)."""

from .compiler import compile_automaton
from .errors import SpecError, SpecIssue
from .parser import load_spec, parse_spec

__all__ = ["parse_spec", "load_spec", "SpecError", "SpecIssue", "compile_automaton"]

# aliases
parse = parse_spec
compile = compile_automaton

# More flexible API
from .parser import parse_spec as _parse_spec
from .compiler import compile_automaton as _compile_automaton

def parse_automaton(text_or_path=None, text=None, path=None):
    if text_or_path is not None:
        # try to detect if it's a path
        from pathlib import Path
        p = Path(str(text_or_path))
        if p.exists() and p.is_file():
            spec = load_spec(p)
        else:
            spec = parse_spec(str(text_or_path))
    elif text is not None:
        spec = parse_spec(text)
    elif path is not None:
        spec = load_spec(path)
    else:
        raise ValueError('no input')
    auto, meta = _compile_automaton(spec)
    return auto
from .compiler import build_automaton, compile
__all__ += ["compile_automaton", "build_automaton", "compile"]
