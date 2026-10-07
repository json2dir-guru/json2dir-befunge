# json2dir-befunge

json2dir in Befunge-98: `json2dir.b98` (core Funge-98 only, generated from `json2dir.bfs` by the assembler `tools/bfasm.py`) validates the JSON document and prints a shell script, and the POSIX sh launcher `json2dir` runs that script.

    python3 tools/bfasm.py json2dir.bfs > json2dir.b98
    JSON2DIR_CFUNGE=/path/to/cfunge ./json2dir < tree.json
