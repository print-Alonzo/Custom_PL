# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

CSC617M machine project — a custom programming language interpreter implemented in Python 3, built from scratch by Group 67 Inc. The interpreter pipeline is: **Scanner → Parser → Interpreter**. The scanner and parser milestones are complete; the interpreter is next.

## Commands

```bash
# Run the scanner on a source file (stdout)
python scanner.py <source_file>

# Write token stream to a file
python scanner.py <source_file> -o <output_file>

# Suppress source code echo
python scanner.py <source_file> --no-src

# Scan all sample programs and dump to their token files
python scanner.py prog1_calculator.src -o prog1_calculator_tokens.txt
python scanner.py prog2_loops_arrays.src -o prog2_loops_arrays_tokens.txt
python scanner.py prog3_functions.src -o prog3_functions_tokens.txt
python scanner.py prog4_structs_match_exceptions.src -o prog4_structs_match_exceptions_tokens.txt
python scanner.py prog5_advanced.src -o prog5_advanced_tokens.txt

# Run the parser on a source file (stdout)
python parser.py <source_file>

# Write parse report to a file
python parser.py <source_file> -o <output_file>

# Include the AST as JSON in the report
python parser.py <source_file> --ast

# Parse all sample programs
python parser.py prog1_calculator.src
python parser.py prog2_loops_arrays.src
python parser.py prog3_functions.src
python parser.py prog4_structs_match_exceptions.src
python parser.py prog5_advanced.src
```

No external dependencies — Python 3.8+ standard library only.

## Architecture

The entire scanner lives in `scanner.py` as a single-file module. Key classes:

- **`TT`** — namespace of token type string constants (no enum, just class attributes)
- **`Token`** — dataclass with `ttype`, `lexeme`, `line`, `col`, `attr` (coerced literal value)
- **`LexError`** — dataclass capturing message, position, and context snippet
- **`Scanner`** — character-by-character scanner; maintains `pos`, `line`, `col` state; produces `List[Token]` and `List[LexError]` via `scan_all()`
- **`format_output()`** — formats the full report (source listing, error table, token table, stats)
- **`main()`** — CLI entry point via `argparse`

### Scanner internals

`scan_all()` is the main loop: skip whitespace/comments → dispatch on the current character → emit a `Token`. Helpers follow the `_scan_*` naming pattern. The scanner **recovers after every error** — it emits an `ERROR` token and continues, so all valid tokens in the rest of the source are still reported.

The `_scan_number()` method has a special case: when a digit sequence is immediately followed by a letter (e.g. `32abc`), it emits both tokens (integer + identifier) as a tuple and the caller spreads them into the token list.

### Language features

The custom language (`.src` files) supports:
- **Declarations**: `const`, `val` (immutable), `var` (mutable), typed with `int`/`float`/`char`/`string`/`bool`/`void`
- **Control flow**: `if-else`, C-style `for`, `for-in-range` (`..` range op), `for-in-collection`, `while`, `repeat-until`
- **Functions**: typed params, multiple return values, call-by-value, named params
- **Structs & typedefs**: `struct`, `typedef`
- **Pattern matching**: `match` statement and `match` expression with `=>` arms and `_` wildcard
- **Exceptions**: `try`/`catch`/`finally`, `throw`, `guard`
- **Strings**: regular (`"`), interpolated (backtick, `{expr}` inside), escape sequences

### Token output format

The `_tokens.txt` files are the expected scanner output for each sample program. When modifying the scanner, re-run all five sample files and verify the token counts and error counts match expectations before committing.

## Parser (`parser.py`, `grammar.py`, `grammar_engine.py`, `ast_nodes.py`)

The parser is **table-driven**, split across four files so the grammar is data,
separate from the machinery that walks it:

- **`ast_nodes.py`** — `Node` (generic AST node: `kind: str` + `fields: dict`,
  serializable via `to_dict()`), `ParseError`, `merge_type`. Shared by
  `parser.py` and `grammar.py`.
- **`grammar_engine.py`** — a generic PEG-style combinator engine
  (`Term`/`Kw`, `Seq`, `Alt`, `Star`/`Plus`/`Opt`, `Ref`, `Cut`, `And`/`Not`,
  `Bind`, `Emit`/`Abort`, `Rule` (adapts a plain function for productions
  that don't compose cleanly out of the other primitives — the most-used one
  in practice), plus helpers `chainl`, `comma_list`, `many_rec`) and the
  `Engine` that drives a grammar table over a token list. **Has zero
  knowledge of this language's syntax and should never need to change** when
  the grammar changes.
- **`grammar.py`** — the grammar itself, as data: a `GRAMMAR` dict mapping
  rule names to combinator trees, each with an `action` that builds the
  `Node` the interpreter expects. **This is the only file to edit to change
  the language.** See its module docstring for the primitives and the
  "Changing the Grammar" section in `README.md` for a worked example.
- **`parser.py`** — thin CLI driver: `parse_source(source)` scans, runs
  `grammar_engine.Engine().parse(grammar.GRAMMAR, tokens)`, then applies
  `validate_program_structure()` as a post-parse pass; `format_parse_report()`
  and `main()` are unchanged from before the table-driven refactor.

### Parser internals

`GRAMMAR["program"]` repeatedly parses top-level declarations (with error
recovery via `many_rec`, mirroring the original hand-written parser's
synchronize-and-skip behavior). After parsing, `parser.py`'s
`validate_program_structure()` enforces:
- No `val`/`var` at global scope (only `const`, `typedef`, `struct` are valid globally)
- The last `FunctionDecl` must be named `main` with `void` return type

**Block structure** — `GRAMMAR["block"]` is two-phase: a leading declaration
section (`val`/`var`/`let` only), then a statement section. Any declaration
appearing after the first statement is a syntax error. The resulting `Block`
node has separate `declarations` and `statements` fields, which simplifies
the interpreter's scope setup.

**Committed vs. tentative parsing** — each combinator's `.run(ps, committed)`
either matches or raises `Fail` (backtrackable — no error recorded) or
`HardFail` (an error is already recorded in `ps.errors`; propagates past
`Alt`/`Opt`/`Star`/`And`/`Not`, only caught by `many_rec`/the top-level rule).
`Cut()` inside a `Seq` marks the point after which failures become soft
required-token errors (mirrors the pre-refactor parser's `expect()`) rather
than clean backtracking (mirrors `check()`/`match()`).

**Key design decisions captured in the grammar:**
- `const` initializers must be literals (not arbitrary expressions)
- Range operands (`..`) must be integer literals — float ranges are not valid
- Match wildcard `_` must be the last case; any case after `_` is an error
- `guard` condition requires parentheses: `guard (cond) else { ... }`
- All control-flow bodies (`if`, `while`, `for`, `repeat`) require a block `{ }`, not a bare statement
- `typedef` supports both type aliases (`typedef int Foo;`) and inline struct definitions (`typedef struct Foo { ... } Bar;`)
- `try` supports multiple `catch` clauses; `finally` is optional but at least one of `catch`/`finally` is required
- Struct fields support multiple declarators per line: `int x, y;`

### AST node kinds

| Kind | Fields |
|---|---|
| `Program` | `declarations` |
| `FunctionDecl` | `name`, `return_type`, `params`, `body` |
| `StructDecl` | `name`, `fields` |
| `TypedefDecl` | `name`, `aliased_type` |
| `VarDecl` | `mutability` (`const`/`val`/`var`), `declarators` |
| `Declarator` | `name`, `type`, `initializer` |
| `LetDecl` | `names`, `values` (and optionally `array_sizes`) |
| `Block` | `declarations`, `statements` |
| `IfStmt` | `condition`, `then`, `else` |
| `ForStmt` | `init`, `condition`, `update`, `body` |
| `ForInStmt` | `name`, `iterable`, `body` |
| `WhileStmt` | `condition`, `body` |
| `RepeatUntilStmt` | `body`, `condition` |
| `MatchStmt` | `subject`, `cases` |
| `MatchExpr` | `subject`, `cases` |
| `MatchCase` | `pattern`, `body` or `value` |
| `TryStmt` | `body`, `catch_clauses`, `finally_body` |
| `CatchClause` | `name`, `body` |
| `GuardStmt` | `condition`, `else_body` |
| `ThrowStmt` | `value` |
| `ReturnStmt` | `values` |
| `LoopControlStmt` | `keyword` (`break`/`continue`) |
| `BuiltinStmt` | `name` (`print`/`input`), `args` |
| `ExprStmt` | `expression` |
| `AssignExpr` | `target`, `value` |
| `BinaryExpr` | `op`, `left`, `right` |
| `UnaryExpr` | `op`, `operand` |
| `RangeExpr` | `start`, `end` |
| `CallExpr` | `callee`, `args` |
| `IndexExpr` | `object`, `index` |
| `MemberExpr` | `object`, `field`, `op` (`.`) |
| `Literal` | `token_type`, `lexeme`, `value` |
| `Identifier` | `name` |
| `Grouping` | `expression` |
| `WildcardPattern` | _(no fields)_ |
| `MultiAssign` | `lvalues`, `values` |
| `NamedArg` | `name`, `value` |
| `Param` | `name`, `type` |
| `Field` | `name`, `type` |
| `InitializerList` | `values` |
| `Type` | `name` |
| `StructType` | `name` |
| `StructDef` | `name`, `fields` |
| `TupleType` | `elements` |

## Milestones

| Milestone | Deadline | Status |
|---|---|---|
| Scanner | June 11, 2026 | Done |
| Parser | July 2, 2026 | Done |
| Interpreter Checkpoint Demo | July 30, 2026 | Upcoming |
| Final Demo & Submission | August 6, 2026 | Upcoming |
