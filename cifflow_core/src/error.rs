#[derive(Debug, Clone)]
pub struct LexerError {
    pub message: String,
    pub line: u32,
    pub column: u32,
    pub context: String,
}

#[derive(Debug, Clone)]
pub struct RustParseError {
    pub error_type: &'static str, // "lexical" | "syntactic" | "semantic"
    pub message: String,
    pub line: u32,
    pub column: u32,
    pub context: String,
    pub recovery_action: String,
}
