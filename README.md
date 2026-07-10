# CSC617M Machine Project — Custom Language Interpreter

**Course:** CSC617M — Theory of Programming Languages  
**Group:** 67 Inc.  
**Current Milestone:** Parser (Syntax Analyzer)

---

## Overview

This repository contains the interpreter project for CSC617M. The group designed a new programming language from scratch and is implementing a full interpreter for it in Python. The interpreter pipeline is:

**Scanner → Parser → Interpreter**

The scanner and parser milestones are complete. The interpreter is next.

The language supports typed variable/constant declarations, control flow (if-else, for, while, repeat-until), functions with multiple parameter-passing schemes, structs, pattern matching, exception handling, and interpolated strings.

---

## Repository Structure

```
.
├── scanner.py                                  # Lexical analyzer
├── parser.py                                   # Syntax analyzer / AST builder
├── prog1_calculator.src                        # Sample program 1
├── prog1_calculator_tokens.txt                 # Scanner output for prog1
├── prog2_loops_arrays.src                      # Sample program 2
├── prog2_loops_arrays_tokens.txt               # Scanner output for prog2
├── prog3_functions.src                         # Sample program 3
├── prog3_functions_tokens.txt                  # Scanner output for prog3
├── prog4_structs_match_exceptions.src          # Sample program 4
├── prog4_structs_match_exceptions_tokens.txt   # Scanner output for prog4
├── prog5_advanced.src                          # Sample program 5
├── prog5_advanced_tokens.txt                   # Scanner output for prog5
└── README.md
```

---

## Requirements

- Python 3.8 or higher
- No external dependencies — standard library only

---

## Running the Scanner

```bash
# Print token stream to stdout
python scanner.py <source_file>

# Write token stream to a file
python scanner.py <source_file> -o <output_file>

# Suppress source code echo
python scanner.py <source_file> --no-src
```

### Example

```bash
python scanner.py prog1_calculator.src
python scanner.py prog3_functions.src -o out.txt
```

---

## Running the Parser

```bash
# Parse and print report to stdout
python parser.py <source_file>

# Write parse report to a file
python parser.py <source_file> -o <output_file>

# Include the AST as JSON in the report
python parser.py <source_file> --ast
```

### Example

```bash
python parser.py prog1_calculator.src
python parser.py prog4_structs_match_exceptions.src --ast
```

---

## Scanner Output Format

Each run produces three sections:

**1. Source listing** (suppressed with `--no-src`)
```
  1 | const int MAX_TRIES = 3;
  2 | const float PI = 3.14159;
```

**2. Lexical errors** — with line number, column, and context
```
[LEXICAL ERROR] Line 3, Col 18: Invalid token: digit sequence mixed with identifier chars
    (near: '32432bace12awf')
```

**3. Token stream table**
```
    #  TYPE              LEXEME                             LINE    COL  ATTR
  --------------------------------------------------------------------------------
    1  KEYWORD           'const'                               1      1
    2  KEYWORD           'int'                                 1      7
    3  IDENTIFIER        'MAX_TRIES'                           1     11
    4  ASSIGN_OP         '='                                   1     21
    5  INTEGER_LIT       '3'                                   1     23  3
    6  SEMICOLON         ';'                                   1     24
```

Literal tokens carry a coerced attribute value: integers as `int`, floats as `float`, strings/chars as their unescaped Python value, and booleans as `True`/`False`.

**4. Statistics** — token type breakdown, total errors, scan time, source size.

---

## Parser Output Format

The parse report includes:

**1. Summary** — parse error count and lex error count  
**2. Parse errors** — each with line number, column, and message  
**3. Optional AST** (with `--ast`) — full abstract syntax tree as JSON

```
Parse complete: 0 syntax error(s), 0 lex error(s)
```

With `--ast`:
```json
{
  "kind": "Program",
  "declarations": [
    {
      "kind": "FunctionDecl",
      "name": "main",
      "return_type": "void",
      ...
    }
  ]
}
```

---

## Token Types

| Category | Token Types |
|---|---|
| Literals | `INTEGER_LIT`, `FLOAT_LIT`, `CHAR_LIT`, `STRING_LIT`, `INTERP_STRING`, `BOOL_LIT` |
| Names | `IDENTIFIER`, `KEYWORD`, `UNDERSCORE` |
| Arithmetic | `ARITH_OP` (`+` `-` `*` `/` `%`) |
| Relational | `REL_OP` (`==` `!=` `<` `>` `<=` `>=`) |
| Logical | `LOGIC_OP` (`&&` `\|\|` `!`) |
| Assignment | `ASSIGN_OP` (`=`) |
| Special ops | `RANGE_OP` (`..`), `MATCH_ARROW` (`=>`) |
| Delimiters | `SEMICOLON`, `COMMA`, `COLON`, `LPAREN`, `RPAREN`, `LBRACE`, `RBRACE`, `LBRACKET`, `RBRACKET`, `DOT` |
| Special | `EOF`, `ERROR` |

### Reserved Keywords

```
int    float   char    string  bool    void    const   val     var
struct let     typedef if      else    for     while   repeat  until
return break   continue true   false   print   input   in      match
guard  try     catch   finally throw   _
```

---

## AST Node Reference

| Kind | Key Fields |
|---|---|
| `Program` | `declarations` |
| `FunctionDecl` | `name`, `return_type`, `params`, `body` |
| `StructDecl` | `name`, `fields` |
| `TypedefDecl` | `name`, `aliased_type` |
| `VarDecl` | `mutability` (`const`/`val`/`var`), `declarators` |
| `LetDecl` | `names`, `values` |
| `Block` | `declarations`, `statements` |
| `IfStmt` | `condition`, `then`, `else` |
| `ForStmt` | `init`, `condition`, `update`, `body` |
| `ForInStmt` | `name`, `iterable`, `body` |
| `WhileStmt` | `condition`, `body` |
| `RepeatUntilStmt` | `body`, `condition` |
| `MatchStmt` / `MatchExpr` | `subject`, `cases` |
| `TryStmt` | `body`, `catch_clauses`, `finally_body` |
| `GuardStmt` | `condition`, `else_body` |
| `ThrowStmt` | `value` |
| `ReturnStmt` | `values` |
| `AssignExpr` | `target`, `value` |
| `BinaryExpr` | `op`, `left`, `right` |
| `UnaryExpr` | `op`, `operand` |
| `CallExpr` | `callee`, `args` |
| `IndexExpr` | `object`, `index` |
| `MemberExpr` | `object`, `field`, `op` (`.`) |
| `Literal` | `token_type`, `lexeme`, `value` |
| `Identifier` | `name` |

---

## Error Detection

### Lexical errors (Scanner)

| Error | Example |
|---|---|
| Digit–letter mixed token | `32432bace` |
| Unterminated string literal | `"hello world` |
| Unterminated character literal | `'A` |
| Empty character literal | `''` |
| Unterminated block comment | `/* never closed` |
| Unknown symbol | `@`, `#`, `$` |
| Unknown escape sequence | `\q` |

The scanner recovers and continues after each error.

### Syntax errors (Parser)

The parser uses recursive descent with error recovery — it synchronizes to the next statement boundary after each error and continues parsing, so all errors in a file are reported in one pass.

---

## Sample Programs

| File | Constructs Demonstrated |
|---|---|
| `prog1_calculator.src` | `const`, `val`, `var`, `int`/`float`/`string`, `input`, `print`, arithmetic, nested `if-else` |
| `prog2_loops_arrays.src` | C-style `for`, `for-in-range`, `for-in-collection`, `while`, `repeat-until`, arrays, `break`, `continue` |
| `prog3_functions.src` | Function declarations, call-by-value, array params, multiple return values, recursion, `let`, named parameters, interpolated strings |
| `prog4_structs_match_exceptions.src` | `struct`, `typedef`, `match` statement, `match` expression, `guard`, `try`/`catch`/`finally`, `throw`, `char`/`bool` literals, escape sequences |
| `prog5_advanced.src` | Multi-assignment, `let` destructuring, complex expressions, nested loops |

---

## Speed

Tested on a synthetic 5,000-line / ~190 KB file:

```
Total tokens : 37,711
Scan time    : ~207 ms
```

---

## Milestones

| Milestone | Deadline | Status |
|---|---|---|
| CFG, Lexical Rules, Intermediate Code Spec, Language Choice | May 28, 2026 | ✅ Done |
| Scanner | June 11, 2026 | ✅ Done |
| Parser | July 2, 2026 | ✅ Done |
| Interpreter Checkpoint Demo | July 30, 2026 | 🔲 Upcoming |
| Final Project Demo & Submission | August 6, 2026 | 🔲 Upcoming |

---

## Implementation Language

The interpreter is implemented in **Python 3**. Chosen for its dynamic typing, native dict/list/tuple data structures (ideal for symbol tables and token streams), strong string processing, and suitability for recursive descent parsing.
