// Standalone CIF profiling binary — no Python, no PyO3 boundary.
//
// Build and run (must use --release; debug keeps dead PyO3 symbol refs):
//   cargo build --release --bin profile_cif
//   .\target\release\profile_cif.exe path\to\file.cif [--repeat N]
//
// Or for flamegraph profiling via samply:
//   samply record .\target\release\profile_cif.exe path\to\file.cif --repeat 20
//
// Output:
//   version detect : X ms
//   lexer          : X ms   (N tokens)
//   parser + IR    : X ms
//   ─────────────────
//   total Rust     : X ms

use cifflow_core::{
    lexer::Lexer,
    parser::Parser,
    raw_builder::RawBuilder,
    version::detect_version,
};
use std::time::{Duration, Instant};

fn fmt(d: Duration) -> String {
    let ms = d.as_secs_f64() * 1000.0;
    if ms < 1.0 { format!("{:.0} µs", ms * 1000.0) } else { format!("{:.2} ms", ms) }
}

fn run_once(source: &str) -> [Duration; 3] {
    let t0 = Instant::now();
    let vr = detect_version(source);
    let d_version = t0.elapsed();

    let t1 = Instant::now();
    let lexer  = Lexer::new(&vr.remaining, vr.version, vr.line_offset);
    let tokens = lexer.tokenise();
    let d_lex = t1.elapsed();

    let t2 = Instant::now();
    let mut builder = RawBuilder::new(vr.version, false);
    for e in &vr.errors { builder.push_error(e); }
    let mut parser = Parser::new();
    parser.parse(tokens, &mut builder);
    let _parsed = builder.finish();
    let d_parse = t2.elapsed();

    [d_version, d_lex, d_parse]
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let mut path: Option<String> = None;
    let mut repeat: usize = 1;
    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--repeat" => { i += 1; repeat = args.get(i).and_then(|s| s.parse().ok()).unwrap_or(1); }
            p          => { path = Some(p.to_string()); }
        }
        i += 1;
    }
    let path = path.unwrap_or_else(|| { eprintln!("Usage: profile_cif <file.cif> [--repeat N]"); std::process::exit(1); });

    let source = std::fs::read_to_string(&path)
        .unwrap_or_else(|e| { eprintln!("Cannot read {path}: {e}"); std::process::exit(1); });

    println!("File   : {} ({:.0} KB)", path, source.len() as f64 / 1024.0);
    println!("Repeat : {}", repeat);

    let mut all: Vec<[Duration; 3]> = Vec::with_capacity(repeat);
    for r in 0..repeat {
        let d = run_once(&source);
        let total = d[0] + d[1] + d[2];
        if repeat > 1 {
            println!("  run {:>2}: version={} lex={} parse+IR={} | total={}", r + 1, fmt(d[0]), fmt(d[1]), fmt(d[2]), fmt(total));
        }
        all.push(d);
    }

    all.sort_by_key(|d| d[0] + d[1] + d[2]);
    let med = &all[all.len() / 2];
    let total = med[0] + med[1] + med[2];

    println!();
    if repeat > 1 { println!("  (median of {} runs)", repeat); }
    println!("  version detect : {}", fmt(med[0]));
    println!("  lexer          : {}", fmt(med[1]));
    println!("  parser + IR    : {}", fmt(med[2]));
    println!("  ─────────────────────────────");
    println!("  total Rust     : {}", fmt(total));
}
