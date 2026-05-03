// Multiline text field transformation pipeline — direct port of textfield.py.
//
// Applies exclusively to ValueType::MultilineString tokens.
// Pipeline: split → prefix detection/removal → line unfolding → rejoin.

pub fn transform_multiline(raw: &str) -> String {
    let lines: Vec<&str> = raw.split('\n').collect();
    let (lines, folding) = apply_prefix(lines);
    let lines = if folding { apply_unfolding(lines) } else { lines };
    lines.join("\n")
}

fn apply_prefix(lines: Vec<&str>) -> (Vec<String>, bool) {
    if lines.is_empty() {
        return (lines.into_iter().map(str::to_owned).collect(), false);
    }

    let first = lines[0];
    let bs = match first.find('\\') {
        Some(i) => i,
        None => return (lines.into_iter().map(str::to_owned).collect(), false),
    };

    let candidate = &first[..bs];
    let remainder = &first[bs..];

    let (double, after) = if remainder.starts_with("\\\\") {
        (true, &remainder[2..])
    } else if remainder.starts_with('\\') {
        (false, &remainder[1..])
    } else {
        return (lines.into_iter().map(str::to_owned).collect(), false);
    };

    if !after.trim_matches(|c| c == ' ' || c == '\t').is_empty() {
        return (lines.into_iter().map(str::to_owned).collect(), false);
    }

    let mut stripped: Vec<String>;

    if !candidate.is_empty() {
        for line in &lines[1..] {
            if !line.starts_with(candidate) {
                return (lines.into_iter().map(str::to_owned).collect(), false);
            }
        }
        let p = candidate.len();
        stripped = lines.iter().map(|l| l[p..].to_owned()).collect();
    } else {
        stripped = lines.iter().map(|l| (*l).to_owned()).collect();
    }

    if double {
        // '\\' → remove one backslash; fold NOT triggered
        stripped[0] = stripped[0][1..].to_owned();
        (stripped, false)
    } else {
        // '\' → first line is the fold separator header; remove it
        (stripped[1..].to_vec(), true)
    }
}

fn apply_unfolding(lines: Vec<String>) -> Vec<String> {
    let mut result: Vec<String> = Vec::new();
    let mut pending = String::new();

    for line in &lines {
        let rstripped = line.trim_end_matches(|c| c == ' ' || c == '\t');
        if rstripped.ends_with('\\') {
            pending.push_str(&rstripped[..rstripped.len() - 1]);
        } else {
            result.push(pending.clone() + line.as_str());
            pending.clear();
        }
    }
    if !pending.is_empty() {
        result.push(pending);
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn no_prefix_no_fold() {
        assert_eq!(transform_multiline("hello\nworld"), "hello\nworld");
    }

    #[test]
    fn prefix_fold() {
        let input = "CIF>\\  \nCIF>line1\nCIF>line2";
        let result = transform_multiline(input);
        assert_eq!(result, "line1\nline2");
    }

    #[test]
    fn empty_raw_string() {
        assert_eq!(transform_multiline(""), "");
    }

    #[test]
    fn single_line_no_backslash() {
        assert_eq!(transform_multiline("hello"), "hello");
    }

    #[test]
    fn multiple_lines_no_transform() {
        // No backslash on first line → pass through unchanged
        assert_eq!(transform_multiline("line1\nline2\nline3"), "line1\nline2\nline3");
    }

    #[test]
    fn whitespace_only_lines_preserved() {
        assert_eq!(transform_multiline("   \n  \nhello"), "   \n  \nhello");
    }

    #[test]
    fn fold_only_no_prefix() {
        // First line is "\  " (just backslash + spaces) with no prefix candidate
        // → fold mode, first line removed, remaining lines unfolded
        let input = "\\  \npart1\\\npart2";
        let result = transform_multiline(input);
        assert_eq!(result, "part1part2");
    }

    #[test]
    fn fold_last_line_dangling_backslash() {
        // Last line ends with \ → the pending fragment is emitted without a newline
        let input = "\\  \npart1\\\npart2\\";
        let result = transform_multiline(input);
        assert_eq!(result, "part1part2");
    }

    #[test]
    fn prefix_double_backslash_no_fold() {
        // "P>\\\\" on first line = prefix "P>" + "\\\\" (two backslashes + spaces)
        // Fold mode is NOT triggered; one backslash is stripped from the first line,
        // leaving "\" (one backslash) plus the trailing spaces from the header.
        let input = "P>\\\\  \nP>line1\nP>line2";
        let result = transform_multiline(input);
        // After prefix "P>" stripped: first line = "\\  " (two backslashes + two spaces)
        // Remove one backslash: first line becomes "\  " (one backslash + two spaces)
        assert_eq!(result, "\\  \nline1\nline2");
    }

    #[test]
    fn prefix_not_on_all_lines_no_transform() {
        // Prefix "P>" only on first line; second line doesn't start with it
        // → no transform
        let input = "P>\\  \nnotprefixed";
        let result = transform_multiline(input);
        assert_eq!(result, input);
    }

    #[test]
    fn fold_joins_continuation_lines() {
        // Lines ending with \ are joined with the next line
        let input = "\\  \nfirst \\\nsecond \\\nthird";
        let result = transform_multiline(input);
        assert_eq!(result, "first second third");
    }

    #[test]
    fn fold_trailing_spaces_not_trimmed_before_backslash() {
        // Spaces immediately before the continuation \ are part of the content —
        // only trailing whitespace AFTER the backslash (i.e. on the rstripped line)
        // would be stripped, but the backslash is the final char so nothing is.
        let input = "\\  \nword  \\\nnext";
        let result = transform_multiline(input);
        assert_eq!(result, "word  next");
    }

    #[test]
    fn prefix_with_fold_combined() {
        // Prefix "P>" + fold: first line is header, remaining lines have prefix stripped
        // then fold applied
        let input = "P>\\  \nP>part1\\\nP>part2";
        let result = transform_multiline(input);
        assert_eq!(result, "part1part2");
    }

    #[test]
    fn no_backslash_on_first_line_pass_through() {
        let input = "no backslash here\nline2";
        assert_eq!(transform_multiline(input), input);
    }
}
