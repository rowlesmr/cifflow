// CIF Lexer — direct port of src/pycifparse/lexer/lexer.py.
//
// Works on a Vec<char> of the (already line-ending-normalised) source so that
// peek(offset) is O(1).  Line endings must already be normalised to '\n' by
// the caller (version::detect_version hands a normalised string to the lexer).

use crate::error::LexerError;
use crate::version::CifVersion;

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum TokenType {
    Tag,
    Keyword,
    Value,
}

#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum ValueType {
    MultilineString    = 0,
    TripleDoubleQuoted = 1,
    TripleSingleQuoted = 2,
    DoubleQuoted       = 3,
    SingleQuoted       = 4,
    String             = 5,
    Placeholder        = 6,
}

impl ValueType {
    // Name of the corresponding Python ValueType enum member.
    pub fn python_attr(&self) -> &'static str {
        match self {
            ValueType::MultilineString    => "MULTILINE_STRING",
            ValueType::TripleDoubleQuoted => "TRIPLE_DOUBLE_QUOTED",
            ValueType::TripleSingleQuoted => "TRIPLE_SINGLE_QUOTED",
            ValueType::DoubleQuoted       => "DOUBLE_QUOTED",
            ValueType::SingleQuoted       => "SINGLE_QUOTED",
            ValueType::String             => "STRING",
            ValueType::Placeholder        => "PLACEHOLDER",
        }
    }
}

#[derive(Debug, Clone)]
pub struct Token {
    pub token_type: TokenType,
    pub value: String,
    pub value_type: Option<ValueType>, // None for Tag and Keyword
    pub line: u32,
    pub column: u32,
    pub errors: Vec<LexerError>,
}

const PREFIX_KEYWORDS: &[&str] = &["data_", "save_"];
const EXACT_KEYWORDS: &[&str]  = &["loop_", "stop_", "global_"];
const CIF2_DELIMITERS: &[char]  = &['[', ']', '{', '}'];

fn classify_bare_word(word: &str) -> (TokenType, Option<ValueType>) {
    if word.starts_with('_') {
        return (TokenType::Tag, None);
    }
    let lower = word.to_ascii_lowercase();
    if EXACT_KEYWORDS.contains(&lower.as_str()) {
        return (TokenType::Keyword, None);
    }
    for prefix in PREFIX_KEYWORDS {
        if lower.starts_with(prefix) {
            return (TokenType::Keyword, None);
        }
    }
    if word == "." || word == "?" {
        return (TokenType::Value, Some(ValueType::Placeholder));
    }
    (TokenType::Value, Some(ValueType::String))
}

fn check_cif1_char(ch: char, line: u32, col: u32) -> Option<LexerError> {
    let code = ch as u32;
    if code == 9 || code == 10 || code == 13 {
        return None; // HT, LF, CR
    }
    if (32..=126).contains(&code) {
        return None; // printable ASCII
    }
    Some(LexerError {
        message: format!("character U+{code:04X} is not permitted in CIF 1.x"),
        line,
        column: col,
        context: ch.to_string(),
    })
}

pub struct Lexer {
    src: Vec<char>,
    is_cif2: bool,
    pos: usize,
    line: u32,
    col: u32,
    last_was_ws: bool,
}

impl Lexer {
    pub fn new(source: &str, version: CifVersion, line_offset: u32) -> Self {
        // Normalise line endings (source should already be normalised by
        // version detection, but be defensive).
        let normalised: String = source.replace("\r\n", "\n").replace('\r', "\n");
        Lexer {
            src: normalised.chars().collect(),
            is_cif2: version == CifVersion::Cif2_0,
            pos: 0,
            line: line_offset + 1,
            col: 1,
            last_was_ws: true,
        }
    }

    fn peek(&self, offset: usize) -> char {
        self.src.get(self.pos + offset).copied().unwrap_or('\0')
    }

    fn advance(&mut self) -> char {
        let ch = self.src[self.pos];
        self.pos += 1;
        if ch == '\n' {
            self.line += 1;
            self.col = 1;
        } else {
            self.col += 1;
        }
        ch
    }

    fn at_end(&self) -> bool {
        self.pos >= self.src.len()
    }

    fn skip_to_eol(&mut self) {
        while !self.at_end() && self.peek(0) != '\n' {
            self.advance();
        }
    }

    pub fn tokenise(mut self) -> Vec<Token> {
        let mut tokens = Vec::new();
        while !self.at_end() {
            let ch = self.peek(0);

            // Inline whitespace
            if ch == ' ' || ch == '\t' {
                self.advance();
                self.last_was_ws = true;
                continue;
            }

            // Line terminator
            if ch == '\n' {
                self.advance();
                self.last_was_ws = true;
                continue;
            }

            // Comment
            if ch == '#' {
                self.skip_to_eol();
                self.last_was_ws = true;
                continue;
            }

            // Multiline text field: ';' at column 1
            if ch == ';' && self.col == 1 {
                let t = self.read_multiline();
                tokens.push(t);
                self.last_was_ws = false;
                continue;
            }

            // Triple-quoted strings (CIF 2.0 only)
            if ch == '\'' && self.is_cif2 && self.peek(1) == '\'' && self.peek(2) == '\'' {
                let t = self.read_triple('\'');
                tokens.push(t);
                self.last_was_ws = false;
                continue;
            }
            if ch == '"' && self.is_cif2 && self.peek(1) == '"' && self.peek(2) == '"' {
                let t = self.read_triple('"');
                tokens.push(t);
                self.last_was_ws = false;
                continue;
            }

            // Single / double quoted strings
            if ch == '\'' {
                if !self.is_cif2 && self.peek(1) == '\'' && self.peek(2) == '\'' {
                    let t = self.read_triple_cif1('\'');
                    tokens.push(t);
                } else {
                    let t = self.read_quoted('\'');
                    tokens.push(t);
                }
                self.last_was_ws = false;
                continue;
            }
            if ch == '"' {
                if !self.is_cif2 && self.peek(1) == '"' && self.peek(2) == '"' {
                    let t = self.read_triple_cif1('"');
                    tokens.push(t);
                } else {
                    let t = self.read_quoted('"');
                    tokens.push(t);
                }
                self.last_was_ws = false;
                continue;
            }

            // CIF 2.0 structural delimiters
            if self.is_cif2 && CIF2_DELIMITERS.contains(&ch) {
                let line = self.line;
                let col  = self.col;
                self.advance();
                tokens.push(Token {
                    token_type: TokenType::Value,
                    value: ch.to_string(),
                    value_type: Some(ValueType::String),
                    line, column: col, errors: vec![],
                });
                self.last_was_ws = false;
                continue;
            }

            // CIF 2.0 table separator ':'
            if self.is_cif2 && ch == ':' && !self.last_was_ws {
                let line = self.line;
                let col  = self.col;
                self.advance();
                tokens.push(Token {
                    token_type: TokenType::Value,
                    value: ":".to_string(),
                    value_type: Some(ValueType::String),
                    line, column: col, errors: vec![],
                });
                self.last_was_ws = false;
                continue;
            }

            // Bare word
            let t = self.read_bare_word();
            tokens.push(t);
            self.last_was_ws = false;
        }
        tokens
    }

    fn read_bare_word(&mut self) -> Token {
        let line = self.line;
        let col  = self.col;
        let mut buf = String::new();

        while !self.at_end() {
            let ch = self.peek(0);
            if ch == ' ' || ch == '\t' || ch == '\n' { break; }
            if ch == '#' { break; }
            if ch == '\'' || ch == '"' { break; }
            if self.is_cif2 && CIF2_DELIMITERS.contains(&ch) {
                if buf.is_empty() { break; }
                let lower = buf.to_ascii_lowercase();
                if !lower.starts_with('_')
                    && !PREFIX_KEYWORDS.iter().any(|p| lower.starts_with(p))
                {
                    break;
                }
            }
            buf.push(self.advance());
        }

        if buf.is_empty() {
            let bad = self.advance();
            return Token {
                token_type: TokenType::Value,
                value: bad.to_string(),
                value_type: Some(ValueType::String),
                line, column: col,
                errors: vec![LexerError {
                    message: format!("unexpected character: {bad:?}"),
                    line, column: col,
                    context: bad.to_string(),
                }],
            };
        }

        let (token_type, value_type) = classify_bare_word(&buf);
        let mut errors = Vec::new();
        if !self.is_cif2 {
            if let Some(first) = buf.chars().next() {
                if first == '[' || first == '$' {
                    errors.push(LexerError {
                        message: format!(
                            "bare word beginning with {:?} is not permitted in CIF 1.x",
                            first
                        ),
                        line, column: col, context: first.to_string(),
                    });
                }
            }
        }

        Token { token_type, value: buf, value_type, line, column: col, errors }
    }

    fn read_quoted(&mut self, delimiter: char) -> Token {
        let line = self.line;
        let col  = self.col;
        let vtype = if delimiter == '\'' { ValueType::SingleQuoted } else { ValueType::DoubleQuoted };
        let mut errors = Vec::new();
        self.advance(); // consume opening delimiter
        let mut buf = String::new();

        while !self.at_end() {
            let ch = self.peek(0);

            if ch == '\n' {
                errors.push(LexerError {
                    message: format!("unterminated {} string", vtype.python_attr().to_lowercase().replace('_', " ")),
                    line, column: col,
                    context: format!("{}{}", delimiter, &buf[..buf.len().min(40)]),
                });
                return Token { token_type: TokenType::Value, value: buf, value_type: Some(vtype), line, column: col, errors };
            }

            if ch == delimiter {
                if !self.is_cif2 {
                    let following = self.peek(1);
                    if following != ' ' && following != '\t' && following != '\n' && following != '\0' {
                        if let Some(err) = check_cif1_char(ch, self.line, self.col) {
                            errors.push(err);
                        }
                        buf.push(self.advance());
                        continue;
                    }
                }
                self.advance(); // consume closing delimiter
                return Token { token_type: TokenType::Value, value: buf, value_type: Some(vtype), line, column: col, errors };
            }

            if !self.is_cif2 {
                if let Some(err) = check_cif1_char(ch, self.line, self.col) {
                    errors.push(err);
                }
            }
            buf.push(self.advance());
        }

        // EOF inside quoted string
        errors.push(LexerError {
            message: format!("unterminated {} string", vtype.python_attr().to_lowercase().replace('_', " ")),
            line, column: col,
            context: format!("{}{}", delimiter, &buf[..buf.len().min(40)]),
        });
        Token { token_type: TokenType::Value, value: buf, value_type: Some(vtype), line, column: col, errors }
    }

    fn read_triple(&mut self, delimiter: char) -> Token {
        let line = self.line;
        let col  = self.col;
        let vtype = if delimiter == '\'' {
            ValueType::TripleSingleQuoted
        } else {
            ValueType::TripleDoubleQuoted
        };
        let mut errors = Vec::new();
        for _ in 0..3 { self.advance(); } // consume opening triple
        let mut buf = String::new();

        while !self.at_end() {
            if self.peek(0) == delimiter && self.peek(1) == delimiter && self.peek(2) == delimiter {
                for _ in 0..3 { self.advance(); } // consume closing triple
                return Token { token_type: TokenType::Value, value: buf, value_type: Some(vtype), line, column: col, errors };
            }
            buf.push(self.advance());
        }

        errors.push(LexerError {
            message: format!("unterminated {} string", vtype.python_attr().to_lowercase().replace('_', " ")),
            line, column: col,
            context: format!("{}{}{}{}", delimiter, delimiter, delimiter, &buf[..buf.len().min(40)]),
        });
        Token { token_type: TokenType::Value, value: buf, value_type: Some(vtype), line, column: col, errors }
    }

    fn read_triple_cif1(&mut self, delimiter: char) -> Token {
        let line = self.line;
        let col  = self.col;
        let triple = format!("{delimiter}{delimiter}{delimiter}");
        let mut errors = vec![LexerError {
            message: "triple-quoted strings are not valid in CIF 1.x".to_string(),
            line, column: col,
            context: triple.clone(),
        }];
        for _ in 0..3 { self.advance(); }
        let mut buf = String::new();

        while !self.at_end() {
            if self.peek(0) == delimiter && self.peek(1) == delimiter && self.peek(2) == delimiter {
                for _ in 0..3 { self.advance(); }
                return Token { token_type: TokenType::Value, value: buf, value_type: Some(ValueType::String), line, column: col, errors };
            }
            buf.push(self.advance());
        }

        errors.push(LexerError {
            message: "unterminated triple-quoted string".to_string(),
            line, column: col,
            context: format!("{triple}{}", &buf[..buf.len().min(40)]),
        });
        Token { token_type: TokenType::Value, value: buf, value_type: Some(ValueType::String), line, column: col, errors }
    }

    pub fn read_multiline(&mut self) -> Token {
        debug_assert!(self.col == 1 && self.peek(0) == ';');
        let line = self.line;
        let col  = self.col;
        let mut errors = Vec::new();
        self.advance(); // consume opening ';'
        let mut buf = String::new();

        while !self.at_end() {
            let ch = self.peek(0);
            if ch == '\n' {
                if self.peek(1) == ';' {
                    self.advance(); // consume '\n'
                    self.advance(); // consume closing ';'
                    return Token {
                        token_type: TokenType::Value,
                        value: buf,
                        value_type: Some(ValueType::MultilineString),
                        line, column: col, errors,
                    };
                } else {
                    buf.push(self.advance());
                }
            } else {
                if !self.is_cif2 {
                    if let Some(err) = check_cif1_char(ch, self.line, self.col) {
                        errors.push(err);
                    }
                }
                buf.push(self.advance());
            }
        }

        errors.push(LexerError {
            message: "unterminated multiline string".to_string(),
            line, column: col, context: ";".to_string(),
        });
        Token {
            token_type: TokenType::Value,
            value: buf,
            value_type: Some(ValueType::MultilineString),
            line, column: col, errors,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn lex2(src: &str) -> Vec<Token> { Lexer::new(src, CifVersion::Cif2_0, 0).tokenise() }
    fn lex1(src: &str) -> Vec<Token> { Lexer::new(src, CifVersion::Cif1_1, 0).tokenise() }

    // ── helpers ───────────────────────────────────────────────────────────────

    fn ttype(t: &Token) -> TokenType { t.token_type }
    fn vtype(t: &Token) -> Option<ValueType> { t.value_type }
    fn val(t: &Token) -> &str { &t.value }
    fn has_errors(t: &Token) -> bool { !t.errors.is_empty() }

    // ── empty / whitespace / comments ─────────────────────────────────────────

    #[test]
    fn empty_source() {
        assert!(lex2("").is_empty());
    }

    #[test]
    fn whitespace_only() {
        assert!(lex2("   \t  \n  \n").is_empty());
    }

    #[test]
    fn comment_only() {
        assert!(lex2("# this is a comment").is_empty());
    }

    #[test]
    fn comment_stripped_before_token() {
        let toks = lex2("# comment\n_tag");
        assert_eq!(toks.len(), 1);
        assert_eq!(ttype(&toks[0]), TokenType::Tag);
        assert_eq!(val(&toks[0]), "_tag");
    }

    #[test]
    fn inline_comment_after_token() {
        let toks = lex2("_tag # rest is comment");
        assert_eq!(toks.len(), 1);
        assert_eq!(ttype(&toks[0]), TokenType::Tag);
    }

    // ── tags ──────────────────────────────────────────────────────────────────

    #[test]
    fn tag_basic() {
        let toks = lex2("_atom_site.x");
        assert_eq!(toks.len(), 1);
        assert_eq!(ttype(&toks[0]), TokenType::Tag);
        assert_eq!(vtype(&toks[0]), None);
        assert_eq!(val(&toks[0]), "_atom_site.x");
    }

    #[test]
    fn tag_underscore_prefix_not_keyword() {
        // _data_block starts with _ so it is a Tag, not a Keyword
        let toks = lex2("_data_block");
        assert_eq!(toks.len(), 1);
        assert_eq!(ttype(&toks[0]), TokenType::Tag);
    }

    #[test]
    fn tag_single_underscore() {
        let toks = lex2("_");
        assert_eq!(ttype(&toks[0]), TokenType::Tag);
        assert_eq!(val(&toks[0]), "_");
    }

    // ── keywords ──────────────────────────────────────────────────────────────

    #[test]
    fn keyword_data() {
        let toks = lex2("data_myblock");
        assert_eq!(ttype(&toks[0]), TokenType::Keyword);
        assert_eq!(val(&toks[0]), "data_myblock");
    }

    #[test]
    fn keyword_data_empty_suffix() {
        let toks = lex2("data_");
        assert_eq!(ttype(&toks[0]), TokenType::Keyword);
    }

    #[test]
    fn keyword_loop_lowercase() {
        let toks = lex2("loop_");
        assert_eq!(ttype(&toks[0]), TokenType::Keyword);
    }

    #[test]
    fn keyword_loop_uppercase() {
        let toks = lex2("LOOP_");
        assert_eq!(ttype(&toks[0]), TokenType::Keyword);
    }

    #[test]
    fn keyword_loop_mixed_case() {
        let toks = lex2("Loop_");
        assert_eq!(ttype(&toks[0]), TokenType::Keyword);
    }

    #[test]
    fn keyword_stop() {
        let toks = lex2("stop_");
        assert_eq!(ttype(&toks[0]), TokenType::Keyword);
    }

    #[test]
    fn keyword_global() {
        let toks = lex2("global_");
        assert_eq!(ttype(&toks[0]), TokenType::Keyword);
    }

    #[test]
    fn keyword_save_open() {
        let toks = lex2("save_myframe");
        assert_eq!(ttype(&toks[0]), TokenType::Keyword);
        assert_eq!(val(&toks[0]), "save_myframe");
    }

    #[test]
    fn keyword_save_close() {
        let toks = lex2("save_");
        assert_eq!(ttype(&toks[0]), TokenType::Keyword);
    }

    // ── bare-word values ───────────────────────────────────────────────────────

    #[test]
    fn value_string_bare() {
        let toks = lex2("hello");
        assert_eq!(ttype(&toks[0]), TokenType::Value);
        assert_eq!(vtype(&toks[0]), Some(ValueType::String));
        assert_eq!(val(&toks[0]), "hello");
    }

    #[test]
    fn value_placeholder_dot() {
        let toks = lex2(".");
        assert_eq!(ttype(&toks[0]), TokenType::Value);
        assert_eq!(vtype(&toks[0]), Some(ValueType::Placeholder));
        assert_eq!(val(&toks[0]), ".");
    }

    #[test]
    fn value_placeholder_question() {
        let toks = lex2("?");
        assert_eq!(ttype(&toks[0]), TokenType::Value);
        assert_eq!(vtype(&toks[0]), Some(ValueType::Placeholder));
    }

    #[test]
    fn value_numeric() {
        let toks = lex2("42");
        assert_eq!(ttype(&toks[0]), TokenType::Value);
        assert_eq!(vtype(&toks[0]), Some(ValueType::String));
    }

    #[test]
    fn value_float_with_su() {
        let toks = lex2("1.23(5)");
        assert_eq!(ttype(&toks[0]), TokenType::Value);
        assert_eq!(vtype(&toks[0]), Some(ValueType::String));
        assert_eq!(val(&toks[0]), "1.23(5)");
    }

    #[test]
    fn value_negative_number() {
        let toks = lex2("-12.3");
        assert_eq!(vtype(&toks[0]), Some(ValueType::String));
        assert_eq!(val(&toks[0]), "-12.3");
    }

    // ── quoted strings ────────────────────────────────────────────────────────

    #[test]
    fn single_quoted_basic() {
        let toks = lex2("'hello world'");
        assert_eq!(ttype(&toks[0]), TokenType::Value);
        assert_eq!(vtype(&toks[0]), Some(ValueType::SingleQuoted));
        assert_eq!(val(&toks[0]), "hello world");
        assert!(!has_errors(&toks[0]));
    }

    #[test]
    fn double_quoted_basic() {
        let toks = lex2("\"hello world\"");
        assert_eq!(vtype(&toks[0]), Some(ValueType::DoubleQuoted));
        assert_eq!(val(&toks[0]), "hello world");
    }

    #[test]
    fn single_quoted_empty() {
        let toks = lex2("''");
        assert_eq!(vtype(&toks[0]), Some(ValueType::SingleQuoted));
        assert_eq!(val(&toks[0]), "");
    }

    #[test]
    fn quoted_dot_is_not_placeholder() {
        let toks = lex2("'.'");
        assert_eq!(ttype(&toks[0]), TokenType::Value);
        assert_eq!(vtype(&toks[0]), Some(ValueType::SingleQuoted));
        assert_eq!(val(&toks[0]), ".");
    }

    #[test]
    fn quoted_question_is_not_placeholder() {
        let toks = lex2("\"?\"");
        assert_eq!(vtype(&toks[0]), Some(ValueType::DoubleQuoted));
        assert_eq!(val(&toks[0]), "?");
    }

    #[test]
    fn quoted_keyword_is_value() {
        let toks = lex2("'loop_'");
        assert_eq!(ttype(&toks[0]), TokenType::Value);
        assert_eq!(vtype(&toks[0]), Some(ValueType::SingleQuoted));
    }

    #[test]
    fn single_quoted_unterminated_at_newline() {
        let toks = lex1("'hello\nnext");
        assert_eq!(vtype(&toks[0]), Some(ValueType::SingleQuoted));
        assert_eq!(val(&toks[0]), "hello");
        assert!(has_errors(&toks[0]));
    }

    #[test]
    fn double_quoted_unterminated_at_eof() {
        let toks = lex2("\"hello");
        assert_eq!(vtype(&toks[0]), Some(ValueType::DoubleQuoted));
        assert_eq!(val(&toks[0]), "hello");
        assert!(has_errors(&toks[0]));
    }

    // ── triple-quoted strings (CIF 2.0) ───────────────────────────────────────

    #[test]
    fn triple_single_quoted_cif2() {
        let toks = lex2("'''hello'''");
        assert_eq!(vtype(&toks[0]), Some(ValueType::TripleSingleQuoted));
        assert_eq!(val(&toks[0]), "hello");
        assert!(!has_errors(&toks[0]));
    }

    #[test]
    fn triple_double_quoted_cif2() {
        let toks = lex2("\"\"\"hello\"\"\"");
        assert_eq!(vtype(&toks[0]), Some(ValueType::TripleDoubleQuoted));
        assert_eq!(val(&toks[0]), "hello");
    }

    #[test]
    fn triple_quoted_contains_newline() {
        let toks = lex2("'''line1\nline2'''");
        assert_eq!(vtype(&toks[0]), Some(ValueType::TripleSingleQuoted));
        assert_eq!(val(&toks[0]), "line1\nline2");
    }

    #[test]
    fn triple_quoted_contains_single_quotes() {
        let toks = lex2("'''it's fine'''");
        assert_eq!(vtype(&toks[0]), Some(ValueType::TripleSingleQuoted));
        assert_eq!(val(&toks[0]), "it's fine");
    }

    #[test]
    fn triple_quoted_semicolon_not_multiline() {
        // semicolon inside triple-quoted string is NOT a multiline delimiter
        let toks = lex2("'''\n;\ncontent\n;'''");
        assert_eq!(vtype(&toks[0]), Some(ValueType::TripleSingleQuoted));
        assert_eq!(val(&toks[0]), "\n;\ncontent\n;");
    }

    #[test]
    fn triple_single_quoted_cif1_is_error() {
        let toks = lex1("'''hello'''");
        assert_eq!(ttype(&toks[0]), TokenType::Value);
        assert_eq!(vtype(&toks[0]), Some(ValueType::String));
        assert!(has_errors(&toks[0]));
        assert!(toks[0].errors[0].message.contains("not valid in CIF 1.x"));
    }

    #[test]
    fn triple_double_quoted_cif1_is_error() {
        let toks = lex1("\"\"\"hello\"\"\"");
        assert_eq!(vtype(&toks[0]), Some(ValueType::String));
        assert!(has_errors(&toks[0]));
    }

    #[test]
    fn triple_unterminated_at_eof() {
        let toks = lex2("'''hello");
        assert_eq!(vtype(&toks[0]), Some(ValueType::TripleSingleQuoted));
        assert!(has_errors(&toks[0]));
    }

    // ── multiline strings ─────────────────────────────────────────────────────

    #[test]
    fn multiline_basic() {
        // ;\nhello\n; — content starts after opening ;, ends before closing \n;
        let toks = lex2(";\nhello\n;");
        assert_eq!(toks.len(), 1);
        assert_eq!(vtype(&toks[0]), Some(ValueType::MultilineString));
        assert_eq!(val(&toks[0]), "\nhello");
        assert!(!has_errors(&toks[0]));
    }

    #[test]
    fn multiline_content_on_same_line_as_opener() {
        // ;text\n; — content starts immediately after opener
        let toks = lex2(";text\n;");
        assert_eq!(vtype(&toks[0]), Some(ValueType::MultilineString));
        assert_eq!(val(&toks[0]), "text");
    }

    #[test]
    fn multiline_multiple_lines() {
        let toks = lex2(";\nline1\nline2\n;");
        assert_eq!(vtype(&toks[0]), Some(ValueType::MultilineString));
        assert_eq!(val(&toks[0]), "\nline1\nline2");
    }

    #[test]
    fn multiline_semicolon_not_at_column_1() {
        // Space before ; means it is NOT a multiline opener
        let toks = lex2(" ;content\n;");
        assert!(toks.len() > 1 || ttype(&toks[0]) != TokenType::Value
            || vtype(&toks[0]) != Some(ValueType::MultilineString));
    }

    #[test]
    fn multiline_unterminated_at_eof() {
        let toks = lex2(";\nhello");
        assert_eq!(vtype(&toks[0]), Some(ValueType::MultilineString));
        assert!(has_errors(&toks[0]));
        assert!(toks[0].errors.iter().any(|e| e.message.contains("unterminated")));
    }

    // ── CIF 2.0 structural delimiters ────────────────────────────────────────

    #[test]
    fn cif2_list_open_close() {
        let toks = lex2("[ ]");
        assert_eq!(toks.len(), 2);
        assert_eq!(val(&toks[0]), "[");
        assert_eq!(val(&toks[1]), "]");
    }

    #[test]
    fn cif2_table_open_close() {
        let toks = lex2("{ }");
        assert_eq!(toks.len(), 2);
        assert_eq!(val(&toks[0]), "{");
        assert_eq!(val(&toks[1]), "}");
    }

    #[test]
    fn cif2_table_colon_separator() {
        // Colon after a non-whitespace character is a table separator in CIF 2
        let toks = lex2("\"key\":");
        // "key" token, then : token
        assert_eq!(toks.len(), 2);
        assert_eq!(val(&toks[1]), ":");
    }

    #[test]
    fn cif1_brackets_are_bare_words() {
        // In CIF 1.x, [ is not a structural delimiter
        let toks = lex1("[foo]");
        assert_eq!(toks.len(), 1);
        assert_eq!(ttype(&toks[0]), TokenType::Value);
        assert_eq!(val(&toks[0]), "[foo]");
    }

    // ── multiple tokens / whitespace separation ───────────────────────────────

    #[test]
    fn multiple_tokens_space_separated() {
        let toks = lex2("data_blk _tag val");
        assert_eq!(toks.len(), 3);
        assert_eq!(ttype(&toks[0]), TokenType::Keyword);
        assert_eq!(ttype(&toks[1]), TokenType::Tag);
        assert_eq!(ttype(&toks[2]), TokenType::Value);
    }

    #[test]
    fn tab_is_whitespace() {
        let toks = lex2("_tag\tval");
        assert_eq!(toks.len(), 2);
    }

    #[test]
    fn newline_is_whitespace() {
        let toks = lex2("_tag\nval");
        assert_eq!(toks.len(), 2);
    }

    // ── position tracking ─────────────────────────────────────────────────────

    #[test]
    fn line_and_column_first_token() {
        let toks = lex2("_tag");
        assert_eq!(toks[0].line, 1);
        assert_eq!(toks[0].column, 1);
    }

    #[test]
    fn line_tracking_across_newlines() {
        let toks = lex2("_a\n_b\n_c");
        assert_eq!(toks[0].line, 1);
        assert_eq!(toks[1].line, 2);
        assert_eq!(toks[2].line, 3);
    }

    #[test]
    fn column_tracking_on_same_line() {
        let toks = lex2("_a val");
        assert_eq!(toks[0].column, 1);
        assert_eq!(toks[1].column, 4); // after "_a "
    }

    // ── CIF 1.x character restrictions ───────────────────────────────────────

    #[test]
    fn cif1_multiline_rejects_high_unicode() {
        // Unicode beyond ASCII triggers a LexerError in CIF 1.x multiline fields
        let toks = lex1(";\ncaf\u{00E9}\n;");
        assert_eq!(vtype(&toks[0]), Some(ValueType::MultilineString));
        assert!(has_errors(&toks[0]));
        assert!(toks[0].errors[0].message.contains("not permitted in CIF 1.x"));
    }

    #[test]
    fn cif2_allows_unicode() {
        let toks = lex2(";\ncaf\u{00E9}\n;");
        assert!(!has_errors(&toks[0]));
    }
}
