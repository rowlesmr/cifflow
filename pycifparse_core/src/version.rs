use crate::error::RustParseError;

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum CifVersion {
    Cif1_1,
    Cif2_0,
}

pub struct VersionResult {
    pub version: CifVersion,
    pub remaining: String,
    pub line_offset: u32,
    pub errors: Vec<RustParseError>,
}

// Check whether a stripped (no trailing \r\n) line is a CIF magic line.
// Magic format: optional BOM + "#\#CIF_" + version_token + optional whitespace
// Returns Some(version_str) if it matches, None otherwise.
fn check_magic(line: &str) -> Option<&str> {
    let line = line.strip_prefix('\u{FEFF}').unwrap_or(line);
    // The magic prefix is "#\#CIF_" — note the literal backslash between the hashes.
    // In Rust string literals, "\\" is a single backslash.
    let rest = line.strip_prefix("#\\#CIF_")?;
    // Version token: no whitespace, followed only by optional trailing whitespace.
    let version = rest.trim_end();
    if version.is_empty() || version.contains(char::is_whitespace) {
        return None;
    }
    Some(version)
}

// Port of Python version.detect_version().
// Normalises \r\n and \r to \n before splitting, then reassembles remaining
// with \n separators (the lexer does the same normalisation anyway).
pub fn detect_version(source: &str) -> VersionResult {
    let mut errors: Vec<RustParseError> = Vec::new();

    // Normalise line endings for splitting.
    let normalised = source.replace("\r\n", "\n").replace('\r', "\n");
    // Split keeping newlines so we can reconstruct `remaining` faithfully.
    // We collect as Vec<&str> of the normalised source.
    let lines: Vec<&str> = normalised.split('\n').collect();
    // `split('\n')` on "a\nb\n" gives ["a", "b", ""].
    // We need to reassemble with '\n' to restore newlines.

    for (i, raw_line) in lines.iter().enumerate() {
        // Strip BOM for whitespace-only check.
        let line = raw_line.strip_prefix('\u{FEFF}').unwrap_or(raw_line);

        // Skip whitespace-only lines.
        if line.trim().is_empty() {
            continue;
        }

        // Candidate line: try magic match.
        match check_magic(raw_line) {
            None => {
                // Not a magic line — leave it for normal processing.
                let remaining = lines[i..].join("\n");
                return VersionResult {
                    version: CifVersion::Cif1_1,
                    remaining,
                    line_offset: i as u32,
                    errors,
                };
            }
            Some(version_str) => {
                let remaining = lines[(i + 1)..].join("\n");
                let line_offset = (i + 1) as u32;
                let version = match version_str {
                    "2.0" => CifVersion::Cif2_0,
                    "1.1" => CifVersion::Cif1_1,
                    other => {
                        // Reconstruct the raw magic line for the error context.
                        let raw = format!("#\\#CIF_{other}");
                        errors.push(RustParseError {
                            error_type: "lexical",
                            message: format!("unrecognised CIF version: {raw}"),
                            line: (i + 1) as u32,
                            column: 1,
                            context: raw,
                            recovery_action: "defaulting to CIF 2.0".to_string(),
                        });
                        CifVersion::Cif2_0
                    }
                };
                return VersionResult { version, remaining, line_offset, errors };
            }
        }
    }

    // EOF before any non-whitespace content.
    VersionResult {
        version: CifVersion::Cif1_1,
        remaining: source.to_string(),
        line_offset: 0,
        errors,
    }
}
