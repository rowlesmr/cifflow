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
}
