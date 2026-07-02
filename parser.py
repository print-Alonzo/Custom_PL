import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from scanner import Scanner, TT, Token


TYPE_KEYWORDS = {"int", "float", "char", "string", "bool", "void"}
DECL_KEYWORDS = {"const", "val", "var"}


@dataclass
class ParseError:
    message: str
    token: Token

    def __str__(self):
        found = "EOF" if self.token.ttype == TT.EOF else repr(self.token.lexeme)
        return (
            f"[SYNTAX ERROR] Line {self.token.line}, Col {self.token.col}: "
            f"{self.message} (found {found})"
        )


@dataclass
class Node:
    kind: str
    fields: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self):
        return {"kind": self.kind, **{k: to_jsonable(v) for k, v in self.fields.items()}}


def to_jsonable(value):
    if isinstance(value, Node):
        return value.to_dict()
    if isinstance(value, list):
        return [to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    return value


class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0
        self.errors = []
        self._fn_name_tokens: dict = {}

    def current(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else self.tokens[-1]

    def previous(self):
        return self.tokens[self.pos - 1]

    def at_end(self):
        return self.current().ttype == TT.EOF

    def check(self, ttype=None, lexeme=None):
        tok = self.current()
        if ttype is not None and tok.ttype != ttype:
            return False
        if lexeme is not None and tok.lexeme != lexeme:
            return False
        return True

    def check_keyword(self, lexeme):
        return self.check(TT.KEYWORD, lexeme)

    def match(self, ttype=None, lexeme=None):
        if self.check(ttype, lexeme):
            tok = self.current()
            self.pos += 1
            return tok
        return None

    def match_keyword(self, lexeme):
        return self.match(TT.KEYWORD, lexeme)

    def expect(self, ttype, lexeme=None, message=None):
        tok = self.match(ttype, lexeme)
        if tok:
            return tok
        self.error(message or f"Expected {lexeme or ttype}")
        return self.current()

    def expect_keyword(self, lexeme, message=None):
        tok = self.match_keyword(lexeme)
        if tok:
            return tok
        self.error(message or f"Expected keyword {lexeme!r}")
        return self.current()

    def error(self, message):
        self.errors.append(ParseError(message, self.current()))

    def synchronize(self):
        while not self.at_end():
            if self.previous().ttype == TT.SEMICOLON:
                return
            if self.check(TT.RBRACE):
                return
            if self.check(TT.KEYWORD) and self.current().lexeme in {
                "const", "val", "var", "let", "if", "for", "while", "repeat",
                "return", "break", "continue", "match", "guard", "try", "throw",
                "struct", "typedef",
            }:
                return
            self.pos += 1

    def parse(self):
        declarations = []
        while not self.at_end():
            try:
                declarations.append(self.parse_top_level())
            except RuntimeError:
                self.synchronize()
                if not self.at_end() and not self.check(TT.RBRACE):
                    self.pos += 1
        self._validate_program_structure(declarations)
        return Node("Program", {"declarations": declarations})

    def _validate_program_structure(self, declarations):
        eof_tok = self.tokens[-1]

        func_decls = [d for d in declarations if d.kind == "FunctionDecl"]
        if not func_decls:
            self.errors.append(ParseError("Program must define at least one function", eof_tok))
            return

        last_fn = func_decls[-1]
        fn_name = last_fn.fields.get("name")
        fn_tok = self._fn_name_tokens.get(fn_name, eof_tok)
        if fn_name != "main":
            self.errors.append(ParseError(
                f"Expected 'main' as the last function declaration, found '{fn_name}'",
                fn_tok,
            ))
            return

        ret = last_fn.fields.get("return_type")
        if not (isinstance(ret, Node) and ret.kind == "Type" and ret.fields.get("name") == "void"):
            self.errors.append(ParseError("'main' function must have return type 'void'", fn_tok))

        params = last_fn.fields.get("params", [])
        if params:
            self.errors.append(ParseError("'main' function must have no parameters", fn_tok))

    def parse_top_level(self):
        if self.check_keyword("struct"):
            return self.parse_struct_decl()
        if self.check_keyword("typedef"):
            return self.parse_typedef_decl()
        if self.check(TT.KEYWORD) and self.current().lexeme in DECL_KEYWORDS:
            if self.current().lexeme in {"val", "var"}:
                self.error(f"'{self.current().lexeme}' declarations are not allowed at global scope")
            return self.parse_var_decl(require_semicolon=True)

        typ = self.parse_type(allow_tuple=True, allow_void=True)
        name_tok = self.expect(TT.IDENTIFIER, message="Expected declaration name")
        if self.check(TT.LPAREN):
            return self.finish_function_decl(typ, name_tok)

        self.error("Expected function parameter list after top-level declaration")
        raise RuntimeError()

    def parse_struct_decl(self):
        self.expect_keyword("struct")
        name = self.expect(TT.IDENTIFIER, message="Expected struct name").lexeme
        self.expect(TT.LBRACE, message="Expected '{' before struct fields")

        fields = []
        while not self.check(TT.RBRACE) and not self.at_end():
            field_type = self.parse_type()
            while True:
                field_name = self.parse_declarator_name()
                fields.append(Node("Field", {
                    "name": field_name["name"],
                    "type": merge_type(field_type, field_name),
                }))
                if not self.match(TT.COMMA):
                    break
            self.expect(TT.SEMICOLON, message="Expected ';' after struct field")

        self.expect(TT.RBRACE, message="Expected '}' after struct body")
        self.expect(TT.SEMICOLON, message="Expected ';' after struct declaration")
        return Node("StructDecl", {"name": name, "fields": fields})

    def parse_typedef_decl(self):
        self.expect_keyword("typedef")
        # Detect inline struct definition: typedef struct Foo { ... } Alias;
        if self._peek_inline_struct():
            self.expect_keyword("struct")
            struct_name = self.expect(TT.IDENTIFIER, message="Expected struct name in typedef").lexeme
            self.expect(TT.LBRACE, message="Expected '{' before inline struct fields")
            fields = []
            while not self.check(TT.RBRACE) and not self.at_end():
                field_type = self.parse_type()
                while True:
                    field_name = self.parse_declarator_name()
                    fields.append(Node("Field", {
                        "name": field_name["name"],
                        "type": merge_type(field_type, field_name),
                    }))
                    if not self.match(TT.COMMA):
                        break
                self.expect(TT.SEMICOLON, message="Expected ';' after struct field")
            self.expect(TT.RBRACE, message="Expected '}' after inline struct body")
            aliased = Node("StructDef", {"name": struct_name, "fields": fields})
        else:
            aliased = self.parse_type()
        name = self.expect(TT.IDENTIFIER, message="Expected typedef alias name").lexeme
        self.expect(TT.SEMICOLON, message="Expected ';' after typedef")
        return Node("TypedefDecl", {"name": name, "aliased_type": aliased})

    def _peek_inline_struct(self):
        return (
            self.pos + 2 < len(self.tokens)
            and self.tokens[self.pos].ttype == TT.KEYWORD
            and self.tokens[self.pos].lexeme == "struct"
            and self.tokens[self.pos + 1].ttype == TT.IDENTIFIER
            and self.tokens[self.pos + 2].ttype == TT.LBRACE
        )

    def finish_function_decl(self, return_type, name_tok):
        name = name_tok.lexeme
        self._fn_name_tokens[name] = name_tok
        self.expect(TT.LPAREN)
        params = []

        if not self.check(TT.RPAREN):
            while True:
                param_type = self.parse_type()
                param_name = self.parse_declarator_name()
                params.append(Node("Param", {
                    "name": param_name["name"],
                    "type": merge_type(param_type, param_name),
                }))
                if not self.match(TT.COMMA):
                    break

        self.expect(TT.RPAREN, message="Expected ')' after parameters")
        body = self.parse_block()
        return Node("FunctionDecl", {
            "name": name,
            "return_type": return_type,
            "params": params,
            "body": body,
        })

    def parse_type(self, allow_tuple=False, allow_void=False):
        if allow_tuple and self.match(TT.LPAREN):
            elements = [self.parse_type()]
            while self.match(TT.COMMA):
                elements.append(self.parse_type())
            self.expect(TT.RPAREN, message="Expected ')' after tuple return type")
            if len(elements) < 2:
                self.error("Tuple return type must contain at least two types")
            return Node("TupleType", {"elements": elements})

        if self.match_keyword("struct"):
            name = self.expect(TT.IDENTIFIER, message="Expected struct type name").lexeme
            base = Node("StructType", {"name": name})
        elif self.check(TT.KEYWORD) and self.current().lexeme in TYPE_KEYWORDS:
            if self.current().lexeme == "void" and not allow_void:
                self.error("'void' is not a valid type here")
                raise RuntimeError()
            base = Node("Type", {"name": self.current().lexeme})
            self.pos += 1
        elif self.check(TT.IDENTIFIER):
            base = Node("Type", {"name": self.current().lexeme})
            self.pos += 1
        else:
            self.error("Expected type")
            raise RuntimeError()

        depth = 0
        while self.match(TT.ARITH_OP, "*"):
            depth += 1
        if depth:
            base = Node("PointerType", {"base": base, "depth": depth})
        return base

    def parse_declarator_name(self):
        depth = 0
        while self.match(TT.ARITH_OP, "*"):
            depth += 1

        name = self.expect(TT.IDENTIFIER, message="Expected identifier").lexeme
        arrays = []

        while self.match(TT.LBRACKET):
            size = None if self.check(TT.RBRACKET) else self.parse_expression()
            self.expect(TT.RBRACKET, message="Expected ']' after array size")
            arrays.append(size)

        return {"name": name, "pointer_depth": depth, "arrays": arrays}

    def parse_block(self):
        self.expect(TT.LBRACE, message="Expected '{' before block")
        declarations = []
        statements = []

        while not self.check(TT.RBRACE) and not self.at_end() and self._at_block_decl():
            try:
                declarations.append(self._parse_block_decl())
            except RuntimeError:
                self.synchronize()
                if not self.at_end() and not self.check(TT.RBRACE):
                    self.pos += 1

        while not self.check(TT.RBRACE) and not self.at_end():
            if self._at_block_decl():
                self.error("Variable declarations must appear before statements in a block")
                try:
                    self._parse_block_decl()
                except RuntimeError:
                    self.synchronize()
                    if not self.at_end() and not self.check(TT.RBRACE):
                        self.pos += 1
            else:
                try:
                    statements.append(self.parse_statement())
                except RuntimeError:
                    self.synchronize()
                    if not self.at_end() and not self.check(TT.RBRACE):
                        self.pos += 1

        self.expect(TT.RBRACE, message="Expected '}' after block")
        return Node("Block", {"declarations": declarations, "statements": statements})

    def _at_block_decl(self):
        return (
            (self.check(TT.KEYWORD) and self.current().lexeme in {"val", "var"}) or
            self.check_keyword("let")
        )

    def _parse_block_decl(self):
        if self.check_keyword("let"):
            return self.parse_let_stmt()
        return self.parse_var_decl(require_semicolon=True)

    def parse_multi_assign_stmt(self):
        """Parse <multi_assign>: typed multi-lvalue = expr_list/func_call."""
        lvalues = []
        first_type = self.parse_type()
        first_name = self.expect(TT.IDENTIFIER, message="Expected identifier after type").lexeme
        lvalues.append({"type": first_type, "name": first_name})

        while self.match(TT.COMMA):
            if self.check(TT.KEYWORD) and self.current().lexeme in TYPE_KEYWORDS:
                next_type = self.parse_type()
            else:
                next_type = first_type
            next_name = self.expect(TT.IDENTIFIER, message="Expected identifier in multi-assign").lexeme
            lvalues.append({"type": next_type, "name": next_name})

        self.expect(TT.ASSIGN_OP, message="Expected '=' in multi-assign statement")
        values = [self.parse_expression()]
        while self.match(TT.COMMA):
            values.append(self.parse_expression())
        self.expect(TT.SEMICOLON, message="Expected ';' after multi-assign")
        return Node("MultiAssign", {"lvalues": lvalues, "values": values})

    def parse_statement(self):
        if self.check(TT.LBRACE):
            return self.parse_block()
        if self.check_keyword("if"):
            return self.parse_if_stmt()
        if self.check_keyword("for"):
            return self.parse_for_stmt()
        if self.check_keyword("while"):
            return self.parse_while_stmt()
        if self.check_keyword("repeat"):
            return self.parse_repeat_stmt()
        if self.check_keyword("return"):
            return self.parse_return_stmt()
        if self.check_keyword("break") or self.check_keyword("continue"):
            return self.parse_loop_control_stmt()
        if self.check_keyword("print") or self.check_keyword("input"):
            return self.parse_builtin_stmt()
        if self.check_keyword("match"):
            return self.parse_match_statement()
        if self.check_keyword("guard"):
            return self.parse_guard_stmt()
        if self.check_keyword("try"):
            return self.parse_try_stmt()
        if self.check_keyword("throw"):
            return self.parse_throw_stmt()
        if self.check(TT.KEYWORD) and self.current().lexeme in TYPE_KEYWORDS:
            return self.parse_multi_assign_stmt()
        return self.parse_expr_stmt()

    def parse_var_decl(self, require_semicolon):
        mutability = self.current().lexeme
        self.pos += 1
        typ = self.parse_type()
        declarators = []
        is_const = mutability == "const"

        while True:
            name_info = self.parse_declarator_name()
            if is_const:
                self.expect(TT.ASSIGN_OP, message="Expected '=' after const declarator name")
                initializer = self.parse_literal()
            else:
                initializer = self.parse_initializer() if self.match(TT.ASSIGN_OP) else None
            declarators.append(Node("Declarator", {
                "name": name_info["name"],
                "type": merge_type(typ, name_info),
                "initializer": initializer,
            }))
            if is_const:
                break
            if not self.match(TT.COMMA):
                break

        if require_semicolon:
            self.expect(TT.SEMICOLON, message="Expected ';' after declaration")
        return Node("VarDecl", {"mutability": mutability, "declarators": declarators})

    def parse_literal(self):
        tok = self.current()
        if tok.ttype in {TT.INTEGER_LIT, TT.FLOAT_LIT, TT.STRING_LIT, TT.CHAR_LIT, TT.BOOL_LIT}:
            self.pos += 1
            return Node("Literal", {"token_type": tok.ttype, "lexeme": tok.lexeme, "value": tok.attr})
        self.error("Expected a literal value (int, float, char, string, or bool) in const declaration")
        raise RuntimeError()

    def parse_initializer(self):
        if self.match(TT.LBRACE):
            values = []
            if not self.check(TT.RBRACE):
                while True:
                    values.append(self.parse_expression())
                    if not self.match(TT.COMMA):
                        break
            self.expect(TT.RBRACE, message="Expected '}' after initializer list")
            return Node("InitializerList", {"values": values})
        return self.parse_expression()

    def parse_let_stmt(self):
        self.expect_keyword("let")
        first_name = self.expect(TT.IDENTIFIER, message="Expected identifier in let declaration").lexeme

        # Array form: let name[size] = { ... }
        if self.match(TT.LBRACKET):
            size = None if self.check(TT.RBRACKET) else self.parse_expression()
            self.expect(TT.RBRACKET, message="Expected ']' after array size")
            self.expect(TT.ASSIGN_OP, message="Expected '=' in let array declaration")
            initializer = self.parse_initializer()
            self.expect(TT.SEMICOLON, message="Expected ';' after let declaration")
            return Node("LetDecl", {"names": [first_name], "array_sizes": [size], "values": [initializer]})

        names = [first_name]
        while self.match(TT.COMMA):
            names.append(self.expect(TT.IDENTIFIER, message="Expected identifier after ','").lexeme)

        self.expect(TT.ASSIGN_OP, message="Expected '=' in let declaration")

        values = [self.parse_expression()]
        while self.match(TT.COMMA):
            values.append(self.parse_expression())

        self.expect(TT.SEMICOLON, message="Expected ';' after let declaration")
        return Node("LetDecl", {"names": names, "values": values})

    def parse_if_stmt(self):
        self.expect_keyword("if")
        condition = self.parse_parenthesized_expression("if condition")
        then_branch = self.parse_block()
        else_branch = None

        if self.match_keyword("else"):
            else_branch = self.parse_if_stmt() if self.check_keyword("if") else self.parse_block()

        return Node("IfStmt", {"condition": condition, "then": then_branch, "else": else_branch})

    def parse_for_stmt(self):
        self.expect_keyword("for")
        self.expect(TT.LPAREN, message="Expected '(' after for")

        if self.check(TT.IDENTIFIER) and self.peek_keyword("in"):
            name = self.expect(TT.IDENTIFIER).lexeme
            self.expect_keyword("in")
            iterable = self.parse_expression()
            self.expect(TT.RPAREN, message="Expected ')' after for-in clause")
            return Node("ForInStmt", {"name": name, "iterable": iterable, "body": self.parse_block()})

        init = None if self.check(TT.SEMICOLON) else self.parse_expression()
        self.expect(TT.SEMICOLON, message="Expected ';' after for initializer")
        condition = None if self.check(TT.SEMICOLON) else self.parse_expression()
        self.expect(TT.SEMICOLON, message="Expected ';' after for condition")
        update = None if self.check(TT.RPAREN) else self.parse_expression()
        self.expect(TT.RPAREN, message="Expected ')' after for clauses")

        return Node("ForStmt", {
            "init": init,
            "condition": condition,
            "update": update,
            "body": self.parse_block(),
        })

    def peek_keyword(self, lexeme):
        return (
            self.pos + 1 < len(self.tokens)
            and self.tokens[self.pos + 1].ttype == TT.KEYWORD
            and self.tokens[self.pos + 1].lexeme == lexeme
        )

    def parse_while_stmt(self):
        self.expect_keyword("while")
        return Node("WhileStmt", {
            "condition": self.parse_parenthesized_expression("while condition"),
            "body": self.parse_block(),
        })

    def parse_repeat_stmt(self):
        self.expect_keyword("repeat")
        body = self.parse_block()
        self.expect_keyword("until", "Expected 'until' after repeat block")
        condition = self.parse_parenthesized_expression("until condition")
        self.expect(TT.SEMICOLON, message="Expected ';' after repeat-until")
        return Node("RepeatUntilStmt", {"body": body, "condition": condition})

    def parse_return_stmt(self):
        self.expect_keyword("return")
        values = []

        if not self.check(TT.SEMICOLON):
            values.append(self.parse_expression())
            while self.match(TT.COMMA):
                values.append(self.parse_expression())

        self.expect(TT.SEMICOLON, message="Expected ';' after return")
        return Node("ReturnStmt", {"values": values})

    def parse_loop_control_stmt(self):
        keyword = self.current().lexeme
        self.pos += 1
        self.expect(TT.SEMICOLON, message=f"Expected ';' after {keyword}")
        return Node("LoopControlStmt", {"keyword": keyword})

    def parse_builtin_stmt(self):
        name = self.current().lexeme
        self.pos += 1
        args = self.parse_argument_list()
        self.expect(TT.SEMICOLON, message=f"Expected ';' after {name} statement")
        return Node("BuiltinStmt", {"name": name, "args": args})

    def parse_match_statement(self):
        self.expect_keyword("match")
        subject = self.parse_parenthesized_expression("match subject")
        self.expect(TT.LBRACE, message="Expected '{' before match cases")
        cases = []
        seen_wildcard = False

        while not self.check(TT.RBRACE) and not self.at_end():
            if seen_wildcard:
                self.error("Unreachable match case after wildcard '_'")
            pattern = self.parse_pattern()
            self.expect(TT.MATCH_ARROW, message="Expected '=>' in match case")
            body = self.parse_statement()
            if pattern.kind == "WildcardPattern":
                seen_wildcard = True
            cases.append(Node("MatchCase", {"pattern": pattern, "body": body}))

        self.expect(TT.RBRACE, message="Expected '}' after match cases")
        return Node("MatchStmt", {"subject": subject, "cases": cases})

    def parse_guard_stmt(self):
        self.expect_keyword("guard")
        condition = self.parse_parenthesized_expression("guard condition")
        self.expect_keyword("else", "Expected 'else' after guard condition")
        return Node("GuardStmt", {"condition": condition, "else_body": self.parse_block()})

    def parse_try_stmt(self):
        self.expect_keyword("try")
        body = self.parse_block()
        catch_clauses = []
        finally_body = None

        while self.match_keyword("catch"):
            self.expect(TT.LPAREN, message="Expected '(' after catch")
            catch_name = self.expect(TT.IDENTIFIER, message="Expected catch variable").lexeme
            self.expect(TT.RPAREN, message="Expected ')' after catch variable")
            catch_clauses.append(Node("CatchClause", {"name": catch_name, "body": self.parse_block()}))

        if self.match_keyword("finally"):
            finally_body = self.parse_block()

        if not catch_clauses:
            self.error("Expected at least one catch clause after try block")

        return Node("TryStmt", {
            "body": body,
            "catch_clauses": catch_clauses,
            "finally_body": finally_body,
        })

    def parse_throw_stmt(self):
        self.expect_keyword("throw")
        value = self.parse_expression()
        self.expect(TT.SEMICOLON, message="Expected ';' after throw")
        return Node("ThrowStmt", {"value": value})

    def parse_expr_stmt(self):
        expr = self.parse_expression()
        self.expect(TT.SEMICOLON, message="Expected ';' after expression")
        return Node("ExprStmt", {"expression": expr})

    def parse_parenthesized_expression(self, label):
        self.expect(TT.LPAREN, message=f"Expected '(' before {label}")
        expr = self.parse_expression()
        self.expect(TT.RPAREN, message=f"Expected ')' after {label}")
        return expr

    def parse_argument_list(self):
        self.expect(TT.LPAREN, message="Expected '(' before arguments")
        return self._parse_call_args_inner()

    def _parse_call_args_inner(self):
        args = []
        if not self.check(TT.RPAREN):
            while True:
                args.append(self.parse_argument())
                if not self.match(TT.COMMA):
                    break
        self.expect(TT.RPAREN, message="Expected ')' after call arguments")
        return args

    def parse_argument(self):
        if (
            self.check(TT.IDENTIFIER)
            and self.pos + 1 < len(self.tokens)
            and self.tokens[self.pos + 1].ttype == TT.COLON
        ):
            name = self.current().lexeme
            self.pos += 2
            return Node("NamedArg", {"name": name, "value": self.parse_expression()})
        return self.parse_expression()

    def parse_pattern(self):
        if self.match(TT.UNDERSCORE):
            return Node("WildcardPattern")
        return self.parse_expression()

    def parse_expression(self):
        return self.parse_assignment()

    def parse_assignment(self):
        expr = self.parse_or()
        if self.match(TT.ASSIGN_OP):
            return Node("AssignExpr", {"target": expr, "value": self.parse_assignment()})
        return expr

    def parse_or(self):
        expr = self.parse_and()
        while self.match(TT.LOGIC_OP, "||"):
            expr = Node("BinaryExpr", {"op": "||", "left": expr, "right": self.parse_and()})
        return expr

    def parse_and(self):
        expr = self.parse_equality()
        while self.match(TT.LOGIC_OP, "&&"):
            expr = Node("BinaryExpr", {"op": "&&", "left": expr, "right": self.parse_equality()})
        return expr

    def parse_equality(self):
        expr = self.parse_comparison()
        while self.check(TT.REL_OP) and self.current().lexeme in {"==", "!="}:
            op = self.current().lexeme
            self.pos += 1
            expr = Node("BinaryExpr", {"op": op, "left": expr, "right": self.parse_comparison()})
        return expr

    def parse_comparison(self):
        expr = self.parse_range()
        while self.check(TT.REL_OP) and self.current().lexeme in {"<", ">", "<=", ">="}:
            op = self.current().lexeme
            self.pos += 1
            expr = Node("BinaryExpr", {"op": op, "left": expr, "right": self.parse_range()})
        return expr

    def parse_range(self):
        expr = self.parse_term()
        while self.match(TT.RANGE_OP):
            right = self.parse_term()
            if not (isinstance(expr, Node) and expr.kind == "Literal" and expr.fields.get("token_type") == TT.INTEGER_LIT):
                self.error("Range operands must be integer literals")
            if not (isinstance(right, Node) and right.kind == "Literal" and right.fields.get("token_type") == TT.INTEGER_LIT):
                self.error("Range operands must be integer literals")
            expr = Node("RangeExpr", {"start": expr, "end": right})
        return expr

    def parse_term(self):
        expr = self.parse_factor()
        while self.check(TT.ARITH_OP) and self.current().lexeme in {"+", "-"}:
            op = self.current().lexeme
            self.pos += 1
            expr = Node("BinaryExpr", {"op": op, "left": expr, "right": self.parse_factor()})
        return expr

    def parse_factor(self):
        expr = self.parse_unary()
        while self.check(TT.ARITH_OP) and self.current().lexeme in {"*", "/", "%"}:
            op = self.current().lexeme
            self.pos += 1
            expr = Node("BinaryExpr", {"op": op, "left": expr, "right": self.parse_unary()})
        return expr

    def parse_unary(self):
        if (
            self.check(TT.LOGIC_OP, "!")
            or self.check(TT.ARITH_OP, "-")
            or self.check(TT.ARITH_OP, "+")
            or self.check(TT.ARITH_OP, "*")
            or self.check(TT.ADDR_OP)
        ):
            op = self.current().lexeme
            self.pos += 1
            return Node("UnaryExpr", {"op": op, "operand": self.parse_unary()})
        return self.parse_postfix()

    def parse_postfix(self):
        expr = self.parse_primary()

        while True:
            if self.match(TT.LPAREN):
                expr = Node("CallExpr", {"callee": expr, "args": self._parse_call_args_inner()})
            elif self.match(TT.LBRACKET):
                index = self.parse_expression()
                self.expect(TT.RBRACKET, message="Expected ']' after index")
                expr = Node("IndexExpr", {"object": expr, "index": index})
            elif self.match(TT.DOT):
                field = self.expect(TT.IDENTIFIER, message="Expected field name after '.'").lexeme
                expr = Node("MemberExpr", {"object": expr, "field": field, "op": "."})
            elif self.match(TT.STRUCT_PTR):
                field = self.expect(TT.IDENTIFIER, message="Expected field name after '->'").lexeme
                expr = Node("MemberExpr", {"object": expr, "field": field, "op": "->"})
            else:
                break

        return expr

    def parse_primary(self):
        tok = self.current()

        if tok.ttype == TT.ERROR:
            self.pos += 1   # consume so the parser advances past the bad token
            raise RuntimeError()  # triggers synchronize(); lex error already recorded

        if tok.ttype in {
            TT.INTEGER_LIT,
            TT.FLOAT_LIT,
            TT.STRING_LIT,
            TT.CHAR_LIT,
            TT.INTERP_STRING,
            TT.BOOL_LIT,
        }:
            self.pos += 1
            return Node("Literal", {
                "token_type": tok.ttype,
                "lexeme": tok.lexeme,
                "value": tok.attr,
            })

        if tok.ttype == TT.IDENTIFIER:
            self.pos += 1
            return Node("Identifier", {"name": tok.lexeme})

        if self.match(TT.LPAREN):
            expr = self.parse_expression()
            self.expect(TT.RPAREN, message="Expected ')' after expression")
            return Node("Grouping", {"expression": expr})

        if self.check_keyword("match"):
            return self.parse_match_expression()

        self.error("Expected expression")
        raise RuntimeError()

    def parse_match_expression(self):
        self.expect_keyword("match")
        subject = self.parse_parenthesized_expression("match subject")
        self.expect(TT.LBRACE, message="Expected '{' before match expression cases")
        cases = []
        seen_wildcard = False

        while not self.check(TT.RBRACE) and not self.at_end():
            if seen_wildcard:
                self.error("Unreachable match expression case after wildcard '_'")
            pattern = self.parse_pattern()
            self.expect(TT.MATCH_ARROW, message="Expected '=>' in match expression case")
            value = self.parse_expression()
            if pattern.kind == "WildcardPattern":
                seen_wildcard = True
            cases.append(Node("MatchCase", {"pattern": pattern, "value": value}))
            self.match(TT.COMMA)

        self.expect(TT.RBRACE, message="Expected '}' after match expression")
        return Node("MatchExpr", {"subject": subject, "cases": cases})


def merge_type(base, declarator):
    typ = base

    if declarator["pointer_depth"]:
        typ = Node("PointerType", {"base": typ, "depth": declarator["pointer_depth"]})

    for size in declarator["arrays"]:
        typ = Node("ArrayType", {"base": typ, "size": size})

    return typ


def parse_source(source):
    scanner = Scanner(source)
    tokens, lex_errors = scanner.scan_all()
    parser = Parser(tokens)
    ast = parser.parse()
    return ast, parser.errors, lex_errors


def format_parse_report(filename, ast, syntax_errors, lex_errors, elapsed_ms, show_ast):
    lines = [
        f"[Parser] {filename}",
        f"  Lexical errors : {len(lex_errors)}",
        f"  Syntax errors  : {len(syntax_errors)}",
        f"  Parse time     : {elapsed_ms:.3f} ms",
    ]

    if lex_errors:
        lines.append("\nLexical errors:")
        lines.extend(f"  {err}" for err in lex_errors)

    if syntax_errors:
        lines.append("\nSyntax errors:")
        lines.extend(f"  {err}" for err in syntax_errors)

    if show_ast:
        lines.append("\nAST:")
        lines.append(json.dumps(ast.to_dict(), indent=2))
    elif not lex_errors and not syntax_errors:
        lines.append("  Result         : syntax accepted")

    return "\n".join(lines) + "\n"


def main():
    cli = argparse.ArgumentParser(description="CSC617M Custom Language Parser")
    cli.add_argument("source_file", help="Path to source file to parse")
    cli.add_argument("-o", "--output", help="Write parse report to this file")
    cli.add_argument("--ast", action="store_true", help="Include the parsed AST as JSON")
    args = cli.parse_args()

    try:
        with open(args.source_file, "r", encoding="utf-8") as f:
            source = f.read()
    except FileNotFoundError:
        print(f"Error: file not found: {args.source_file}", file=sys.stderr)
        sys.exit(1)

    start = time.perf_counter()
    ast, syntax_errors, lex_errors = parse_source(source)
    elapsed_ms = (time.perf_counter() - start) * 1000

    report = format_parse_report(
        os.path.basename(args.source_file),
        ast,
        syntax_errors,
        lex_errors,
        elapsed_ms,
        args.ast,
    )

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"[Parser] {os.path.basename(args.source_file)}")
        print(f"  Lexical errors : {len(lex_errors)}")
        print(f"  Syntax errors  : {len(syntax_errors)}")
        print(f"  Output         : {args.output}")
    else:
        print(report)

    if lex_errors or syntax_errors:
        sys.exit(2)


if __name__ == "__main__":
    main()