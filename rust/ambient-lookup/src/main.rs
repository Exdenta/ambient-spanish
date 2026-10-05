//! ambient-lookup: read an English draft on stdin and print, for every word or
//! phrase that has a Spanish equivalent in the learner's vocabulary, the
//! candidates the writer may substitute.
//!
//! The vocabulary is `vocabulary.txt`, rewritten by `ambient_state.py context`:
//! `id | spanish | english` lines under `## kind (n)` headings. It is parsed on
//! every call (about 3,000 lines, well under a millisecond), so there is no
//! index file to keep fresh.

mod index;
mod scan;

use std::io::{Read, Write};
use std::path::PathBuf;
use std::process::ExitCode;

const USAGE: &str = "usage: ambient-lookup [--vocab PATH] [--file PATH] [--json] < draft.txt

Reads the draft from stdin (or --file) and prints the vocabulary matches.
The vocabulary defaults to $AMBIENT_SPANISH_VOCAB, then
~/.codex/state/ambient-spanish/vocabulary.txt.";

struct Args {
    vocab: PathBuf,
    file: Option<PathBuf>,
    json: bool,
}

fn parse_args() -> Result<Args, String> {
    let mut vocab: Option<PathBuf> = None;
    let mut file = None;
    let mut json = false;
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--vocab" => vocab = Some(args.next().ok_or("--vocab needs a path")?.into()),
            "--file" => file = Some(args.next().ok_or("--file needs a path")?.into()),
            "--json" => json = true,
            "-h" | "--help" => return Err(USAGE.to_string()),
            other => return Err(format!("unknown argument: {other}\n{USAGE}")),
        }
    }
    let vocab = vocab
        .or_else(|| std::env::var_os("AMBIENT_SPANISH_VOCAB").map(PathBuf::from))
        .or_else(|| {
            std::env::var_os("HOME").map(|home| {
                PathBuf::from(home).join(".codex/state/ambient-spanish/vocabulary.txt")
            })
        })
        .ok_or("cannot locate the vocabulary file; pass --vocab")?;
    Ok(Args { vocab, file, json })
}

fn run() -> Result<(), String> {
    let args = parse_args()?;
    let raw = std::fs::read_to_string(&args.vocab)
        .map_err(|e| format!("cannot read {}: {e}", args.vocab.display()))?;
    let index = index::Index::parse(&raw);
    if index.entries.is_empty() {
        eprintln!("warning: no vocabulary entries found in {}", args.vocab.display());
    }

    let bytes = match &args.file {
        Some(path) => std::fs::read(path)
            .map_err(|e| format!("cannot read {}: {e}", path.display()))?,
        None => {
            let mut buf = Vec::new();
            std::io::stdin()
                .read_to_end(&mut buf)
                .map_err(|e| format!("cannot read stdin: {e}"))?;
            buf
        }
    };
    let draft = String::from_utf8_lossy(&bytes).into_owned();

    let matches = scan::find_matches(&index, &draft);
    let out = if args.json {
        scan::render_json(&index, &matches)
    } else {
        scan::render_text(&index, &matches)
    };
    std::io::stdout()
        .write_all(out.as_bytes())
        .map_err(|e| format!("cannot write stdout: {e}"))
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(message) => {
            eprintln!("{message}");
            ExitCode::from(2)
        }
    }
}
